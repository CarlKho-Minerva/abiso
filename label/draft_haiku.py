"""Draft one Label per post with Claude Haiku, for Carl to review on the label page.

    uv run python -m label.draft_haiku --max-usd 2.00          # PAID (Anthropic API, TMUC key)

Protocol amendment (Carl, 2026-10-08): labels are model-drafted and human-reviewed, not hand-labelled
from scratch. Drafts go to data/labels/drafts.jsonl, never to labels.jsonl; a draft only becomes a label
when Carl saves it on the page (source=haiku_reviewed). Haiku is a different model family from the
systems being scored (Gemma, Gemini). Resumable: posts already drafted are skipped. Stops before the
estimated spend passes --max-usd. Exits non-zero if any post failed.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx

from abiso.compile import read_env_key
from abiso.places import BARANGAYS
from abiso.schema import DisruptionType, Label, Post, Status
from label.server import DRAFTS, MEDIA, PH, POSTS, ROOT, load_posts, parse_ph

MODEL = "claude-haiku-4-5"
PRICE = {"input": 1.00, "output": 5.00}      # USD per million tokens
MAX_IMAGES = 6
MAX_IMAGE_BYTES = 4_500_000                  # API limit is 5 MB per image

RULES = f"""You label official Lapu-Lapu City (Cebu, Philippines) disruption notices. One label per post.
If a post lists several outages: use the most common type, the union of every barangay named, and the
overall window (earliest start to latest end). Times are Philippine time (UTC+8); resolve relative dates
("bukas", "ugma", "tomorrow") from the post date. Use null for anything the post does not state.
Barangays must come from this list exactly; leave the list empty if city-wide or none named:
{", ".join(BARANGAYS)}.
Types: power, water, class_suspension, work_suspension, road, weather, other_advisory, not_disruption
(events, PR, greetings). Status: scheduled, ongoing, extended, cancelled, resolved, or null.
In notes, give one short reason (under 20 words), and say if you were unsure."""

TOOL = {
    "name": "label_post",
    "description": "Record the label for this post.",
    "input_schema": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": [t.value for t in DisruptionType]},
            "status": {"type": ["string", "null"], "enum": [s.value for s in Status] + [None]},
            "barangays": {"type": "array", "items": {"type": "string"}},
            "start": {"type": ["string", "null"], "description": "YYYY-MM-DDTHH:MM, Philippine time"},
            "end": {"type": ["string", "null"], "description": "YYYY-MM-DDTHH:MM, Philippine time"},
            "all_day": {"type": "boolean"},
            "notes": {"type": "string"},
        },
        "required": ["type", "barangays", "all_day", "notes"],
    },
}


def image_blocks(post: Post) -> list[dict]:
    out = []
    for m in post.media:
        if m.kind != "image" or not m.path or len(out) >= MAX_IMAGES:
            continue
        p = MEDIA / Path(m.path).name
        if not p.exists() or p.stat().st_size > MAX_IMAGE_BYTES:
            continue
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            continue
        out.append({"type": "image", "source": {"type": "base64", "media_type": mime,
                                                "data": base64.b64encode(p.read_bytes()).decode()}})
    return out


def worst_cost(post: Post) -> float:
    n_img = min(MAX_IMAGES, sum(m.kind == "image" for m in post.media))
    return 2 * ((1700 * n_img + 2500) / 1e6 * PRICE["input"] + 600 / 1e6 * PRICE["output"])


def draft(post: Post, key: str, workspace: str) -> tuple[dict, float]:
    alt = " ".join(m.alt for m in post.media if m.alt)
    text = (f"Issuer page: {post.page}\nPosted: {post.posted_at}\nURL: {post.url}\n\n"
            f"Post text:\n{post.text or '(no text)'}\n\nImage alt text: {alt or '(none)'}")
    body = {"model": MODEL, "max_tokens": 600, "system": RULES, "tools": [TOOL],
            "tool_choice": {"type": "tool", "name": "label_post"},
            "messages": [{"role": "user", "content": [{"type": "text", "text": text}] + image_blocks(post)}]}
    for wait in (5, 20, 60, None):
        r = httpx.post("https://api.anthropic.com/v1/messages", json=body, timeout=120,
                       headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                "anthropic-workspace-id": workspace})
        if r.status_code in (429, 500, 502, 503, 529) and wait:
            time.sleep(wait)
            continue
        r.raise_for_status()
        break
    data = r.json()
    use = data.get("usage", {})
    cost = use.get("input_tokens", 0) / 1e6 * PRICE["input"] + use.get("output_tokens", 0) / 1e6 * PRICE["output"]
    out = next(c["input"] for c in data["content"] if c["type"] == "tool_use")
    brgys = [b for b in out.get("barangays", []) if b in BARANGAYS]
    dropped = [b for b in out.get("barangays", []) if b not in BARANGAYS]
    notes = out.get("notes", "").strip() + (f" [dropped unknown barangays: {dropped}]" if dropped else "")
    lab = Label(post_id=post.post_id, type=DisruptionType(out["type"]),
                status=Status(out["status"]) if out.get("status") else None, barangays=brgys,
                start=parse_ph(out.get("start")), end=parse_ph(out.get("end")),
                all_day=bool(out.get("all_day")), labelled_at=datetime.now(PH),
                notes=notes[:300], source="haiku_draft")
    return json.loads(lab.model_dump_json()), cost


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, required=True)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    key = read_env_key("ANTHROPIC_TMUC_API_KEY")
    workspace = read_env_key("ANTHROPIC_TMUC_WORKSPACE_ID")
    posts = load_posts(POSTS)
    done = {json.loads(l)["post_id"] for l in DRAFTS.read_text().splitlines() if l.strip()} if DRAFTS.exists() else set()
    todo = [p for p in posts if p.post_id not in done]
    print(f"{len(posts)} posts, {len(done)} drafted, {len(todo)} to draft", file=sys.stderr)
    lock, spent, reserved, fails = threading.Lock(), [0.0], [0.0], []
    DRAFTS.parent.mkdir(parents=True, exist_ok=True)

    def one(post: Post) -> None:
        w = worst_cost(post)
        with lock:
            if spent[0] + reserved[0] + w > a.max_usd:
                fails.append((post.post_id, "skipped: spend cap"))
                return
            reserved[0] += w
        try:
            row, cost = draft(post, key, workspace)
        except Exception as e:  # noqa: BLE001 - recorded and reported, run continues
            with lock:
                reserved[0] -= w
                fails.append((post.post_id, f"{type(e).__name__}: {str(e)[:200]}"))
            return
        with lock:
            reserved[0] -= w
            spent[0] += cost
            with DRAFTS.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"  {post.post_id} -> {row['type']} (${spent[0]:.3f})", file=sys.stderr)

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(one, todo))
    print(f"drafted {len(todo) - len(fails)}/{len(todo)}; spend ${spent[0]:.4f}; failures {len(fails)}", file=sys.stderr)
    for pid, why in fails[:20]:
        print(f"  FAIL {pid}: {why}", file=sys.stderr)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
