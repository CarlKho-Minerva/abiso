"""Capture news items from the official city website, https://lapulapucity.gov.ph/.

    uv run python -m collect.website [--no-media]

Source-quality notes (checked 2026-10-03):
- Items are copies of the city's own Facebook posts, so the text is official.
- The site is stale: the newest items are from 2023 (previous administration).
- /postview/<n> pages carry no date at all; /articles/<n> pages carry a date with no time.
  Neither is turned into posted_at (no invented times); the shown date stays in the text.
- mod_security rejects requests without browser-like Accept headers (HTTP 406).
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import datetime

from abiso.schema import Media, Post
from collect.media import download
from collect.sites import fetch, now_ph, pause
from collect.sites import text_of as _text
from collect.store import POSTS, append_log, append_posts

BASE = "https://lapulapucity.gov.ph"
PAGE = "lapulapucity.gov.ph"


def item_links(index_html: str) -> list[str]:
    """postview/<n> and articles/<n> paths linked from an index page, in a stable order."""
    found = set(re.findall(r'href="(?:https://lapulapucity\.gov\.ph/)?((?:postview|articles)/\d+)"', index_html))
    return sorted(found, key=lambda p: (p.split("/")[0], int(p.split("/")[1])))


def _main_block(page_html: str) -> str:
    start = page_html.find('<div class="pages-banner">')
    end = page_html.find("<footer", start)
    return page_html[start:end] if start != -1 else ""


def parse_item(path: str, page_html: str) -> dict | None:
    """Extract title, body, shown date and images from a postview/ or articles/ page."""
    block = _main_block(page_html)
    if not block:
        return None
    if path.startswith("postview/"):
        m = re.search(r'<span class="display-5[^"]*">(.*?)</span>', block, re.DOTALL)
        title = _text(m.group(1)) if m else ""
        b = re.search(r'<div class="col-md-8">(.*)', block, re.DOTALL)
        body = _text(b.group(1)) if b else ""
        shown_date = None
    else:
        m = re.search(r'<span class="h1">(.*?)</span>', block, re.DOTALL)
        title = _text(m.group(1)) if m else ""
        d = re.search(r'fa-calendar"></i>\s*([^<]+?)\s*</p>', block)
        shown_date = d.group(1).strip() if d else None
        b = re.search(r'<span class="text-justify">(.*)', block, re.DOTALL)
        body = _text(b.group(1)) if b else ""
    imgs = [(src, html.unescape(alt)) for src, alt in
            re.findall(r'<img[^>]*src="(https://lapulapucity\.gov\.ph/storage/[^"]+)"[^>]*alt="([^"]*)"', block)]
    if body.startswith(title):
        body = body[len(title):].strip()
    return {"title": title, "body": body, "shown_date": shown_date, "images": imgs}


def to_post(path: str, item: dict, captured_at: datetime) -> Post:
    parts = [item["title"], item["body"]]
    if item["shown_date"]:
        parts.append(f"[date as shown on the site, no time given: {item['shown_date']}]")
    # The site's alt text is a template ("Image Posted for Mayor's Trophies") on most items: kept as-is.
    media = [Media(kind="image", url=src, alt=alt or None) for src, alt in item["images"]]
    return Post(
        post_id=f"llcgov:{path.replace('/', ':')}",
        page=PAGE,
        url=f"{BASE}/{path}",
        posted_at=None,
        captured_at=captured_at,
        text="\n\n".join(p for p in parts if p),
        media=media,
        capture_method="website",
    )


def capture(log=print) -> list[Post]:
    paths: list[str] = []
    for index in ("/", "/news"):
        for p in item_links(fetch(BASE + index)):
            if p not in paths:
                paths.append(p)
        pause()
    log(f"{len(paths)} item links: {paths}")
    posts = []
    for p in paths:
        try:
            item = parse_item(p, fetch(f"{BASE}/{p}"))
        except Exception as e:
            log(f"WARN {p}: {e}")
            item = None
        if item and (item["title"] or item["body"]):
            posts.append(to_post(p, item, now_ph()))
        else:
            log(f"WARN {p}: no content parsed")
        pause()
    return posts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-media", action="store_true")
    args = ap.parse_args(argv)
    posts = capture()
    if not posts:
        print("ERROR: captured nothing from the city website", file=sys.stderr)
        return 1
    if not args.no_media:
        for p in posts:
            for i, m in enumerate(p.media):
                m.path = download(m.url, f"web_{p.post_id.replace(':', '_')}_{i}")
    added, skipped = append_posts(posts)
    append_log({"page": PAGE, "posts": len(posts), "added": added, "skipped": skipped, "stop": "done"})
    print(f"{PAGE}: {len(posts)} items -> {POSTS}: +{added} new, {skipped} already stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
