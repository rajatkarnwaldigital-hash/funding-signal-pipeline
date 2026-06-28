import os
import logging
from datetime import datetime, timezone

import feedparser
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

FEEDS = [
    ("EU Startups",         "https://www.eu-startups.com/feed"),
    ("Sifted",              "https://sifted.eu/feed"),
    ("Tech.eu",             "https://tech.eu/feed"),
    ("TechCrunch",          "https://techcrunch.com/category/startups/feed"),
    ("TechCrunch Funding",  "https://techcrunch.com/tag/funding/feed"),
    ("Startup Daily",       "https://www.startupdaily.net/feed"),
    ("BetaKit",             "https://betakit.com/feed"),
    ("Business Wire",       "https://businesswire.com/rss/home/?rss=g22"),
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

SHEET_HEADER = ["source", "title", "url", "published_date", "summary", "date_added"]


def get_sheet():
    key_path = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    creds = Credentials.from_service_account_file(key_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(sheet_id).sheet1
    return sheet


def ensure_header(sheet):
    existing = sheet.row_values(1)
    # Check for column presence, not exact equality — other scripts append extra columns to row 1
    if not all(col in existing for col in SHEET_HEADER):
        sheet.insert_row(SHEET_HEADER, 1)
        log.info("Header row written.")


def fetch_existing_urls(sheet):
    try:
        url_col_index = SHEET_HEADER.index("url") + 1  # 1-based
        values = sheet.col_values(url_col_index)
        return set(values[1:])  # skip header
    except Exception as exc:
        log.warning("Could not fetch existing URLs: %s", exc)
        return set()


def parse_date(entry) -> str:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                dt = datetime(*t[:6], tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
    return ""


def clean_summary(entry) -> str:
    raw = getattr(entry, "summary", "") or ""
    # strip rudimentary HTML tags
    import re
    return re.sub(r"<[^>]+>", "", raw).strip()


def poll_feed(source_name: str, url: str) -> list[dict]:
    log.info("Polling %s → %s", source_name, url)
    try:
        parsed = feedparser.parse(url)
        if parsed.get("bozo") and not parsed.entries:
            raise ValueError(f"bozo exception: {parsed.bozo_exception}")
        articles = []
        for entry in parsed.entries:
            link = getattr(entry, "link", "") or ""
            if not link:
                continue
            articles.append({
                "source":         source_name,
                "title":          getattr(entry, "title", "").strip(),
                "url":            link.strip(),
                "published_date": parse_date(entry),
                "summary":        clean_summary(entry),
            })
        log.info("  → %d articles fetched", len(articles))
        return articles
    except Exception as exc:
        log.error("Feed failed [%s]: %s", source_name, exc)
        return []


def main():
    sheet = get_sheet()
    ensure_header(sheet)
    existing_urls = fetch_existing_urls(sheet)
    log.info("Existing URLs in sheet: %d", len(existing_urls))

    all_articles: list[dict] = []
    for source_name, url in FEEDS:
        all_articles.extend(poll_feed(source_name, url))

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    new_rows = []
    skipped = 0

    for article in all_articles:
        if article["url"] in existing_urls:
            skipped += 1
            continue
        existing_urls.add(article["url"])  # guard against duplicates within this run
        new_rows.append([
            article["source"],
            article["title"],
            article["url"],
            article["published_date"],
            article["summary"],
            now_str,
        ])

    if new_rows:
        sheet.append_rows(new_rows, value_input_option="RAW")
        log.info("Added %d new articles, skipped %d duplicates.", len(new_rows), skipped)
    else:
        log.info("No new articles. Skipped %d duplicates.", skipped)


if __name__ == "__main__":
    main()
