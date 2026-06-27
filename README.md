# Funding Signal Pipeline

## What this is

This pipeline automatically monitors funding news across 8 startup and tech publications, extracts the companies that have recently raised money, and checks whether those companies have a meaningful but not dominant organic search presence. Companies that pass the filter are flagged as qualified outreach targets — funded, in growth mode, and likely investing in SEO.

## Why we built it

A company that has just raised funding has allocated budget and is actively looking to grow. That is a materially different conversation than cold outreach to a company that may or may not have budget or urgency.

By filtering on funding signal first, every lead that reaches the outreach stage has already passed a budget qualifier. No human time is spent reviewing companies that cannot buy. The SEMrush filter then removes companies that are either too small to be worth the effort or too large and established to need our services, leaving only the window where SEO help has real leverage.

## How it works

1. Every 6 hours, the pipeline reads the latest articles from 8 funding and startup news sources
2. Each article is checked against the sheet — duplicates are skipped automatically
3. Claude reads each article title and extracts the name of the company that raised funding
4. Exa searches the web for that company's official website and returns the domain
5. The domain is checked against any domain already in the sheet to avoid processing the same company twice
6. SEMrush pulls organic traffic and authority score for each new domain
7. Domains that fall within the qualification range are marked `QUALIFIED` and are ready for outreach

## Pipeline architecture

| Script | What it does | Tools | Schedule |
|---|---|---|---|
| `rss_poller.py` | Polls 8 RSS feeds, deduplicates by URL, appends new articles to Google Sheet | feedparser, gspread | Every 6h at `:00` |
| `domain_resolver.py` | Extracts company name from article title via Claude, resolves to domain via Exa, deduplicates by domain | anthropic, exa-py, gspread | Every 6h at `:30` |
| `semrush_qualifier.py` | Fetches organic traffic, paid traffic, and authority score from SEMrush; applies qualification thresholds | SEMrush API, gspread | Every 6h at `:01` (offset +1h) |

All three workflows run on GitHub Actions. Each processes up to 50 rows per run to manage API costs.

## Qualification criteria

A domain is marked `QUALIFIED` only if **both** conditions are met:

- **Authority Score:** between 10 and 70
- **Organic traffic:** between 500 and 500,000 monthly visits

A domain outside either range is marked `DISQUALIFIED` with the specific reason written to `semrush_notes`. Domains where SEMrush returns no data are marked `NO_DATA`.

## Google Sheet columns

| Column | Set by | What it contains |
|---|---|---|
| `source` | rss_poller | Publication name (e.g. TechCrunch, Sifted) |
| `title` | rss_poller | Article headline |
| `url` | rss_poller | Link to the article |
| `published_date` | rss_poller | Article publish date (UTC) |
| `summary` | rss_poller | Article description, HTML stripped |
| `date_added` | rss_poller | Timestamp when the row was written |
| `company_name` | domain_resolver | Company name extracted from the article title |
| `domain` | domain_resolver | Resolved company domain (e.g. `stripe.com`) |
| `resolved` | domain_resolver | `YES` / `FAILED` / `UNKNOWN` / `DUPLICATE` |
| `resolver_notes` | domain_resolver | Reason when resolved ≠ YES |
| `organic_traffic` | semrush_qualifier | Monthly organic visits from SEMrush |
| `authority_score` | semrush_qualifier | Domain Authority Score from SEMrush |
| `paid_traffic` | semrush_qualifier | Monthly paid search traffic from SEMrush |
| `semrush_status` | semrush_qualifier | `QUALIFIED` / `DISQUALIFIED` / `NO_DATA` / `FAILED` |
| `semrush_notes` | semrush_qualifier | Disqualification reason or error detail |

## What is being built next

- **Hook generator** — uses SEMrush competitor gap data to write a personalised outreach hook for each qualified domain
- **Contact sourcer** — finds CEO and CMO email addresses via Apollo API, with Exa as fallback
- **Plusvibe loader** — pushes qualified leads with hooks into Plusvibe for sequenced outreach
- **Hiring signal tracker** — separate pipeline that monitors job boards for companies posting senior marketing or SEO roles

## Setup

### Local

```bash
git clone https://github.com/rajatkarnwaldigital-hash/funding-signal-pipeline.git
cd funding-signal-pipeline

cp .env.rss.example .env
# Edit .env and fill in all credentials

pip install -r requirements_rss.txt
pip install -r requirements_resolver.txt
pip install -r requirements_qualifier.txt

python rss_poller.py
python domain_resolver.py
python semrush_qualifier.py
```

### Google Sheet

Create a new Google Sheet and share it with your service account email address (Editor access). Copy the Sheet ID from the URL and add it to `.env`.

The scripts create all required columns automatically on first run — no manual sheet setup needed.

### GitHub Actions

Add the following secrets to the repo under `Settings → Secrets and variables → Actions`:

| Secret | What it is |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full JSON content of the service account key file |
| `GOOGLE_SHEET_ID` | ID from the Google Sheet URL |
| `EXA_API_KEY` | Exa API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SEMRUSH_API_KEY` | SEMrush API key |

Workflows run automatically on every push to `main` and on their cron schedules.
