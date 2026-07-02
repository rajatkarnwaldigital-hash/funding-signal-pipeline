# Shared helpers used across pipeline scripts.
# No third-party dependencies — stdlib only.

BLOCKED_DOMAINS = {
    # News, media, directories
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
    # Confirmed category mismatches — wrong industry surfaced by SEMrush overlap
    "bgo.com",               # casino (matched software/app companies)
    "thelogic.co",           # Canadian tech news site
    "mdauk.org",             # Muscular Dystrophy Association UK (acronym collision with MDA Space)
    "blossomeducational.com",# educational toys (matched Blossom Social)
    "agilealliance.org",     # methodology nonprofit (matched OpenText)
    "readdle.com",           # productivity apps (matched Nuvei payment platform)
    "fusionxinvest.com",     # investment firm (matched General Fusion)
    "meridianstar.com",      # news site (matched Meridian nonprofit, name collision)
    "tastekenyauk.com",      # food/tea importer (matched Omnea procurement)
    "pons.com",              # language learning (matched Etched AI chips)
    "trendbible.com",        # fashion forecasting (matched Fluent language platform)
    "againstmalaria.com",    # charity (matched Peec AI)
    "dayiwasborn.co.uk",     # gift novelty site (matched Archimede watches)
    "preventioninstitute.org",# health nonprofit (matched Purpose)
    "north.tech",            # Faroe Islands app company (matched Cohere enterprise AI)
    "popchorus.co.uk",       # choir/music community (matched Finn car subscription via neilfinn.com resolver bug)
    "u2songs.com",           # U2 fan site (matched Bono company via u2.com resolver bug)
    "vandusengarden.org",    # botanical garden (matched Showpass ticketing)
    "rivianforums.com",      # EV fan forum (matched Slate Auto)
}


def _a1(row: int, col: int) -> str:
    """Convert 1-based row/col to A1 notation."""
    col_str = ""
    c = col
    while c > 0:
        c, rem = divmod(c - 1, 26)
        col_str = chr(65 + rem) + col_str
    return f"{col_str}{row}"


def safe_cell(row: list, idx: int) -> str:
    """Return row[idx] safely, empty string if out of bounds."""
    return row[idx].strip() if 0 <= idx < len(row) else ""


def parse_int(val) -> int:
    try:
        return int(str(val).replace(",", "").split(".")[0])
    except (ValueError, TypeError):
        return -1


BLOCKED_TLDS = {".gov", ".mil", ".edu"}


def is_blocked(domain: str) -> bool:
    if not domain or "." not in domain:
        return True
    for tld in BLOCKED_TLDS:
        if domain.endswith(tld):
            return True
    for blocked in BLOCKED_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True
    return False
