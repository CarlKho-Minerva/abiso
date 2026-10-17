"""Capture official notices from issuer websites (no Facebook, no login, human pace).

    uv run python -m collect.sites meco mcwd deped pagasa [--per-source 60] [--no-media]

Every issuer and how each was confirmed official: collect/SOURCES.md.
Every datetime is Asia/Manila (+08:00). A date shown without a time never becomes posted_at;
it stays in the text as "[date as shown ...]" so nothing is invented.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import http.cookiejar
import json
import random
import re
import sys
import time
import urllib.request
from datetime import UTC, datetime

from abiso.schema import Media, Post
from collect.media import UA, download
from collect.store import POSTS, append_log, append_posts
from collect.timeparse import PH

HTML_HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"}
JSON_HEADERS = {"User-Agent": UA, "Accept": "application/json"}


def _opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def fetch(url: str, headers=HTML_HEADERS, opener=None) -> str:
    req = urllib.request.Request(url, headers=headers)
    with (opener or _opener()).open(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def pause() -> None:
    time.sleep(random.uniform(1.5, 4.0))


def now_ph() -> datetime:
    return datetime.now(UTC).astimezone(PH)


def text_of(fragment: str) -> str:
    s = re.sub(r"<(script|style)\b.*?</\1>", "", fragment, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", s, flags=re.IGNORECASE)
    s = html.unescape(re.sub(r"<[^>]+>", "", s)).replace(" ", " ")
    lines = [" ".join(line.split()) for line in s.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def wp_gmt(value: str) -> datetime:
    """WordPress date_gmt ('2026-10-01T23:56:38', no offset) is UTC by definition."""
    return datetime.fromisoformat(value).replace(tzinfo=UTC).astimezone(PH)


# --- MECO: daily "OUTAGE NOTICES" posts on mecomactan.com (WordPress) ----------------------

MECO_API = "https://mecomactan.com/wp-json/wp/v2/posts?per_page={n}&_fields=id,date_gmt,link,title,content"


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return html.unescape(m.group(1)) if m else None


def meco_posts(items: list[dict], captured_at: datetime) -> list[Post]:
    out = []
    for p in items:
        content = p["content"]["rendered"]
        imgs = [(_attr(tag, "src"), _attr(tag, "alt")) for tag in re.findall(r"<img\b[^>]*>", content)]
        imgs = [(src, alt) for src, alt in imgs if src and "/wp-content/uploads/" in src]
        title = text_of(p["title"]["rendered"])
        body = text_of(content)
        text = title + (f"\n\n{body}" if body else "")
        if imgs and not body:
            text += "\n\n[the notices are in the attached images]"
        out.append(Post(
            post_id=f"meco:wp:{p['id']}", page="mecomactan.com", url=p["link"],
            posted_at=wp_gmt(p["date_gmt"]), captured_at=captured_at, text=text,
            media=[Media(kind="image", url=src, alt=alt or None) for src, alt in imgs],
            capture_method="website",
        ))
    return out


def capture_meco(n: int) -> tuple[list[Post], dict]:
    items = json.loads(fetch(MECO_API.format(n=min(n, 100)), JSON_HEADERS))
    posts = meco_posts(items[:n], now_ph())
    return posts, {"url": MECO_API.format(n=n)}


# --- MCWD: "Water Service Interruptions" cards on mcwd.gov.ph (Oracle APEX) ----------------

MCWD_HOME = "https://www.mcwd.gov.ph/"
MCWD_LIST = "https://www.mcwd.gov.ph/ords/production/r/mcwd-website/news?p55_news_selected=interruption&clear=55"
# Lapu-Lapu shares MCWD supply with Cordova (Vivant-Cordova plant). Names alone are unsafe:
# Cebu City has "Basak San Nicolas" and "Babag", Mandaue has "Basak" and "Looc".
LAPU_RE = re.compile(r"lapu|mactan|cordova", re.IGNORECASE)

_CARD = re.compile(
    r'<h3 class="t-Card-title">(.*?)</h3>.*?<div class="t-Card-desc">(.*?)</div></div>\s*'
    r'<div class="t-Card-info">(.*?)</div>', re.DOTALL)


def mcwd_cards(page_html: str) -> list[dict]:
    """Cards in page order (newest first): category, text, date as shown."""
    region = page_html.split('id="water_interruption_id"', 1)[-1]
    return [{"category": text_of(c), "text": text_of(d), "date": text_of(i)} for c, d, i in _CARD.findall(region)]


def mcwd_select(cards: list[dict], n: int, lapu_share: float = 0.75) -> list[dict]:
    """Newest Lapu-Lapu/Mactan/Cordova cards first (the Basak household's supply), then the newest
    others as location negatives, so the resolver is also tested on "Basak San Nicolas" (Cebu City)."""
    lapu = [c for c in cards if LAPU_RE.search(c["text"])]
    other = [c for c in cards if not LAPU_RE.search(c["text"])]
    k = min(len(lapu), round(n * lapu_share))
    chosen = lapu[:k] + other[: n - k]
    order = {id(c): i for i, c in enumerate(cards)}
    return sorted(chosen, key=lambda c: order[id(c)])


def mcwd_posts(cards: list[dict], captured_at: datetime) -> list[Post]:
    out = []
    for c in cards:
        key = hashlib.sha1(f"{c['date']}|{c['text']}".encode()).hexdigest()[:16]
        text = (f"{c['category']}\n" if c["category"] else "") + c["text"]
        text += f"\n[date as shown on the site, no time given: {c['date']}]"
        out.append(Post(post_id=f"mcwd:{key}", page="mcwd.gov.ph", url=MCWD_LIST, posted_at=None,
                        captured_at=captured_at, text=text, capture_method="website"))
    return out


def capture_mcwd(n: int) -> tuple[list[Post], dict]:
    opener = _opener()
    fetch(MCWD_HOME, opener=opener)  # APEX needs its session cookie first
    pause()
    cards = mcwd_cards(fetch(MCWD_LIST, opener=opener))
    chosen = mcwd_select(cards, n)
    stats = {"url": MCWD_LIST, "cards_listed": len(cards),
             "cards_lapu": sum(bool(LAPU_RE.search(c["text"])) for c in cards),
             "listed_range": [cards[-1]["date"], cards[0]["date"]] if cards else None,
             "chosen_lapu": sum(bool(LAPU_RE.search(c["text"])) for c in chosen)}
    return mcwd_posts(chosen, now_ph()), stats


# --- DepEd Lapu-Lapu City Division: issuances on depedlapulapu.net.ph (WordPress Download Manager)

DEPED_API = "https://depedlapulapu.net.ph/wp-json/wp/v2/wpdmpro?per_page={n}&_fields=id,date_gmt,link,title"


def deped_posts(items: list[dict], captured_at: datetime) -> list[Post]:
    return [Post(post_id=f"deped:wpdm:{p['id']}", page="depedlapulapu.net.ph", url=p["link"],
                 posted_at=wp_gmt(p["date_gmt"]), captured_at=captured_at,
                 text=text_of(p["title"]["rendered"]) + "\n[title of a memorandum/advisory; the document itself is a download]",
                 capture_method="website")
            for p in items]


def capture_deped(n: int) -> tuple[list[Post], dict]:
    items = json.loads(fetch(DEPED_API.format(n=min(n, 100)), JSON_HEADERS))
    return deped_posts(items[:n], now_ph()), {"url": DEPED_API.format(n=n)}


# --- PAGASA Visayas PRSD: current warnings on pagasa.dost.gov.ph (no archive) -------------------

PAGASA_URL = "https://www.pagasa.dost.gov.ph/regional-forecast/visprsd"
_ISSUED = re.compile(r"Issued at (\d{1,2}:\d{2} [AP]M) (\d{1,2} \w+ \d{4})", re.IGNORECASE)


def pagasa_items(page_html: str) -> list[dict]:
    """Issued warnings in the rainfall and thunderstorm tabs. 'There is no ... Warning' is not a notice."""
    out = []
    for tab in ("rainfalls", "thunderstorms"):
        start = page_html.find(f'<div id="{tab}" class="tab-pane')
        if start == -1:
            continue
        end = page_html.find('<div id="', start + 10)  # the next tab pane
        pane = page_html[start:end if end != -1 else None]
        for block in re.findall(r"<div>(.*?)</div>", pane, re.DOTALL):
            text = text_of(block)
            if not text or re.match(r"As of today, there is no", text, re.IGNORECASE):
                continue
            iss = _ISSUED.search(text)
            issued = (datetime.strptime(f"{iss.group(2)} {iss.group(1)}", "%d %B %Y %I:%M %p").replace(tzinfo=PH)
                      if iss else None)
            out.append({"tab": tab, "text": text, "issued": issued})
    return out


def pagasa_posts(items: list[dict], captured_at: datetime) -> list[Post]:
    out = []
    for it in items:
        first = it["text"].splitlines()[0]
        key = hashlib.sha1(f"{first}|{it['issued']}".encode()).hexdigest()[:16]
        out.append(Post(post_id=f"pagasa:visprsd:{key}", page="pagasa.dost.gov.ph", url=PAGASA_URL,
                        posted_at=it["issued"], captured_at=captured_at, text=it["text"],
                        capture_method="website"))
    return out


def capture_pagasa(n: int) -> tuple[list[Post], dict]:
    items = pagasa_items(fetch(PAGASA_URL))
    return pagasa_posts(items[:n], now_ph()), {"url": PAGASA_URL, "items_current": len(items)}


SOURCES = {"meco": capture_meco, "mcwd": capture_mcwd, "deped": capture_deped, "pagasa": capture_pagasa}
# DepEd's site carries internal memos (olympiads, trainings), not suspensions: a smaller sample keeps
# easy negatives from inflating the headline accuracy.
DEFAULT_N = {"meco": 60, "mcwd": 60, "deped": 20, "pagasa": 60}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+", choices=sorted(SOURCES))
    ap.add_argument("--per-source", type=int, help="override the per-source cap")
    ap.add_argument("--no-media", action="store_true")
    args = ap.parse_args(argv)
    failed = 0
    for name in args.sources:
        n = args.per_source or DEFAULT_N[name]
        try:
            posts, stats = SOURCES[name](n)
        except Exception as e:
            print(f"ERROR {name}: {e}", file=sys.stderr)
            append_log({"page": name, "stop": f"error: {e}"})
            failed += 1
            continue
        if not args.no_media:
            for p in posts:
                for i, m in enumerate(p.media):
                    m.path = download(m.url, f"{p.post_id.replace(':', '_')}_{i}")
                    time.sleep(random.uniform(0.5, 1.5))
        added, skipped = append_posts(posts)
        stats.update(page=name, posts=len(posts), added=added, skipped=skipped,
                     posted_range=[str(min(d)), str(max(d))] if (d := [p.posted_at for p in posts if p.posted_at]) else None)
        append_log(stats)
        print(f"{name}: {len(posts)} posts -> {POSTS}: +{added} new, {skipped} already stored  {stats}")
        pause()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
