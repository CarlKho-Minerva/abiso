"""Firestore: where the deployed service keeps the feed, so it can be refreshed without a redeploy.

Collections (database "(default)", asia-southeast1):
    posts/{doc}      one captured Post (doc id = post_id with "/" replaced)
    compiled/{doc}   one compiler result per post: {post_id, events[], usd, seconds} or {post_id, error}
    events/{id}      the reconciled feed the store serves (replaced as a set on each refresh)
    meta/feed        {updated_at, n_events, n_posts, last_refresh{...}, usd_total}
    questions/{auto} what people asked the page and how long the answer took (no IP, no name)

    uv run python -m serve.fs push      # seed from data/raw/posts.jsonl, data/compiled.jsonl, data/events.jsonl
    uv run python -m serve.fs stats

Local runs use the isolated TMUC gcloud ADC (~/.config/carl-life-os/gcloud-tmuc); Cloud Run uses its
service account. Project: ABISO_GCP_PROJECT (default dev-antler-508423-m1).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

PROJECT = os.environ.get("ABISO_GCP_PROJECT", "dev-antler-508423-m1")
REPO = Path(__file__).resolve().parent.parent
BATCH = 400   # Firestore allows 500 writes per batch

_client = None


def client():
    global _client
    if _client is None:
        if not os.environ.get("K_SERVICE"):   # not on Cloud Run: use the isolated TMUC login
            adc = Path.home() / ".config/carl-life-os/gcloud-tmuc/application_default_credentials.json"
            if adc.exists():
                os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(adc))
        from google.cloud import firestore
        _client = firestore.Client(project=PROJECT)
    return _client


def doc_id(post_id: str) -> str:
    return post_id.replace("/", "_")


def _write(coll: str, rows: Iterable[tuple[str, dict]]) -> int:
    db = client()
    n, batch = 0, db.batch()
    for key, data in rows:
        batch.set(db.collection(coll).document(key), data)
        n += 1
        if n % BATCH == 0:
            batch.commit()
            batch = db.batch()
    batch.commit()
    return n


def read_all(coll: str) -> list[dict]:
    return [d.to_dict() for d in client().collection(coll).stream()]


def replace_events(events: list[dict]) -> int:
    """Write the reconciled set and delete events no longer in it (a merge can retire an id)."""
    db = client()
    keep = {e["event_id"] for e in events}
    stale = [d.id for d in db.collection("events").select([]).stream() if d.id not in keep]
    n = _write("events", ((e["event_id"], e) for e in events))
    batch = db.batch()
    for i, sid in enumerate(stale, 1):
        batch.delete(db.collection("events").document(sid))
        if i % BATCH == 0:
            batch.commit()
            batch = db.batch()
    batch.commit()
    return n


def write_posts(posts: list[dict]) -> int:
    return _write("posts", ((doc_id(p["post_id"]), p) for p in posts))


def write_compiled(rows: list[dict]) -> int:
    return _write("compiled", ((doc_id(r["post_id"]), r) for r in rows))


def get_meta() -> dict:
    snap = client().collection("meta").document("feed").get()
    return snap.to_dict() or {}


def set_meta(**fields) -> None:
    client().collection("meta").document("feed").set(fields, merge=True)


def log_question(q: str, answer: str, ok: bool, ms: int, lang_hint: str = "") -> None:
    client().collection("questions").add({
        "q": q[:500], "answer": answer[:2000], "ok": ok, "ms": ms,
        "at": datetime.now(timezone.utc).isoformat(), "lang_hint": lang_hint,
    })


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def push() -> int:
    posts = _jsonl(REPO / "data/raw/posts.jsonl")
    from abiso.build import _read_cache   # last row per post wins (retries append)
    compiled = list(_read_cache(REPO / "data/compiled.jsonl").values())
    events = _jsonl(REPO / "data/events.jsonl")
    if not (posts and compiled and events):
        print("push: posts, compiled and events files must all exist and be non-empty", file=sys.stderr)
        return 1
    for p in posts:   # local media paths mean nothing on Cloud Run; the URL stays
        for m in p.get("media", []):
            m["path"] = None
    usd = round(sum(r.get("usd", 0) for r in compiled), 4)
    print(f"posts {write_posts(posts)}, compiled {write_compiled(compiled)}, "
          f"events {replace_events(events)}", file=sys.stderr)
    set_meta(updated_at=datetime.now(timezone.utc).isoformat(), n_events=len(events),
             n_posts=len(posts), usd_total=usd, seeded_from="local data/ files")
    return 0


def stats() -> int:
    meta = get_meta()
    print(json.dumps(meta, indent=2, default=str))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit({"push": push, "stats": stats}.get(cmd, lambda: (print(__doc__), 2)[1])())
