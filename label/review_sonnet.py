"""Second pass on the Haiku drafts: Claude Sonnet 5.5 checks every draft against the post and its images.

    uv run python -m label.review_sonnet            # uses Carl's Claude Max plan via `claude -p` (authorized 2026-10-08)

Batches of 10 posts per call (about 17 calls), 2 calls at a time. Sonnet opens the images itself with the
Read tool. Every verdict goes to data/labels/reviews_sonnet.jsonl. When Sonnet corrects a draft, the corrected
label is appended to data/labels/drafts.jsonl (the newest line per post wins), so the label page shows the
corrected draft with Sonnet's reason. Resumable; exits non-zero if any batch failed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from abiso.places import BARANGAYS
from abiso.schema import DisruptionType, Label, Status
from label.draft_haiku import RULES
from label.server import DRAFTS, MEDIA, PH, POSTS, ROOT, latest_labels, load_posts, parse_ph

MODEL = "claude-sonnet-5-5"
REVIEWS = ROOT / "data" / "labels" / "reviews_sonnet.jsonl"
RAW = ROOT / "data" / "labels" / "reviews_sonnet_raw.jsonl"
BATCH = 10
MAX_IMAGES = 4

ASK = """You are checking draft labels for official Lapu-Lapu City disruption notices. For EACH post below,
open its image files with the Read tool when it has any (the notice is often only in the image), then decide
whether the draft label is correct under these rules:

{rules}

Reply with ONLY a JSON array, one object per post, no prose:
[{{"post_id": "...", "verdict": "ok" or "fixed", "reason": "under 20 words",
   "type": "...", "status": null or "...", "barangays": [...], "start": null or "YYYY-MM-DDTHH:MM",
   "end": null or "YYYY-MM-DDTHH:MM", "all_day": true/false}}]
For "ok", repeat the draft's values. Times are Philippine time.

POSTS:
{posts}"""


def batch_prompt(posts, drafts) -> str:
    items = []
    for p in posts:
        d = drafts[p.post_id]
        imgs = [str(MEDIA / Path(m.path).name) for m in p.media
                if m.kind == "image" and m.path and (MEDIA / Path(m.path).name).exists()][:MAX_IMAGES]
        items.append({"post_id": p.post_id, "page": p.page, "posted": str(p.posted_at), "text": p.text or "",
                      "image_files": imgs,
                      "draft": {k: d.get(k) for k in ("type", "status", "barangays", "start", "end", "all_day", "notes")}})
    return ASK.format(rules=RULES, posts=json.dumps(items, ensure_ascii=False, indent=1))


def run_batch(posts, drafts) -> list[dict]:
    r = subprocess.run(["claude", "-p", "--model", MODEL, "--output-format", "json",
                        "--allowedTools", "Read", "--add-dir", str(MEDIA)],
                       input=batch_prompt(posts, drafts), capture_output=True, text=True, timeout=900, cwd=ROOT)
    if r.returncode:
        raise RuntimeError(f"claude -p exit {r.returncode}: {r.stderr[:300]}")
    RAW.parent.mkdir(parents=True, exist_ok=True)
    with RAW.open("a") as f:  # keep every raw reply so a parse bug never costs a rerun
        f.write(json.dumps({"first": posts[0].post_id, "stdout": r.stdout}) + "\n")
    out = json.loads(r.stdout)
    if isinstance(out, list):  # newer CLI: a list of messages; the final one carries "result"
        out = next((m for m in reversed(out) if isinstance(m, dict) and "result" in m), {})
    text = out.get("result", "")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        raise RuntimeError(f"no JSON array in reply: {text[:200]}")
    return json.loads(text[start:end + 1])


def to_label(v: dict) -> dict:
    brgys = [b for b in v.get("barangays", []) if b in BARANGAYS]
    lab = Label(post_id=v["post_id"], type=DisruptionType(v["type"]),
                status=Status(v["status"]) if v.get("status") else None, barangays=brgys,
                start=parse_ph(v.get("start")), end=parse_ph(v.get("end")), all_day=bool(v.get("all_day")),
                labelled_at=datetime.now(PH), notes=f"Sonnet fixed: {v.get('reason', '')}"[:300],
                source="sonnet_fixed_draft")
    return json.loads(lab.model_dump_json())


def main(limit_batches: int | None = None) -> int:
    posts = load_posts(POSTS)
    drafts = latest_labels(DRAFTS)
    done = {json.loads(l)["post_id"] for l in REVIEWS.read_text().splitlines() if l.strip()} if REVIEWS.exists() else set()
    todo = [p for p in posts if p.post_id in drafts and p.post_id not in done]
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    batches = batches[:limit_batches] if limit_batches else batches
    print(f"{len(todo)} drafts to review in {len(batches)} batches", file=sys.stderr)
    fails, fixed = [], 0

    def one(b):
        nonlocal fixed
        try:
            verdicts = run_batch(b, drafts)
        except Exception as e:  # noqa: BLE001 - reported at the end, run continues
            fails.append(f"batch starting {b[0].post_id}: {type(e).__name__}: {str(e)[:200]}")
            return
        want = {p.post_id for p in b}
        for v in verdicts:
            if v.get("post_id") not in want:
                continue
            with REVIEWS.open("a") as f:
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
            if v.get("verdict") == "fixed":
                try:
                    row = to_label(v)
                except Exception as e:  # noqa: BLE001
                    fails.append(f"{v['post_id']}: bad fix {e}")
                    continue
                with DRAFTS.open("a") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                fixed += 1
        missing = want - {v.get("post_id") for v in verdicts}
        if missing:
            fails.append(f"no verdict for {sorted(missing)}")
        print(f"  batch {b[0].post_id}: {len(verdicts)} verdicts", file=sys.stderr)

    with ThreadPoolExecutor(2) as ex:
        list(ex.map(one, batches))
    print(f"reviewed; Sonnet fixed {fixed}; failures {len(fails)}", file=sys.stderr)
    for f in fails:
        print("  FAIL", f, file=sys.stderr)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else None))
