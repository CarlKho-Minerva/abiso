"""Label review page for Abiso. Stdlib only; one post at a time; append-only labels.

    uv run python -m label.server            # http://0.0.0.0:8516

Reads  data/raw/posts.jsonl   (Posts, never modified)
Writes data/labels/labels.jsonl (one Label per line, append-only; the latest line per post wins)

No model output is ever shown or pre-filled: labels must stay independent of the systems they score.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from abiso.places import BARANGAYS
from abiso.schema import DisruptionType, Label, Post, Status

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "data" / "raw" / "posts.jsonl"
LABELS = ROOT / "data" / "labels" / "labels.jsonl"
DRAFTS = ROOT / "data" / "labels" / "drafts.jsonl"
SPOT = ROOT / "data" / "labels" / "spotcheck.json"
HUMAN = ("hand", "haiku_reviewed", "human_spotcheck")
MEDIA = ROOT / "data" / "raw" / "media"
STATIC = Path(__file__).resolve().parent / "static"
PH = timezone(timedelta(hours=8))

QUICK_TAGS = ["opportunity", "person", "interesting", "rss"]
# Keyboard shortcut per type, shown on the buttons.
TYPE_KEYS = {
    "power": "p", "water": "w", "class_suspension": "c", "work_suspension": "k",
    "road": "r", "weather": "t", "other_advisory": "o", "not_disruption": "n",
}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_posts(path: Path = POSTS) -> list[Post]:
    return [Post.model_validate(d) for d in load_jsonl(path)]


def latest_labels(path: Path = LABELS) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in load_jsonl(path):
        out[d["post_id"]] = d  # later lines supersede earlier ones
    return out


def parse_ph(value: str | None) -> datetime | None:
    """'2026-10-04T08:00' from a datetime-local input is Philippine time; attach +08:00."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=PH) if dt.tzinfo is None else dt.astimezone(PH)


def build_label(payload: dict, known_ids: set[str], now: datetime | None = None, spot: bool = False) -> Label:
    """Validate a form payload into a Label. Raises ValueError on anything off."""
    pid = payload.get("post_id")
    if pid not in known_ids:
        raise ValueError(f"unknown post_id: {pid!r}")
    bad = [b for b in payload.get("barangays", []) if b not in BARANGAYS]
    if bad:
        raise ValueError(f"not Lapu-Lapu barangays: {bad}")
    tags = []
    for t in payload.get("tags", []):
        t = " ".join(str(t).strip().lower().split())
        if t and t not in tags:
            tags.append(t)
    return Label(
        post_id=pid,
        type=DisruptionType(payload["type"]),
        status=Status(payload["status"]) if payload.get("status") else None,
        barangays=list(dict.fromkeys(payload.get("barangays", []))),
        start=parse_ph(payload.get("start")),
        end=parse_ph(payload.get("end")),
        all_day=bool(payload.get("all_day")),
        labelled_at=now or datetime.now(PH),
        notes=str(payload.get("notes", "")).strip(),
        tags=tags,
        source="human_spotcheck" if spot else (payload.get("source") if payload.get("source") in ("hand", "haiku_reviewed") else "hand"),
    )


def append_label(label: Label, path: Path = LABELS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(label.model_dump(mode="json"), ensure_ascii=False) + "\n")


def spot_ids(labels_path: Path = LABELS) -> list[str] | None:
    """Spot-check mode (Carl, 2026-10-09): the page shows only these posts, pre-filled with Claude's label."""
    f = Path(labels_path).parent / SPOT.name
    return json.loads(f.read_text())["ids"] if f.exists() else None


def state(posts_path: Path = POSTS, labels_path: Path = LABELS) -> dict:
    posts = load_posts(posts_path)
    labels = latest_labels(labels_path)
    spot = spot_ids(labels_path)
    if spot is not None:
        keep = set(spot)
        posts = [p for p in posts if p.post_id in keep]
        claude = {k: v for k, v in labels.items() if k in keep and v.get("source") not in HUMAN}
        labels = {k: v for k, v in labels.items() if k in keep and v.get("source") in HUMAN}
        drafts = {k: v for k, v in claude.items() if k not in labels}
    else:
        drafts = latest_labels(DRAFTS)
    ids = {p.post_id for p in posts}
    return {
        "posts": [p.model_dump(mode="json") for p in posts],
        "labels": labels,
        "total": len(posts),
        "labelled": len(ids & labels.keys()),
        "barangays": list(BARANGAYS),
        "types": [{"value": t.value, "key": TYPE_KEYS[t.value]} for t in DisruptionType],
        "statuses": [s.value for s in Status],
        "quick_tags": QUICK_TAGS,
        "drafts": drafts,
    }


class Handler(BaseHTTPRequestHandler):
    posts_path = POSTS
    labels_path = LABELS
    media_dir = MEDIA

    def log_message(self, fmt, *args):  # quieter: one line per request to stderr
        sys.stderr.write("label: " + fmt % args + "\n")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._json(200, state(self.posts_path, self.labels_path))
        if path.startswith("/media/"):
            f = (self.media_dir / unquote(path[len("/media/"):])).resolve()
            if not f.is_relative_to(self.media_dir.resolve()) or not f.is_file():
                return self._json(404, {"error": "no such media"})
            return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/label":
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(n) or b"{}")
            ids = {p.post_id for p in load_posts(self.posts_path)}
            label = build_label(payload, ids, spot=spot_ids(self.labels_path) is not None)
        except (ValueError, KeyError, ValidationError, json.JSONDecodeError) as e:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
        append_label(label, self.labels_path)
        labels = latest_labels(self.labels_path)
        self._json(200, {"ok": True, "label": label.model_dump(mode="json"),
                         "labelled": len(ids & labels.keys()), "total": len(ids)})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Abiso label review page")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8516)
    args = ap.parse_args(argv)
    if not POSTS.exists():
        print(f"ERROR: {POSTS} does not exist; run collect first", file=sys.stderr)
        return 1
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Abiso label page on http://{args.host}:{args.port}  ({len(load_posts())} posts; labels -> {LABELS})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
