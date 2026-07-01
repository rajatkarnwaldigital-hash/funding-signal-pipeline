"""
One-off patch (safe to re-run): two passes over QUALIFIED rows in the sheet.

Pass 1 — ceiling check (no API calls):
  Rows marked QUALIFIED before the upper-bound thresholds were added may have
  organic_traffic > 500,000 or authority_score > 70. Re-evaluate them using
  the values already written in the sheet and downgrade to DISQUALIFIED where
  they violate current ceilings. Also clears any hook fields on those rows.

Pass 2 — hook reprocess reset:
  Rows that are legitimately QUALIFIED but have hook_status = NO_COMPETITOR_GAP
  get those fields cleared so hook_generator.py picks them up again on the
  next run (with the updated pick_competitor logic).
"""
import os
import logging
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

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

TRAFFIC_MIN = 500
TRAFFIC_MAX = 500_000
AUTH_MIN    = 10
AUTH_MAX    = 70


def get_sheet():
    creds = Credentials.from_service_account_file(
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"], scopes=SCOPES
    )
    client = gspread.authorize(creds)
    return client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).sheet1


def main():
    sheet  = get_sheet()
    header = sheet.row_values(1)
    col_map = {col: idx + 1 for idx, col in enumerate(header) if col}

    all_values = sheet.get_all_values()
    if len(all_values) < 2:
        log.info("Sheet has no data rows.")
        return

    semrush_status_idx  = col_map.get("semrush_status", 0) - 1
    semrush_notes_idx   = col_map.get("semrush_notes", 0) - 1
    hook_status_idx     = col_map.get("hook_status", 0) - 1
    hook_notes_idx      = col_map.get("hook_notes", 0) - 1
    organic_traffic_idx = col_map.get("organic_traffic", 0) - 1
    authority_score_idx = col_map.get("authority_score", 0) - 1

    stats   = {"downgraded": 0, "cleared": 0, "already_ok": 0}
    updates: list[dict] = []

    def q(row: int, col_name: str, value: str):
        updates.append({"range": _a1(row, col_map[col_name]), "values": [[value]]})

    for i, row in enumerate(all_values[1:], start=2):
        if safe_cell(row, semrush_status_idx) != "QUALIFIED":
            continue

        traffic   = parse_int(safe_cell(row, organic_traffic_idx))
        authority = parse_int(safe_cell(row, authority_score_idx))

        traffic_ok   = traffic   == -1 or (TRAFFIC_MIN <= traffic   <= TRAFFIC_MAX)
        authority_ok = authority == -1 or (AUTH_MIN    <= authority <= AUTH_MAX)

        if not traffic_ok or not authority_ok:
            # Pass 1 — downgrade
            reasons = []
            if traffic > TRAFFIC_MAX:
                reasons.append(f"organic traffic too high ({traffic:,} > {TRAFFIC_MAX:,})")
            if traffic != -1 and traffic < TRAFFIC_MIN:
                reasons.append(f"organic traffic {traffic:,} < {TRAFFIC_MIN}")
            if authority > AUTH_MAX:
                reasons.append(f"authority score too high ({authority} > {AUTH_MAX})")
            if authority != -1 and authority < AUTH_MIN:
                reasons.append(f"authority score {authority} < {AUTH_MIN}")
            note = "; ".join(reasons)
            log.info("Row %d: DISQUALIFIED (%s)", i, note)
            stats["downgraded"] += 1
            q(i, "semrush_status", "DISQUALIFIED")
            q(i, "semrush_notes", note)
            # Clear any hook fields written before this row was downgraded
            if safe_cell(row, hook_status_idx):
                q(i, "hook_status", "")
                q(i, "hook_notes", "")

        elif safe_cell(row, hook_status_idx) == "NO_COMPETITOR_GAP":
            # Pass 2 — clear for reprocessing with updated pick_competitor logic
            log.info("Row %d: cleared NO_COMPETITOR_GAP for reprocessing", i)
            stats["cleared"] += 1
            q(i, "hook_status", "")
            q(i, "hook_notes", "")

        else:
            stats["already_ok"] += 1

    if updates:
        sheet.batch_update(updates, value_input_option="RAW")
        log.info("Wrote %d cell updates to sheet.", len(updates))
    else:
        log.info("No updates needed.")

    log.info(
        "Done — downgraded: %d | cleared for reprocess: %d | already ok: %d",
        stats["downgraded"], stats["cleared"], stats["already_ok"],
    )


if __name__ == "__main__":
    main()
