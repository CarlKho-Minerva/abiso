"""Keep the deployed feed current: capture the issuers' sites, compile new posts with Gemini, reconcile.

Cloud Scheduler calls POST /tasks/refresh every few hours (header X-Abiso-Task); it can also run by hand:

    uv run python -m serve.refresh --max-usd 0.10          # PAID: Gemini on new posts only

Sources are the ones a server can read without a login (collect/sites.py): MECO's outage notices
(WordPress API), MCWD's interruption cards, DepEd Lapu-Lapu issuances, PAGASA's Visayas forecast.
Facebook pages need a logged-out browser and stay a Mac-side capture (collect/facebook.py).

Spend: each run stops before a post whose worst case would pass max_usd, and the running total
(meta/feed.usd_total) stops all compiling past ABISO_REFRESH_TOTAL_USD (default $10).
A source that fails is reported in the result and in meta/feed.last_refresh; the run goes on with the
others and the result's ok is false, so the scheduler's log shows the failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from abiso.compile import CompileError, Gemini, MediaError, compile_post_detailed
from abiso.reconcile import reconcile_posts
from abiso.schema import Event, Post
from collect import sites
from serve import fs, store

# Gemini Flash list prices per 1M tokens (the same table abiso.build uses)
from abiso.build import GEMINI_PRICE, _transient, _with_retry

SOURCES = {
    "meco": lambda: sites.capture_meco(15),
    "mcwd": lambda: sites.capture_mcwd(20),
    "deped": lambda: sites.capture_deped(10),
    "pagasa": lambda: sites.capture_pagasa(3),
}
TOTAL_CAP = float(os.environ.get("ABISO_REFRESH_TOTAL_USD", "10"))


def _worst(post: Post) -> float:
    n_img = sum(m.kind == "image" for m in post.media)
    return 2 * ((1100 * n_img + 1500) / 1e6 * GEMINI_PRICE["input"] + 2000 / 1e6 * GEMINI_PRICE["output"])


def run(max_usd: float = 0.10, backend=None) -> dict:
    t0 = time.monotonic()
    started = datetime.now(timezone.utc).isoformat()
    known = {p["post_id"] for p in fs.read_all("posts")}
    meta = fs.get_meta()
    total = float(meta.get("usd_total", 0))

    captured, source_errors = [], {}
    for name, cap in SOURCES.items():
        try:
            posts, _ = cap()
            captured += posts
        except Exception as e:  # noqa: BLE001  one dead site must not stop the others; reported below
            source_errors[name] = f"{type(e).__name__}: {e}"[:300]
    new = [p for p in captured if p.post_id not in known]

    rows, spent, skipped = [], 0.0, []
    backend = backend or Gemini()
    for post in new:
        if spent + _worst(post) > max_usd or total + spent + _worst(post) > TOTAL_CAP:
            skipped.append(post.post_id)
            continue
        t1 = time.monotonic()
        replies = []
        try:
            try:
                res = _with_retry(lambda: compile_post_detailed(post, backend))
                row = {"post_id": post.post_id, "used_images": res.used_images}
            except MediaError as e:
                res = _with_retry(lambda: compile_post_detailed(post, backend, images=[]))
                row = {"post_id": post.post_id, "used_images": False, "images_unavailable": str(e)[:300]}
            replies = res.replies
            row["events"] = [json.loads(ev.model_dump_json()) for ev in res.events]
        except Exception as e:  # noqa: BLE001
            if not (isinstance(e, CompileError) or _transient(e)):
                raise
            replies = getattr(e, "replies", [])
            row = {"post_id": post.post_id, "error": f"{type(e).__name__}: {e}"[:2000]}
        cost = sum((r.input_tokens or 0) / 1e6 * GEMINI_PRICE["input"]
                   + (r.output_tokens or 0) / 1e6 * GEMINI_PRICE["output"] for r in replies)
        spent += cost
        row["usd"] = round(cost, 6)
        row["seconds"] = round(time.monotonic() - t1, 2)
        row["compiled_at"] = datetime.now(timezone.utc).isoformat()
        rows.append(row)

    done = {r["post_id"] for r in rows}
    fresh_posts = [p for p in new if p.post_id in done]   # skipped posts are captured again next run
    if fresh_posts:
        fs.write_posts([json.loads(p.model_dump_json()) for p in fresh_posts])
        fs.write_compiled(rows)

    n_events = int(meta.get("n_events", 0))
    if fresh_posts or not meta.get("n_events"):
        all_posts = [Post.model_validate(d) for d in fs.read_all("posts")]
        compiled = fs.read_all("compiled")
        events = [Event.model_validate(e) for r in compiled for e in r.get("events", [])]
        result = reconcile_posts(events, all_posts)
        n_events = fs.replace_events([json.loads(e.model_dump_json()) for e in result.events])

    errors = [r["post_id"] for r in rows if "error" in r]
    report = {
        "started": started, "seconds": round(time.monotonic() - t0, 1),
        "captured": len(captured), "new": len(new), "compiled": len(rows) - len(errors),
        "compile_errors": errors, "skipped_for_budget": skipped, "source_errors": source_errors,
        "usd": round(spent, 4), "n_events": n_events,
        "ok": not (errors or source_errors or skipped),
    }
    now = datetime.now(timezone.utc).isoformat()
    fs.set_meta(last_refresh=report, usd_total=round(total + spent, 4), n_events=n_events,
                **({"updated_at": now} if fresh_posts else {}), checked_at=now,
                n_posts=len(known) + len(fresh_posts))
    store.clear_cache()
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, required=True, help="PAID: Gemini spend cap for this run")
    a = ap.parse_args()
    r = run(a.max_usd)
    print(json.dumps(r, indent=2))
    sys.exit(0 if r["ok"] else 1)
