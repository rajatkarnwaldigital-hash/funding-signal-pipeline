"""
Re-fetches paid_traffic for all QUALIFIED rows using database=uk.
Existing rows were qualified with database=us, which returns 0 paid visits
for European companies. This patch overwrites paid_traffic in-place so
hook_generator can use the paid-to-organic angle.
"""
import os
import logging
import time
import requests
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from _utils import _a1, safe_cell, parse_int

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

SCOPES      = ["https://www.googleapis.com/auth/spreadsheets"]
SEMRUSH_URL = "https://api.semrush.com/"
DATABASE    = "uk"
DELAY       = 0.35


def get_sheet():
    creds = Credentials.from_service_account_file(
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"], scopes=SCOPES
    )
    client = gspread.authorize(creds)
    return client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).sheet1


def fetch_paid(domain: str, api_key: str) -> int:
    try:
        r = requests.get(SEMRUSH_URL, params={
            "type":           "domain_rank",
            "key":            api_key,
            "domain":         domain,
            "database":       DATABASE,
            "export_columns": "At",
        }, timeout=15)
        r.raise_for_status()
        text = r.text.strip()
        if not text or text.upper().startswith("ERROR"):
            return 0
        lines = text.splitlines()
        if len(lines) < 2:
            return 0
        data = dict(zip(lines[0].split(";"), lines[1].split(";")))
        return max(parse_int(data.get("At", 0)), 0)
    except Exception as exc:
        log.error("domain_rank(uk) failed [%s]: %s", domain, exc)
        return 0


def main():
    sheet   = get_sheet()
    header  = sheet.row_values(1)
    col_map = {col: idx + 1 for idx, col in enumerate(header) if col}

    all_values = sheet.get_all_values()
    if len(all_values) < 2:
        log.info("No data rows.")
        return

    api_key             = os.environ["SEMRUSH_API_KEY"]
    semrush_status_idx  = col_map.get("semrush_status", 0) - 1
    domain_idx          = col_map.get("domain", 0) - 1
    paid_traffic_idx_col = col_map.get("paid_traffic")

    if not paid_traffic_idx_col:
        log.error("paid_traffic column not found in sheet header.")
        return

    qualified = [
        (i, safe_cell(row, domain_idx))
        for i, row in enumerate(all_values[1:], start=2)
        if safe_cell(row, semrush_status_idx) == "QUALIFIED"
        and safe_cell(row, domain_idx)
    ]

    log.info("QUALIFIED rows to refresh: %d", len(qualified))
    updates: list[dict] = []
    refreshed = 0

    for row_num, domain in qualified:
        paid = fetch_paid(domain, api_key)
        log.info("Row %d: %s → paid=%d", row_num, domain, paid)
        updates.append({
            "range":  _a1(row_num, paid_traffic_idx_col),
            "values": [[str(paid)]],
        })
        refreshed += 1
        time.sleep(DELAY)

    if updates:
        sheet.batch_update(updates, value_input_option="RAW")
        log.info("Updated paid_traffic for %d rows.", refreshed)


if __name__ == "__main__":
    main()
