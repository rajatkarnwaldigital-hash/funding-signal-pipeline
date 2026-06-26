import os
import logging
import re
from urllib.parse import urlparse

import anthropic
import gspread
from exa_py import Exa
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

NEW_COLUMNS = ["company_name", "domain", "resolved", "resolver_notes"]

# Domains that are directories/news sites, not company homepages
BLOCKED_DOMAINS = {
    "wikipedia.org", "crunchbase.com", "linkedin.com", "techcrunch.com",
    "bloomberg.com", "forbes.com", "businesswire.com", "reuters.com",
    "wsj.com", "ft.com", "nytimes.com", "theguardian.com",
    "tech.eu", "sifted.eu", "eu-startups.com", "betakit.com",
    "startupdaily.net", "venturebeat.com", "wired.com", "theverge.com",
    "engadget.com", "zdnet.com", "cnet.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "youtube.com", "github.com",
    "medium.com", "substack.com", "angel.co", "angellist.com",
    "pitchbook.com", "cbinsights.com", "dealroom.co", "techeu.com",
    "prnewswire.com", "globenewswire.com", "accesswire.com",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _a1(row: int, col: int) -> str:
    """Convert 1-based row/col to A1 notation."""
    col_str = ""
    c = col
    while c > 0:
        c, rem = divmod(c - 1, 26)
        col_str = chr(65 + rem) + col_str
    return f"{col_str}{row}"


def extract_domain(url: str) -> str:
    try:
        if not url.startswith("http"):
            url = "https://" + url
        hostname = urlparse(url).netloc
        return re.sub(r"^www\.", "", hostname).lower()
    except Exception:
        return ""


def is_blocked(domain: str) -> bool:
    if not domain or "." not in domain:
        return True
    for blocked in BLOCKED_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True
    return False


def safe_cell(row: list, idx: int) -> str:
    """Return row[idx] safely, empty string if out of bounds."""
    return row[idx].strip() if idx >= 0 and idx < len(row) else ""


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
    """Add any missing resolver columns and return col_name → 1-based index map."""
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
# Claude: company name extraction
# ---------------------------------------------------------------------------

def extract_company_name(title: str, claude_client: anthropic.Anthropic) -> str:
    prompt = (
        "Extract the name of the startup or company that raised funding from this article title. "
        "Return only the company name, nothing else. "
        "If no company name is identifiable, return UNKNOWN. "
        f"Title: {title}"
    )
    try:
        resp = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        log.error("Claude error for title '%s': %s", title[:80], exc)
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# Exa: domain resolution
# ---------------------------------------------------------------------------

def resolve_domain(company_name: str, exa_client: Exa) -> tuple[str, str]:
    """Return (domain, notes). Domain is empty on failure."""
    try:
        results = exa_client.search(
            f"{company_name} official website",
            num_results=5,
            type="auto",
        )
        if not results.results:
            return "", "Exa returned no results"
        for result in results.results:
            domain = extract_domain(result.url)
            if domain and not is_blocked(domain):
                return domain, ""
        return "", "No valid domain in top Exa results (all blocked/news sites)"
    except Exception as exc:
        return "", f"Exa error: {exc}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sheet = get_sheet()
    col_map = ensure_columns(sheet)
    log.info("Columns: %s", col_map)

    all_values = sheet.get_all_values()
    if len(all_values) < 2:
        log.info("Sheet has no data rows.")
        return

    title_idx    = col_map.get("title", 0) - 1       # 0-based for list access
    resolved_idx = col_map.get("resolved", 0) - 1
    domain_idx   = col_map.get("domain", 0) - 1

    # Collect domains already in the sheet for dedup
    existing_domains: set[str] = set()
    for row in all_values[1:]:
        d = safe_cell(row, domain_idx)
        if d:
            existing_domains.add(d.lower())

    # Find unprocessed rows (resolved cell is empty)
    unprocessed = []
    for i, row in enumerate(all_values[1:], start=2):  # sheet rows are 1-based; row 1 is header
        if not safe_cell(row, resolved_idx):
            unprocessed.append({
                "sheet_row": i,
                "title": safe_cell(row, title_idx),
            })

    log.info("Unprocessed rows: %d — processing up to %d", len(unprocessed), BATCH_SIZE)
    batch = unprocessed[:BATCH_SIZE]

    claude_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    exa_client    = Exa(api_key=os.environ["EXA_API_KEY"])

    stats = {"resolved": 0, "failed": 0, "unknown": 0, "duplicate": 0}
    updates: list[dict] = []  # gspread batch_update payload

    def q(row: int, col_name: str, value: str):
        updates.append({"range": _a1(row, col_map[col_name]), "values": [[value]]})

    for item in batch:
        row_num = item["sheet_row"]
        title   = item["title"]
        log.info("Row %d: %.80s", row_num, title)

        # Step 1 — extract company name via Claude
        company = extract_company_name(title, claude_client)

        if company == "UNKNOWN":
            log.info("  → UNKNOWN (no company identifiable)")
            stats["unknown"] += 1
            q(row_num, "company_name", "UNKNOWN")
            q(row_num, "resolved", "UNKNOWN")
            continue

        log.info("  → Company: %s", company)
        q(row_num, "company_name", company)

        # Step 2 — resolve domain via Exa
        domain, notes = resolve_domain(company, exa_client)

        if not domain:
            log.info("  → FAILED: %s", notes)
            stats["failed"] += 1
            q(row_num, "resolved", "FAILED")
            q(row_num, "resolver_notes", notes)
            continue

        # Step 3 — dedup check
        if domain.lower() in existing_domains:
            log.info("  → DUPLICATE: %s", domain)
            stats["duplicate"] += 1
            q(row_num, "domain", domain)
            q(row_num, "resolved", "DUPLICATE")
            q(row_num, "resolver_notes", "Domain already exists in sheet")
            continue

        # Success
        log.info("  → Resolved: %s", domain)
        stats["resolved"] += 1
        existing_domains.add(domain.lower())  # prevent within-batch dupes
        q(row_num, "domain", domain)
        q(row_num, "resolved", "YES")

    if updates:
        sheet.batch_update(updates, value_input_option="RAW")
        log.info("Wrote %d cell updates to sheet.", len(updates))

    log.info(
        "Run complete — resolved: %d | failed: %d | unknown: %d | duplicate: %d",
        stats["resolved"], stats["failed"], stats["unknown"], stats["duplicate"],
    )


if __name__ == "__main__":
    main()
