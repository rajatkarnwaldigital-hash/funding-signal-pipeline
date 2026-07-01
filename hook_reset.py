"""
Clears hook, hook_status, hook_notes, and competitor_domain for all rows
where hook_status = GENERATED, so hook_generator.py will reprocess them.

Safe to re-run — only touches GENERATED rows, leaves everything else alone.
"""
import os
import logging
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from _utils import _a1, safe_cell

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HOOK_COLUMNS = ["hook", "hook_status", "hook_notes", "competitor_domain"]


def get_sheet():
    creds = Credentials.from_service_account_file(
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"], scopes=SCOPES
    )
    client = gspread.authorize(creds)
    return client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).sheet1


def main():
    sheet      = get_sheet()
    header     = sheet.row_values(1)
    col_map    = {col: idx + 1 for idx, col in enumerate(header) if col}
    all_values = sheet.get_all_values()

    hook_status_idx = col_map.get("hook_status", 0) - 1

    updates: list[dict] = []
    cleared = 0

    for i, row in enumerate(all_values[1:], start=2):
        if safe_cell(row, hook_status_idx) != "GENERATED":
            continue
        for col_name in HOOK_COLUMNS:
            if col_name in col_map:
                updates.append({
                    "range":  _a1(i, col_map[col_name]),
                    "values": [[""]],
                })
        cleared += 1

    if updates:
        sheet.batch_update(updates, value_input_option="RAW")
        log.info("Cleared %d GENERATED rows (%d cell updates).", cleared, len(updates))
    else:
        log.info("No GENERATED rows found — nothing to clear.")


if __name__ == "__main__":
    main()
