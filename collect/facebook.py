"""Capture recent public posts from an official Facebook Page, logged out, in Carl's Dia.

    uv run python -m collect.facebook LLCInformationOffice [--max-posts 250]

Rules this enforces (see docs/specs/2026-10-03-abiso-design.md section 5):
- Logged out only. Before reading anything it checks the page shows logged-out UI and no
  `c_user` session cookie; if it sees a session it aborts with exit code 3.
- Human pace: 1.5 to 4 s random pause between scroll steps. Cap of 250 posts.
- The only clicks: a dismissable login dialog's Close button, and "See more" inside a post.
- Stops at the login wall. Official page posts only: no comments, no commenter names.
- Opens its own tab and closes it at the end, even on error.
"""
from __future__ import annotations

import argparse
import random
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from abiso.schema import Media, Post
from collect import dia
from collect.media import download
from collect.store import POSTS, append_log, append_posts
from collect.timeparse import PH, from_epoch, parse_fb_time

EXTRACT_JS = (Path(__file__).parent / "fb_extract.js").read_text()

CLOSE_DIALOG_JS = """(() => { const c = document.querySelector('[role=dialog] [aria-label="Close"]');
if (c) { c.click(); return 'closed'; } return 'none'; })()"""

# Click "See more" only inside top-level posts (never inside comments).
SEE_MORE_JS = """(() => { let n = 0;
for (const a of document.querySelectorAll('[role=article]')) {
  if (a.parentElement && a.parentElement.closest('[role=article]')) continue;
  for (const b of a.querySelectorAll('[role=button]')) {
    if ((b.innerText || '').trim() === 'See more') { b.click(); n++; }
  }
} return String(n); })()"""

SCROLL_JS = "(() => { window.scrollBy(0, Math.round(window.innerHeight * 0.8)); return String(window.scrollY); })()"


class LoggedInError(RuntimeError):
    pass


def assert_logged_out(snap: dict) -> None:
    """Abort if anything suggests a Facebook session. We never read as anyone."""
    if "c_user" in snap["cookie_names"] or "xs" in snap["cookie_names"]:
        raise LoggedInError(f"session cookie present: {snap['cookie_names']}")
    if not (snap["login_button"] or snap["create_account"] or snap["wall"]):
        raise LoggedInError("no logged-out UI (no 'Log In' / 'Create new account') on the page")


def viewer_tz() -> str:
    """The capture machine's IANA zone; Facebook renders absolute dates in it."""
    link = Path("/etc/localtime").resolve()
    return str(link).split("zoneinfo/")[-1]


_TOGGLE = re.compile(r"\s*(?:…\s*)?See (?:more|less)\s*$")


def strip_toggle(text: str) -> str:
    """Drop a trailing "See more"/"See less" button label: it is Facebook's UI, not the issuer's words."""
    return _TOGGLE.sub("", text.strip())


def to_post(page: str, art: dict, captured_at: datetime, tz: str) -> Post:
    posted_at = None
    if art.get("creation_time"):
        posted_at = from_epoch(int(art["creation_time"]))
    elif art.get("raw_time"):
        posted_at = parse_fb_time(art["raw_time"], captured_at, tz)
    text = strip_toggle(art.get("text") or art.get("fallback_text") or "")
    if posted_at is None and art.get("raw_time"):
        text += f"\n[timestamp as shown, unresolved: {art['raw_time']}]"
    media = [Media(kind="image", url=i["src"], alt=i.get("alt")) for i in art.get("images", [])]
    return Post(
        post_id=art.get("numeric_id") or art["id"],
        page=page,
        url=art["permalink"],
        posted_at=posted_at,
        captured_at=captured_at,
        text=text,
        media=media,
        capture_method="dom",
    )


def _snapshot(tab: str) -> dict:
    """Extract once; if the page was mid-navigation and answered nothing, wait and try once more."""
    try:
        return dia.js_json(tab, EXTRACT_JS)
    except dia.DiaError:
        time.sleep(3)
        return dia.js_json(tab, EXTRACT_JS)


def needs_permalink(art: dict) -> bool:
    """Feed copy is incomplete: text still collapsed, or no exact time from the page data."""
    return bool(art.get("permalink")) and (art.get("see_more") or not art.get("creation_time"))


def enrich_from_permalinks(tab: str, found: dict[str, dict], log=print) -> int:
    """Open each incomplete post's own permalink (logged out, human pace) for its full text and
    exact creation_time. Stops at the first login redirect. Returns how many posts improved."""
    improved = 0
    for key, art in list(found.items()):
        if not needs_permalink(art):
            continue
        time.sleep(random.uniform(2.0, 4.5))
        dia.set_url(tab, art["permalink"])
        time.sleep(random.uniform(5, 7))
        try:
            snap = _snapshot(tab)
        except dia.DiaError as e:
            log(f"ERROR permalink {art['permalink']}: {e}")
            continue
        assert_logged_out(snap)
        if snap["url"].startswith("https://www.facebook.com/login"):
            log("permalink pass stopped: login redirect")
            break
        if snap["closable_dialog"]:
            dia.js(tab, CLOSE_DIALOG_JS)
        if any(a["see_more"] for a in snap["articles"]):
            dia.js(tab, SEE_MORE_JS)
            time.sleep(random.uniform(1.5, 2.5))
            snap = _snapshot(tab)
        match = [a for a in snap["articles"] if a["id"] == art["id"]] or snap["articles"][:1]
        if not match:
            continue
        better = match[0]
        merged = dict(art)
        if better.get("text") and len(better["text"]) > len(art.get("text") or ""):
            merged["text"], merged["see_more"] = better["text"], better["see_more"]
        for k in ("creation_time", "numeric_id"):  # exact time and stable numeric id, when the feed lacked them
            if better.get(k) and not art.get(k):
                merged[k] = better[k]
        if len(better.get("images", [])) > len(art.get("images", [])):
            merged["images"] = better["images"]
        if merged != art:
            found[key] = merged
            improved += 1
    return improved


def capture(page: str, max_posts: int = 250, max_idle_steps: int = 4, max_steps: int = 400,
            log=print) -> tuple[list[Post], dict]:
    """Scroll the page logged out and return (posts, stats). Never writes."""
    url = f"https://www.facebook.com/{page}"
    tab = dia.open_tab(url)
    stats = {"page": page, "steps": 0, "stop": None, "visibility": None}
    found: dict[str, dict] = {}
    try:
        time.sleep(random.uniform(5, 7))
        snap = _snapshot(tab)
        assert_logged_out(snap)
        stats.update(visibility=snap["visibility"], landed=snap["url"], title=snap["title"], intro=snap["intro"])
        if snap["visibility"] != "visible":
            log(f"warning: tab is {snap['visibility']}; Facebook may not load more posts in a background tab")
        idle = 0
        for step in range(max_steps):
            stats["steps"] = step + 1
            if snap["closable_dialog"]:
                dia.js(tab, CLOSE_DIALOG_JS)
            if any(a["see_more"] for a in snap["articles"]):
                dia.js(tab, SEE_MORE_JS)
                time.sleep(random.uniform(1.5, 2.5))
            try:
                snap = _snapshot(tab)
            except dia.DiaError as e:
                log(f"ERROR {page}: page stopped answering ({e}); keeping {len(found)} posts")
                stats["stop"] = f"js error: {e}"
                break
            assert_logged_out(snap)
            before = len(found)
            for a in snap["articles"]:
                key = a.get("numeric_id") or a["id"]
                if key not in found or (a.get("text") and not found[key].get("text")):
                    found[key] = a
            log(f"step {step + 1}: {len(found)} posts, wall={snap['wall']}, y={snap['scroll_y']:.0f}/{snap['scroll_h']}")
            if len(found) >= max_posts:
                stats["stop"] = "cap"
                break
            if snap["url"].startswith("https://www.facebook.com/login"):
                stats["stop"] = "login redirect"
                break
            idle = idle + 1 if len(found) == before else 0
            if idle >= max_idle_steps:
                stats["stop"] = "login wall" if snap["wall"] else "no new posts"
                break
            dia.js(tab, SCROLL_JS)
            time.sleep(random.uniform(1.5, 4.0))
        else:
            stats["stop"] = "max steps"
        stats["enriched"] = enrich_from_permalinks(tab, found, log)
    finally:
        dia.close_tab(tab)
    captured_at = datetime.now(UTC).astimezone(PH)
    tz = viewer_tz()
    posts = [to_post(page, a, captured_at, tz) for a in list(found.values())[:max_posts]]
    stats["posts"] = len(posts)
    return posts, stats


def capture_permalinks(page: str, urls: list[str], log=print) -> tuple[list[Post], dict]:
    """Capture specific posts by permalink (URLs seen earlier on the Page itself), logged out."""
    PERMA_ID = re.compile(r"/posts/(pfbid\w+|\d+)|fbid=(\d+)|story_fbid=(\w+)")
    found = {}
    for u in urls:
        m = PERMA_ID.search(u)
        if not m:
            raise ValueError(f"not a post permalink: {u}")
        pid = next(g for g in m.groups() if g)
        found[pid] = {"id": pid, "permalink": u, "see_more": True, "text": None, "images": []}
    tab = dia.open_tab("about:blank")
    stats = {"page": page, "mode": "permalinks", "requested": len(urls)}
    try:
        stats["enriched"] = enrich_from_permalinks(tab, found, log)
    finally:
        dia.close_tab(tab)
    captured_at = datetime.now(UTC).astimezone(PH)
    posts = [to_post(page, a, captured_at, viewer_tz()) for a in found.values() if a.get("text")]
    stats.update(posts=len(posts), stop="done")
    return posts, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pages", nargs="+", help="Facebook page slugs, e.g. LLCInformationOffice")
    ap.add_argument("--per-page", type=int, default=60, help="cap per Page (default 60)")
    ap.add_argument("--max-posts", type=int, default=250, help="cap across all Pages in this run")
    ap.add_argument("--no-media", action="store_true", help="skip downloading images")
    ap.add_argument("--permalink", action="append", default=[],
                    help="capture this post URL (seen earlier on the Page) instead of scrolling; needs exactly one page")
    args = ap.parse_args(argv)
    if args.permalink and len(args.pages) != 1:
        ap.error("--permalink needs exactly one page slug")
    total = 0
    for n, page in enumerate(args.pages):
        if n:
            time.sleep(random.uniform(5, 12))
        try:
            if args.permalink:
                posts, stats = capture_permalinks(page, args.permalink)
            else:
                posts, stats = capture(page, max_posts=min(args.per_page, args.max_posts - total))
        except LoggedInError as e:
            print(f"ABORT {page}: looks logged in ({e}). Nothing read, nothing written.", file=sys.stderr)
            return 3
        if not args.no_media:
            for p in posts:
                for i, m in enumerate(p.media):
                    m.path = download(m.url, f"fb_{p.post_id}_{i}")
        added, skipped = append_posts(posts)
        stats.update(added=added, skipped=skipped)
        append_log(stats)
        total += len(posts)
        print(f"{page}: {len(posts)} posts, stop={stats['stop']}, visibility={stats.get('visibility')} "
              f"-> {POSTS}: +{added} new, {skipped} already stored")
        if total >= args.max_posts:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
