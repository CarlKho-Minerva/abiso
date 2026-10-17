"""Compile every captured post into the feed the doors serve (data/events.jsonl).

    uv run python -m abiso.build                 # Gemma on the PC (free), resumable
    uv run python -m abiso.build --reconcile-only

Per-post results are cached in data/compiled.jsonl (one line per post: its
events, or its error), so a rerun only compiles new posts. Then every event is
reconciled oldest post first and written to data/events.jsonl; conflicts
between issuers go to data/conflicts.jsonl. Exits non-zero if any post failed,
after writing everything that did compile.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from abiso.compile import CompileError, Gemini, GemmaVLLM, MediaError, compile_post_detailed
from eval.smoke import GEMINI_PRICE
from abiso.reconcile import reconcile_posts
from abiso.schema import Event

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"


def _read_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["post_id"]] = row
    return out


def _transient(e: Exception) -> bool:
    """503/429/timeouts from Gemini or the PC: worth waiting for, not a compile failure."""
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    return code in (429, 500, 502, 503, 504) or type(e).__name__ in ("ReadTimeout", "ConnectTimeout", "ConnectError")


def _with_retry(fn, waits=(10, 30, 90)):
    for w in waits:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - re-raised unless transient
            if not _transient(e):
                raise
            print(f"  transient {type(e).__name__} ({getattr(e, 'code', '')}); retrying in {w}s", file=sys.stderr)
            time.sleep(w)
    return fn()


def main(argv: list[str] | None = None) -> int:
    # Imported here, not at the top: serve/refresh.py imports this module's helpers on Cloud Run,
    # where finetune/ is not in the image.
    from finetune.build_dataset import DEFAULT_POSTS, load_posts
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", type=Path, default=DEFAULT_POSTS)
    ap.add_argument("--cache", type=Path, default=DATA / "compiled.jsonl")
    ap.add_argument("--out", type=Path, default=DATA / "events.jsonl")
    ap.add_argument("--conflicts", type=Path, default=DATA / "conflicts.jsonl")
    ap.add_argument("--retry-errors", action="store_true", help="recompile posts that failed before")
    ap.add_argument("--reconcile-only", action="store_true")
    ap.add_argument("--backend", choices=["gemma", "gemini"], default="gemma")
    ap.add_argument("--max-usd", type=float, help="required for gemini (paid): hard spend cap")
    ap.add_argument("--max-images", type=int, default=None,
                    help="skip posts with more images than the backend accepts (PC vLLM: 1); they stay 'not compiled'")
    a = ap.parse_args(argv)

    posts = load_posts(a.posts)
    cache = _read_cache(a.cache)
    if a.retry_errors:
        cache = {k: v for k, v in cache.items() if "error" not in v}
        a.cache.write_text("".join(json.dumps(v) + "\n" for v in cache.values()))

    if a.backend == "gemini" and a.max_usd is None and not a.reconcile_only:
        print("REFUSING: gemini is paid; pass --max-usd after Carl approves the estimate", file=sys.stderr)
        return 2
    max_images = a.max_images if a.max_images is not None else (1 if a.backend == "gemma" else 99)
    spent = 0.0
    if not a.reconcile_only:
        backend = GemmaVLLM() if a.backend == "gemma" else Gemini()
        todo = [p for pid, p in posts.items() if pid not in cache
                and sum(m.kind == "image" for m in p.media) <= max_images]
        print(f"{len(posts)} posts, {len(cache)} cached, {len(todo)} to compile", file=sys.stderr)
        with a.cache.open("a") as f:
            for i, post in enumerate(todo, 1):
                if a.backend == "gemini":
                    n_img = sum(m.kind == "image" for m in post.media)
                    # worst case: ~1.1k tokens per image + 1.5k text in, 2k out, times 2 for a retry
                    worst = 2 * ((1100 * n_img + 1500) / 1e6 * GEMINI_PRICE["input"] + 2000 / 1e6 * GEMINI_PRICE["output"])
                    if spent + worst > a.max_usd:
                        print(f"STOP: spend cap ${a.max_usd} would be passed (${spent:.4f} spent)", file=sys.stderr)
                        break
                t0 = time.monotonic()
                replies = []
                try:
                    res = _with_retry(lambda: compile_post_detailed(post, backend))
                    replies = res.replies
                    row = {"post_id": post.post_id, "used_images": res.used_images,
                           "events": [json.loads(e.model_dump_json()) for e in res.events]}
                except MediaError as e:
                    # Images gone (expired CDN, site error): compile from text + alt text, and say so.
                    try:
                        res = _with_retry(lambda: compile_post_detailed(post, backend, images=[]))
                        replies = res.replies
                        row = {"post_id": post.post_id, "used_images": False,
                               "images_unavailable": str(e)[:300],
                               "events": [json.loads(ev.model_dump_json()) for ev in res.events]}
                    except Exception as e2:  # noqa: BLE001
                        if not (isinstance(e2, CompileError) or _transient(e2)):
                            raise
                        replies = getattr(e2, "replies", [])
                        row = {"post_id": post.post_id, "error": f"{type(e2).__name__}: {e2}"[:2000]}
                except Exception as e:  # noqa: BLE001
                    if not (isinstance(e, CompileError) or _transient(e)):
                        raise
                    # still unavailable after retries: record it, keep going, exit non-zero at the end
                    replies = getattr(e, "replies", [])
                    row = {"post_id": post.post_id, "error": f"{type(e).__name__}: {e}"[:2000]}
                if a.backend == "gemini":
                    cost = sum((rp.input_tokens or 0) / 1e6 * GEMINI_PRICE["input"]
                               + (rp.output_tokens or 0) / 1e6 * GEMINI_PRICE["output"] for rp in replies)
                    spent += cost
                    row["usd"] = round(cost, 6)
                row["seconds"] = round(time.monotonic() - t0, 2)
                f.write(json.dumps(row) + "\n")
                f.flush()
                cache[post.post_id] = row
                tag = "ERR" if "error" in row else f"{len(row['events'])} ev" + (" TEXT-ONLY" if "images_unavailable" in row else "")
                print(f"[{i}/{len(todo)}] {post.post_id} {tag} {row['seconds']}s", file=sys.stderr)

    events = [Event.model_validate(e) for row in cache.values() for e in row.get("events", [])]
    result = reconcile_posts(events, list(posts.values()))
    tmp = a.out.with_suffix(".tmp")
    tmp.write_text("".join(e.model_dump_json() + "\n" for e in result.events))
    tmp.replace(a.out)
    a.conflicts.write_text("".join(c.model_dump_json() + "\n"
                                   for c in result.conflicts))
    errors = [r for r in cache.values() if "error" in r]
    missing = [pid for pid in posts if pid not in cache]
    print(f"events: {len(events)} compiled -> {len(result.events)} after reconcile; "
          f"conflicts: {len(result.conflicts)}; failed posts: {len(errors)}; "
          f"not compiled: {len(missing)}; gemini spend this run: ${spent:.4f} -> {a.out}", file=sys.stderr)
    return 1 if errors or missing else 0


if __name__ == "__main__":
    sys.exit(main())
