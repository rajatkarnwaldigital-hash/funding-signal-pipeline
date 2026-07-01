# Funding Signal Pipeline

## What this is

This pipeline automatically monitors funding news across startup and tech publications, extracts the companies that have recently raised money, qualifies them against a search-visibility filter, and generates a personalised cold email hook for each qualified lead. Companies that reach the hook stage are funded, in growth mode, and have a measurable gap that a competitor is already exploiting.

## Why we built it

A company that has just raised funding has allocated budget and is actively looking to grow. That is a materially different conversation than cold outreach to a company that may or may not have budget or urgency.

By filtering on funding signal first, every lead that reaches the outreach stage has already passed a budget qualifier. The SEMrush filter then removes companies that are either too small or too large to be worth targeting, leaving only the window where SEO help has real leverage. The hook generator then writes a 2-sentence observation grounded in real competitor gap data — no agency name, no pitch, pure signal.

## How it works

1. Every 6 hours, the pipeline reads the latest articles from 8 funding and startup news sources
2. Each article is checked against the sheet — duplicates are skipped automatically
3. Claude reads each article title and extracts the name of the company that raised funding
4. Exa searches the web for that company's official website and returns the domain
5. The domain is checked against any domain already in the sheet to avoid processing the same company twice
6. SEMrush (UK database) pulls organic traffic, paid traffic, and authority score for each new domain
7. Domains that fall within the qualification range are marked `QUALIFIED`
8. The hook generator fetches the top organic competitor for each qualified domain and writes a 2-sentence cold email opening grounded in the gap

## Pipeline architecture

### Core scripts (run automatically)

| Script | What it does | Tools | Schedule |
|---|---|---|---|
| `rss_poller.py` | Polls 8 RSS feeds, deduplicates by URL, appends new articles to Google Sheet | feedparser, gspread | Every 6h at `:00` |
| `domain_resolver.py` | Extracts company name from article title via Claude, resolves to domain via Exa, deduplicates by domain | anthropic, exa-py, gspread | Every 6h at `:30` |
| `semrush_qualifier.py` | Fetches organic traffic, paid traffic, and authority score from SEMrush (UK database); applies qualification thresholds | SEMrush API, gspread | Every 6h at `:01` (offset +1h) |
| `hook_generator.py` | Fetches organic competitors from SEMrush, picks best match, generates 2-sentence hook via Claude | SEMrush API, anthropic, gspread | Every 6h at `:30` (offset from qualifier) |
| `_utils.py` | Shared helpers: cell addressing, domain blocking, safe parsing | — | imported by all scripts |

### Maintenance scripts (manual-dispatch only)

| Script | Workflow | What it does |
|---|---|---|
| `semrush_requalify.py` | `semrush_requalify.yml` | Downgrades QUALIFIED rows that exceed ceilings; clears `NO_COMPETITOR_GAP` rows so hook generator reprocesses them |
| `hook_reset.py` | (called by `hook_regenerate.yml`) | Clears `hook`, `hook_status`, `hook_notes`, `competitor_domain` for all GENERATED rows |
| `paid_traffic_refresh.py` | (called by `hook_regenerate.yml`) | Re-fetches paid traffic from SEMrush UK database for all QUALIFIED rows |

### Full reset workflow

`hook_regenerate.yml` (manual dispatch) runs all maintenance steps in sequence:

1. `semrush_requalify.py` — fix any disqualification edge cases
2. `paid_traffic_refresh.py` — ensure paid traffic is current from UK database
3. `hook_reset.py` — clear all existing hooks
4. `hook_generator.py` — regenerate hooks fresh

Run this whenever you want to regenerate all hooks after a prompt change.

## Qualification criteria

A domain is marked `QUALIFIED` only if **both** conditions are met:

- **Authority Score:** between 10 and 70
- **Organic traffic:** between 500 and 500,000 monthly visits (UK database)

A domain outside either range is marked `DISQUALIFIED` with the specific reason written to `semrush_notes`.

## Hook generator rules

The generated hook is a 2-sentence cold email opening. Hard rules baked into the prompt:

- Pure observation only — no agency name, no solution offer, no call to action
- Never mention funding, hiring, or any recent news — written as if found through search research
- No em dashes, no AI-speak ("leverage", "landscape", "domain authority", etc.)
- No invented dollar figures
- No mention of SEMrush or any analytics tool
- Must surface a gap or vulnerability, never praise the target
- Paid-to-organic angle fires only when paid traffic ≥ 500 (avoids fabricated claims)

Competitor selection rules:

- Minimum 10 shared keywords
- Minimum 200 monthly visits (filters ghost competitors)
- Maximum 50× the target's traffic (filters incidental matches like academic databases)
- Blocked TLDs: `.gov`, `.mil`, `.edu`

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
| `organic_traffic` | semrush_qualifier | Monthly organic visits (UK database) |
| `authority_score` | semrush_qualifier | Domain Authority Score |
| `paid_traffic` | semrush_qualifier | Monthly paid search traffic (UK database) |
| `semrush_status` | semrush_qualifier | `QUALIFIED` / `DISQUALIFIED` / `NO_DATA` / `FAILED` |
| `semrush_notes` | semrush_qualifier | Disqualification reason or error detail |
| `hook` | hook_generator | 2-sentence cold email opening |
| `hook_status` | hook_generator | `GENERATED` / `NO_COMPETITOR_GAP` / `FAILED` |
| `hook_notes` | hook_generator | Reason when hook_status ≠ GENERATED |
| `competitor_domain` | hook_generator | Competitor domain used for the hook |

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
pip install -r requirements_hookgen.txt

python rss_poller.py
python domain_resolver.py
python semrush_qualifier.py
python hook_generator.py
```

### Google Sheet

Create a new Google Sheet and share it with your service account email address (Editor access). Copy the Sheet ID from the URL and add it to `.env`.

All scripts create required columns automatically on first run — no manual sheet setup needed.

### GitHub Actions

Add the following secrets under `Settings → Secrets and variables → Actions`:

| Secret | What it is |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full JSON content of the service account key file |
| `GOOGLE_SHEET_ID` | ID from the Google Sheet URL |
| `EXA_API_KEY` | Exa API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SEMRUSH_API_KEY` | SEMrush API key |

Workflows run on their cron schedules automatically. `hook_regenerate.yml` and `semrush_requalify.yml` are manual-dispatch only.
