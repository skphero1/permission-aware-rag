"""Ingest live web pages into Markdown for Project 8 (Permission-Aware RAG).

Uses Context.dev — Sajid has 11,000 credits on org "Home" (19 Nov 2026:
1,000 free + 10,000 from code YAHIAISINFINLANDONHISBIRTHDAY10K;
SPOOKY10K tried 19 Nov 2026 and was CODE_FULLY_REDEEMED, balance stayed
11,000). A standard scrape costs 1 credit per page, so a 20-page test
costs ~20 credits. No card is needed and none is used here.

Verified against the official Python SDK (PyPI context.dev 2.41.0,
checked 20 Nov 2026):
  from context.dev import ContextDev
  client = ContextDev()                       # reads CONTEXT_DEV_API_KEY
  page = client.web.scrape(url=..., formats={"markdown": True})
  page.markdown.data                          # clean Markdown

What this script does NOT do: it does not seed the Project 8 database.
Project 8 seeds from SEED_DOCS in app/seed.py (in-code documents with
allowed_roles). These Markdown files are the raw material for the next
step — chunk them the same heading-aware way (app/seed.py: chunk_text)
and add them as new SEED_DOCS entries with the correct allowed_roles
before embedding. Do not ingest a page and assume its role; set the
role per document, as Project 8 requires.

Usage (from the project folder, on your PC — key from your phone,
never pasted into chat):
  pip install context.dev
  set CONTEXT_DEV_API_KEY=<your key>        # PowerShell: $env:CONTEXT_DEV_API_KEY="..."
  python context_ingest.py https://sysfortech.com --max-pages 5
  python context_ingest.py --urls urls.txt --max-pages 20

Output: ingest/<slug>.md files, each headed with its source URL and
the date fetched, so every chunk can be traced back to its page.
Default test site is sysfortech.com (your own site) when no URL is
given — 1 page, 1 credit — so the first run is a cheap smoke test.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_URL = "https://sysfortech.com"
OUT_DIR = Path(__file__).resolve().parent / "ingest"


def slug_for(url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "")
    path = urlparse(url).path.strip("/").replace("/", "-") or "home"
    slug = re.sub(r"[^a-z0-9]+", "-", f"{host}-{path}".lower()).strip("-")
    return slug[:120] or "page"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="*", help="URLs to scrape (default: sysfortech.com)")
    parser.add_argument("--urls", dest="url_file", help="Text file, one URL per line")
    parser.add_argument("--max-pages", type=int, default=20,
                        help="Hard cap on pages scraped (default 20 = ~20 credits)")
    parser.add_argument("--out", default=str(OUT_DIR), help="Output folder")
    args = parser.parse_args()

    urls: list[str] = list(args.urls)
    if args.url_file:
        urls += [line.strip() for line in Path(args.url_file).read_text().splitlines()
                 if line.strip() and not line.strip().startswith("#")]
    if not urls:
        urls = [DEFAULT_URL]
    urls = urls[: args.max_pages]

    if not os.environ.get("CONTEXT_DEV_API_KEY"):
        print("Set CONTEXT_DEV_API_KEY first (from your phone, not from chat).")
        print("Get the key at context.dev dashboard > API keys. No card needed.")
        return 2

    try:
        from context.dev import ContextDev
    except ImportError:
        print("Install the SDK first: pip install context.dev")
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = ContextDev()
    fetched = 0
    for url in urls:
        try:
            page = client.web.scrape(url=url, formats={"markdown": True})
            markdown = (page.markdown.data or "").strip()
        except Exception as exc:  # noqa: BLE001 - report and move on
            print(f"FAILED {url}: {exc}")
            continue
        if not markdown:
            print(f"EMPTY  {url} (0 credits? check dashboard — no Markdown returned)")
            continue
        target = out_dir / f"{slug_for(url)}.md"
        target.write_text(
            f"<!-- source: {url} | fetched: {date.today().isoformat()} "
            f"via Context.dev (1 credit) -->\n\n{markdown}\n",
            encoding="utf-8",
        )
        fetched += 1
        print(f"OK     {url} -> {target.name} ({len(markdown)} chars)")
    print(f"\nDone: {fetched}/{len(urls)} pages saved to {out_dir} "
          f"(~{fetched} credits of your 11,000).")
    print("Next: pick a file, set its allowed_roles, add it to app/seed.py "
          "as a SEED_DOCS entry, then re-seed and re-run the permission tests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
