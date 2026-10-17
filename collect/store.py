"""Append-only store for captured Posts. One validated Post per line, deduped by post_id."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from abiso.schema import Post

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "data" / "raw" / "posts.jsonl"
MEDIA = ROOT / "data" / "raw" / "media"


CAPTURE_LOG = ROOT / "data" / "raw" / "capture_log.jsonl"


def append_log(entry: dict, path: Path = CAPTURE_LOG) -> None:
    """One line per source per run: what was reached, where it stopped, what the page claimed to be."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"logged_at": datetime.now(UTC).isoformat(timespec="seconds"), **entry}
    with path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_posts(path: Path = POSTS) -> list[Post]:
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip():
            try:
                out.append(Post.model_validate_json(line))
            except Exception as e:  # a bad line is a bug upstream; say where
                raise ValueError(f"{path}:{n}: invalid Post: {e}") from e
    return out


def append_posts(posts: list[Post], path: Path = POSTS) -> tuple[int, int]:
    """Append posts whose post_id is not already stored. Returns (added, skipped).

    Posts are immutable once written, so a re-capture of a known id is skipped, not merged.
    """
    seen = {p.post_id for p in load_posts(path)}
    path.parent.mkdir(parents=True, exist_ok=True)
    added = skipped = 0
    with path.open("a") as f:
        for p in posts:
            if p.post_id in seen:
                skipped += 1
                continue
            seen.add(p.post_id)
            f.write(json.dumps(p.model_dump(mode="json"), ensure_ascii=False) + "\n")
            added += 1
    return added, skipped
