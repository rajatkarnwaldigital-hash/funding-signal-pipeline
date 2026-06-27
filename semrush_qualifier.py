import os
import logging
import time
import requests
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

BATCH_SIZE = 50
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SEMRUSH_URL   = "https://api.semrush.com/"
ANALYTICS_URL = "https://api.semrush.com/analytics/v1/"
DATABASE = "us"
DELAY    = 0.35   # seconds between API calls — stay well under rate limits

TRAFFIC_MIN = 500
TRAFFIC_MAX = 500_000
AUTH_MIN    = 10
AUTH_MAX    = 70

NEW_COLUMNS = [
    "organic_traffic", "authority_score", "paid_traffic",
    "semrush_status", "semrush_notes",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _a1(row: int, col: int) -> str:
    col_str = ""
    c = col
    while c > 0:
        c, rem = divmod(c - 1, 26)
        col_str = chr(65 + rem) + col_str
    return f"{col_str}{row}"


def safe_cell(row: list, idx: int) -> str:
    return row[idx].strip() if 0 <= idx < len(row) else ""


def parse_int(val) -> int:
    try:
        return int(str(val).replace(",", "").split(".")[0])
    except (ValueError, TypeError):
        return -1


# ---------------------------------------------------------------------------
# SEMrush API
# ---------------------------------------------------------------------------

def _parse(text: str) -> dict | None:
    text = text.strip()
    if not text or text.upper().startswith("ERROR"):
        return None
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    return dict(zip(lines[0].split(";"), lines[1].split(";")))


def semrush_domain_rank(domain: str, api_key: str) -> dict | None:
    """Returns dict with Ot (organic traffic) and At (paid traffic), or None on failure."""
    try:
        r = requests.get(SEMRUSH_URL, params={
            "type":           "domain_rank",
            "key":            api_key,
            "domain":         domain,
            "database":       DATABASE,
            "export_columns": "Ot,At",
        }, timeout=15)
        r.raise_for_status()
        return _parse(r.text)
    except Exception as exc:
        log.error("domain_rank failed [%s]: %s", domain, exc)
        return None


def semrush_authority_score(domain: str, api_key: str) -> int:
    """Returns authority score integer, or -1 on failure / no data."""
    try:
        r = requests.get(ANALYTICS_URL, params={
            "type":           "backlinks_overview",
            "key":            api_key,
            "target":         domain,
            "target_type":    "root_domain",
            "export_columns": "ascore",
        }, timeout=15)
        r.raise_for_status()
        data = _parse(r.text)
        if data is None:
            return -1
        return parse_int(data.get("ascore", -1))
    except Exception as exc:
        log.error("backlinks_overview failed [%s]: %s", domain, exc)
        return -1


# ---------------------------------------------------------------------------
# Sheet setup
# ---------------------------------------------------------------------------

def get_sheet():
    creds = Credentials.from_service_account_file(
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"], scopes=SCOPES
    )
    client = gspread.authorize(creds)
    return client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).sheet1


def ensure_columns(sheet) -> dict:
    header = sheet.row_values(1)
    missing = [c for c in NEW_COLUMNS if c not in header]
    if missing:
        start = len(header) + 1
        updates = [{"range": _a1(1, start + i), "values": [[col]]} for i, col in enumerate(missing)]
        sheet.batch_update(updates, value_input_option="RAW")
        header = sheet.row_values(1)
        log.info("Added columns: %s", missing)
    return {col: idx + 1 for idx, col in enumerate(header) if col}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sheet    = get_sheet()
    col_map  = ensure_columns(sheet)
    api_key  = os.environ["SEMRUSH_API_KEY"]

    log.info("Columns: %s", col_map)

    all_values = sheet.get_all_values()
    if len(all_values) < 2:
        log.info("Sheet has no data rows.")
        return

    resolved_idx      = col_map.get("resolved", 0) - 1
    semrush_status_idx = col_map.get("semrush_status", 0) - 1
    domain_idx        = col_map.get("domain", 0) - 1

    # Find rows that are resolved=YES and semrush_status is empty
    unprocessed = []
    for i, row in enumerate(all_values[1:], start=2):
        if (safe_cell(row, resolved_idx) == "YES" and
                not safe_cell(row, semrush_status_idx)):
            domain = safe_cell(row, domain_idx)
            if domain:
                unprocessed.append({"sheet_row": i, "domain": domain})

    log.info("Unprocessed rows: %d — processing up to %d", len(unprocessed), BATCH_SIZE)
    batch = unprocessed[:BATCH_SIZE]

    stats = {"qualified": 0, "disqualified": 0, "no_data": 0, "failed": 0}
    updates: list[dict] = []

    def q(row: int, col_name: str, value):
        updates.append({"range": _a1(row, col_map[col_name]), "values": [[str(value)]]})

    for item in batch:
        row_num = item["sheet_row"]
        domain  = item["domain"]
        log.info("Row %d: %s", row_num, domain)

        try:
            # Call 1 — domain rank (organic + paid traffic)
            rank_data = semrush_domain_rank(domain, api_key)
            time.sleep(DELAY)

            if rank_data is None:
                log.info("  → NO_DATA (domain_rank returned nothing)")
                stats["no_data"] += 1
                q(row_num, "semrush_status", "NO_DATA")
                q(row_num, "semrush_notes", "domain_rank returned no data")
                continue

            organic_traffic = parse_int(rank_data.get("Ot", rank_data.get("Organic Traffic", -1)))
            paid_traffic    = parse_int(rank_data.get("At", rank_data.get("Paid Traffic", -1)))

            # Call 2 — authority score
            authority = semrush_authority_score(domain, api_key)
            time.sleep(DELAY)

            log.info(
                "  → organic=%s | authority=%s | paid=%s",
                organic_traffic, authority, paid_traffic,
            )

            # Write metrics regardless of qualification outcome
            if organic_traffic >= 0:
                q(row_num, "organic_traffic", organic_traffic)
            if paid_traffic >= 0:
                q(row_num, "paid_traffic", paid_traffic)
            if authority >= 0:
                q(row_num, "authority_score", authority)

            # Qualification
            traffic_ok   = TRAFFIC_MIN <= organic_traffic <= TRAFFIC_MAX
            authority_ok = AUTH_MIN <= authority <= AUTH_MAX

            if organic_traffic == -1 and authority == -1:
                log.info("  → NO_DATA (both metrics missing)")
                stats["no_data"] += 1
                q(row_num, "semrush_status", "NO_DATA")
                q(row_num, "semrush_notes", "No organic traffic or authority score data")
            elif traffic_ok and authority_ok:
                log.info("  → QUALIFIED")
                stats["qualified"] += 1
                q(row_num, "semrush_status", "QUALIFIED")
            else:
                reasons = []
                if organic_traffic != -1 and organic_traffic < TRAFFIC_MIN:
                    reasons.append(f"organic traffic {organic_traffic} < {TRAFFIC_MIN}")
                if organic_traffic > TRAFFIC_MAX:
                    reasons.append(f"organic traffic too high ({organic_traffic:,} > {TRAFFIC_MAX:,})")
                if authority != -1 and authority < AUTH_MIN:
                    reasons.append(f"authority score {authority} < {AUTH_MIN}")
                if authority > AUTH_MAX:
                    reasons.append(f"authority score too high ({authority} > {AUTH_MAX})")
                note = "; ".join(reasons)
                log.info("  → DISQUALIFIED: %s", note)
                stats["disqualified"] += 1
                q(row_num, "semrush_status", "DISQUALIFIED")
                q(row_num, "semrush_notes", note)

        except Exception as exc:
            log.error("Unexpected error on row %d (%s): %s", row_num, domain, exc)
            stats["failed"] += 1
            q(row_num, "semrush_status", "FAILED")
            q(row_num, "semrush_notes", str(exc)[:200])

    if updates:
        sheet.batch_update(updates, value_input_option="RAW")
        log.info("Wrote %d cell updates to sheet.", len(updates))

    log.info(
        "Run complete — qualified: %d | disqualified: %d | no_data: %d | failed: %d",
        stats["qualified"], stats["disqualified"], stats["no_data"], stats["failed"],
    )


if __name__ == "__main__":
    main()
