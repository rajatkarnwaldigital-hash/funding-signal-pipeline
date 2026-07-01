import os
import logging
import time
import requests
import anthropic
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from _utils import _a1, is_blocked, safe_cell, parse_int

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

BATCH_SIZE = 50
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SEMRUSH_URL = "https://api.semrush.com/"
DATABASE    = "uk"
DELAY       = 0.35   # seconds between SEMrush calls

NEW_COLUMNS = ["hook", "hook_status", "hook_notes", "competitor_domain"]


# ---------------------------------------------------------------------------
# SEMrush API
# ---------------------------------------------------------------------------

def _parse_table(text: str) -> list[dict]:
    """Parse a multi-row SEMrush response into a list of dicts."""
    text = text.strip()
    if not text or text.upper().startswith("ERROR"):
        return []
    lines = text.splitlines()
    if len(lines) < 2:
        return []
    headers = lines[0].split(";")
    return [
        dict(zip(headers, line.split(";")))
        for line in lines[1:]
        if line.strip()
    ]


def semrush_organic_competitors(domain: str, api_key: str) -> list[dict]:
    """
    Returns list of competitor dicts with keys Dn (domain), Or (keywords), Ot (traffic).
    Sorted by relevance (SEMrush default). Empty list on failure or no data.
    """
    try:
        r = requests.get(SEMRUSH_URL, params={
            "type":           "domain_organic_organic",
            "key":            api_key,
            "domain":         domain,
            "database":       DATABASE,
            "export_columns": "Dn,Or,Ot",
            "display_limit":  20,
        }, timeout=15)
        r.raise_for_status()
        return _parse_table(r.text)
    except Exception as exc:
        log.error("domain_organic_organic failed [%s]: %s", domain, exc)
        return []


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
# Competitor selection
# ---------------------------------------------------------------------------

MIN_SHARED_KEYWORDS  = 10
MIN_COMPETITOR_TRAFFIC = 200


def pick_competitor(competitors: list[dict], target_traffic: int) -> tuple[str, int]:
    """
    Walk the SEMrush competitor list (relevance-sorted) and return the first
    unblocked domain with at least MIN_SHARED_KEYWORDS keyword overlap and
    MIN_COMPETITOR_TRAFFIC organic visits (avoids near-zero ghost competitors).

    domain_organic_organic returns full column names ('Domain', 'Organic Keywords',
    'Organic Traffic') rather than the short codes — check both for resilience.
    """
    for row in competitors:
        domain   = (row.get("Domain") or row.get("Dn") or "").strip().lower()
        traffic  = parse_int(row.get("Organic Traffic") or row.get("Ot") or 0)
        overlap  = parse_int(row.get("Organic Keywords") or row.get("Or") or 0)
        if not domain or is_blocked(domain):
            continue
        if overlap < MIN_SHARED_KEYWORDS:
            continue
        if traffic < MIN_COMPETITOR_TRAFFIC:
            continue
        return domain, traffic
    return "", -1


# ---------------------------------------------------------------------------
# Hook generation
# ---------------------------------------------------------------------------

HOOK_PROMPT = """\
Write a 2-sentence cold email opening. Pure observation only — no agency name, no solution offer, no call to action.

Target: {company_name} ({domain})
Their monthly organic visits: {target_traffic:,}
Their monthly paid search visits: {paid_traffic}
Competitor in their keyword space: {competitor_domain}
That competitor's monthly organic visits: {competitor_traffic:,}

The hook must always surface a gap or vulnerability for {company_name}, never praise them or imply they are winning. The observation should make the reader think "we have a problem here."

Choose the sharpest angle:
- If {company_name} has meaningful paid visits (paid_traffic > 500): note they are paying for search demand that {competitor_domain} captures for free organically — the paid spend is an ongoing cost for something a competitor earns automatically
- If paid visits are low or zero: note the specific keyword territory where {competitor_domain} is capturing visits that {company_name} is not ranking for — frame this as pipeline going elsewhere

Sentence 1: state the gap using actual numbers — name both companies, be specific about what {competitor_domain} captures that {company_name} is missing
Sentence 2: state the cost or consequence in plain terms — qualified search demand flows to a competitor instead of {company_name}'s site. No vague language like "potential losses" or "could mean." State it as a fact.

Rules — all mandatory:
- Do NOT mention funding, hiring, or any recent news — write as if found through search research only
- Do NOT name any agency, do not offer a fix, do not say "we" or "I can help"
- The character — (em dash) is FORBIDDEN. Do not use it. Use a comma or period instead.
- Do NOT invent or estimate dollar figures
- Do NOT mention SEMrush or any analytics tool
- No AI-speak or SEO jargon: "leverage", "landscape", "dive into", "delve", "game-changer", "unlock", "journey", "cutting-edge", "robust", "domain authority", "owned properties", "keyword territory", "search intent", "organic presence"
- Write "visits" not "clicks"
- Plain, direct English
- 2 sentences, hard limit — no exceptions
- Output only the hook text, nothing else\
"""


def generate_hook(
    company_name: str,
    domain: str,
    target_traffic: int,
    paid_traffic: int,
    competitor_domain: str,
    competitor_traffic: int,
    claude_client: anthropic.Anthropic,
) -> str:
    # Only surface paid traffic in the prompt when we have a real number.
    # Natural-language conditionals in the prompt are not reliably enforced by
    # the model — passing paid=0 causes fabricated paid-search claims.
    paid_str = f"{paid_traffic:,}" if paid_traffic >= 500 else "0 (not running paid search)"
    prompt = HOOK_PROMPT.format(
        company_name=company_name,
        domain=domain,
        target_traffic=target_traffic,
        paid_traffic=paid_str,
        competitor_domain=competitor_domain,
        competitor_traffic=competitor_traffic,
    )
    try:
        resp = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.error("Claude error for domain '%s': %s", domain, exc)
        return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sheet   = get_sheet()
    col_map = ensure_columns(sheet)
    semrush_key  = os.environ["SEMRUSH_API_KEY"]
    claude_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    log.info("Columns: %s", col_map)

    all_values = sheet.get_all_values()
    if len(all_values) < 2:
        log.info("Sheet has no data rows.")
        return

    semrush_status_idx  = col_map.get("semrush_status", 0) - 1
    hook_status_idx     = col_map.get("hook_status", 0) - 1
    domain_idx          = col_map.get("domain", 0) - 1
    company_name_idx    = col_map.get("company_name", 0) - 1
    organic_traffic_idx = col_map.get("organic_traffic", 0) - 1
    paid_traffic_idx    = col_map.get("paid_traffic", 0) - 1

    # Find rows where semrush_status=QUALIFIED and hook_status is empty
    unprocessed = []
    for i, row in enumerate(all_values[1:], start=2):
        if (safe_cell(row, semrush_status_idx) == "QUALIFIED" and
                not safe_cell(row, hook_status_idx)):
            domain       = safe_cell(row, domain_idx)
            company_name = safe_cell(row, company_name_idx)
            traffic      = parse_int(safe_cell(row, organic_traffic_idx))
            paid         = parse_int(safe_cell(row, paid_traffic_idx))
            if domain:
                unprocessed.append({
                    "sheet_row":    i,
                    "domain":       domain,
                    "company_name": company_name or domain,
                    "traffic":      traffic,
                    "paid_traffic": max(paid, 0),
                })

    log.info("Unprocessed rows: %d — processing up to %d", len(unprocessed), BATCH_SIZE)
    batch = unprocessed[:BATCH_SIZE]

    stats = {"generated": 0, "no_competitor_gap": 0, "failed": 0}
    updates: list[dict] = []

    def q(row: int, col_name: str, value):
        updates.append({"range": _a1(row, col_map[col_name]), "values": [[str(value)]]})

    for item in batch:
        row_num      = item["sheet_row"]
        domain         = item["domain"]
        company_name   = item["company_name"]
        target_traffic = item["traffic"]
        paid_traffic   = item["paid_traffic"]

        log.info("Row %d: %s (traffic=%s)", row_num, domain, target_traffic)

        try:
            competitors = semrush_organic_competitors(domain, semrush_key)
            time.sleep(DELAY)

            if not competitors:
                log.info("  → NO_COMPETITOR_GAP (SEMrush returned no competitor data)")
                stats["no_competitor_gap"] += 1
                q(row_num, "hook_status", "NO_COMPETITOR_GAP")
                q(row_num, "hook_notes", "SEMrush returned no competitor data")
                continue

            competitor_domain, competitor_traffic = pick_competitor(competitors, target_traffic)

            if not competitor_domain:
                log.info("  → NO_COMPETITOR_GAP (all %d competitors blocked or empty domain)",
                         len(competitors))
                stats["no_competitor_gap"] += 1
                q(row_num, "hook_status", "NO_COMPETITOR_GAP")
                q(row_num, "hook_notes", "No unblocked competitor returned by SEMrush")
                continue

            log.info(
                "  → Competitor: %s (traffic=%s, gap=%s)",
                competitor_domain,
                competitor_traffic,
                competitor_traffic - target_traffic,
            )

            hook = generate_hook(
                company_name=company_name,
                domain=domain,
                target_traffic=target_traffic,
                paid_traffic=paid_traffic,
                competitor_domain=competitor_domain,
                competitor_traffic=competitor_traffic,
                claude_client=claude_client,
            )

            if not hook:
                log.info("  → FAILED (Claude returned empty response)")
                stats["failed"] += 1
                q(row_num, "hook_status", "FAILED")
                q(row_num, "hook_notes", "Claude returned empty response")
                continue

            log.info("  → GENERATED")
            stats["generated"] += 1
            q(row_num, "hook", hook)
            q(row_num, "hook_status", "GENERATED")
            q(row_num, "competitor_domain", competitor_domain)

        except Exception as exc:
            log.error("Unexpected error on row %d (%s): %s", row_num, domain, exc)
            stats["failed"] += 1
            q(row_num, "hook_status", "FAILED")
            q(row_num, "hook_notes", str(exc)[:200])

    if updates:
        sheet.batch_update(updates, value_input_option="RAW")
        log.info("Wrote %d cell updates to sheet.", len(updates))

    log.info(
        "Run complete — generated: %d | no_competitor_gap: %d | failed: %d",
        stats["generated"], stats["no_competitor_gap"], stats["failed"],
    )


if __name__ == "__main__":
    main()
