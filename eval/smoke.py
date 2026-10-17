"""Smoke test of both compile backends on 4 synthetic posts (no real posts yet).

    uv run python -m eval.smoke gemma          # PC vLLM, free, shared with nightly jobs
    uv run python -m eval.smoke gemini         # PAID: hard cap of 8 calls per run

Post 2 is an image-only brownout schedule from the city: its text says nothing
about places or times, so a correct answer proves the backend read the image.
Post 4 is MECO posting the same Pusok outage at 08:30 (the city image says
08:00), so reconcile must merge the two and report a start conflict.
Writes eval/smoke_<backend>.json with every event plus tokens, latency and cost.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime
from pathlib import Path

from abiso.compile import CompileError, Gemini, GemmaVLLM, ImagePart, compile_post_detailed
from abiso.reconcile import reconcile_posts
from abiso.schema import Media, Post

HERE = Path(__file__).resolve().parent
MAX_GEMINI_CALLS = 8
# Gemini 3.8 Flash paid tier, USD per 1M tokens, through 2026-12-31
# (ai.google.dev/gemini-api/docs/pricing, read 2026-10-03). Doubles on 2027-01-01.
GEMINI_PRICE = {"input": 0.75, "output": 3.75}


def barangays() -> list[str]:
    try:
        from abiso.places_match import load_barangays
        return load_barangays()
    except ImportError:
        from tests.fixtures.barangays import LAPULAPU_BARANGAYS
        return LAPULAPU_BARANGAYS


def schedule_png() -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30)
    big = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 44)
    img = Image.new("RGB", (1080, 760), "white")
    d = ImageDraw.Draw(img)
    lines = [
        ("ABISO: SCHEDULED POWER INTERRUPTION", big),
        ("Petsa: Oktubre 6, 2026 (Martes)", font),
        ("", font),
        ("8:00 AM - 5:00 PM", font),
        ("  Portions of Brgy. Pusok (Feeder 7), M.L. Quezon Hi-way", font),
        ("", font),
        ("1:00 PM - 4:00 PM", font),
        ("  Brgy. Marigondon: Sitio Ibabao ug Sitio Lawis", font),
        ("", font),
        ("Rason: Line maintenance. Pasensya sa kahasol.", font),
    ]
    y = 40
    for text, f in lines:
        d.text((40, y), text, fill="black", font=f)
        y += 62
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def posts() -> list[tuple[Post, list[ImagePart] | None, str]]:
    cap = datetime.fromisoformat("2026-10-03T21:00:00+08:00")
    p1 = Post(post_id="smoke-1", page="LLCPIO", url="https://example.invalid/smoke-1",
              posted_at=datetime.fromisoformat("2026-10-03T19:40:00+08:00"), captured_at=cap,
              text=("ABISO: Walay klase sa tanang lebel, publiko ug pribado, ugma tungod sa "
                    "Bagyong Paeng. Suspended ang klase sa tibuok siyudad sa Lapu-Lapu."),
              capture_method="dom")
    p2 = Post(post_id="smoke-2", page="LLCPIO", url="https://example.invalid/smoke-2",
              posted_at=datetime.fromisoformat("2026-10-03T10:15:00+08:00"), captured_at=cap,
              text="ABISO. Tan-awa ang hulagway.",
              media=[Media(kind="image")], capture_method="dom")
    p3 = Post(post_id="smoke-3", page="LLCPIO", url="https://example.invalid/smoke-3",
              posted_at=datetime.fromisoformat("2026-10-03T08:00:00+08:00"), captured_at=cap,
              text="Happy Founding Anniversary sa atong Barangay Basak! Daghang salamat sa tanan.",
              capture_method="dom")
    p4 = Post(post_id="smoke-4", page="MECO", url="https://example.invalid/smoke-4",
              posted_at=datetime.fromisoformat("2026-10-03T16:00:00+08:00"), captured_at=cap,
              text=("SCHEDULED POWER INTERRUPTION. Martes, Oktubre 6, 2026, 8:30 AM - 5:00 PM. "
                    "Apektado: portions of Pusok (Feeder 7) ug Ibabao, Cordova. Rason: line maintenance."),
              capture_method="dom")
    return [
        (p1, None, "class_suspension, city-wide, all_day 2026-10-04"),
        (p2, [ImagePart("image/png", schedule_png())],
         "2 power events 2026-10-06: Pusok 08-17, Marigondon 13-16 (image only)"),
        (p3, None, "not_disruption"),
        (p4, None, "power 2026-10-06 08:30-17:00, Pusok (+ Cordova as written)"),
    ]


def main(which: str) -> int:
    if which == "gemma":
        backend = GemmaVLLM()
    elif which == "gemini":
        backend = Gemini()
    else:
        print("usage: python -m eval.smoke gemma|gemini", file=sys.stderr)
        return 2
    names = barangays()
    results, calls, all_events, all_posts = [], 0, [], []
    for post, images, expected in posts():
        if which == "gemini" and calls + 2 > MAX_GEMINI_CALLS:
            # a retry would make 2 calls; stop rather than risk passing the cap
            print(f"STOP: Gemini call cap ({MAX_GEMINI_CALLS}) would be exceeded", file=sys.stderr)
            break
        rec = {"post_id": post.post_id, "expected": expected}
        try:
            r = compile_post_detailed(post, backend, names, images=images)
            rec["events"] = [json.loads(e.model_dump_json()) for e in r.events]
            rec["used_images"] = r.used_images
            replies = r.replies
            all_events += r.events
            all_posts.append(post)
        except CompileError as e:
            rec["error"] = str(e)
            replies = e.replies
        calls += len(replies)
        rec["calls"] = []
        for rp in replies:
            c = {"model": rp.model, "input_tokens": rp.input_tokens,
                 "output_tokens": rp.output_tokens, "latency_s": round(rp.latency_s, 2),
                 "usage": rp.raw_usage}
            if which == "gemini" and rp.input_tokens is not None:
                c["cost_usd"] = round(rp.input_tokens / 1e6 * GEMINI_PRICE["input"]
                                      + (rp.output_tokens or 0) / 1e6 * GEMINI_PRICE["output"], 6)
            rec["calls"].append(c)
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
    rr = reconcile_posts(all_events, all_posts)
    recon = {"events": [json.loads(e.model_dump_json()) for e in rr.events],
             "conflicts": [json.loads(c.model_dump_json()) for c in rr.conflicts]}
    print("reconciled:", json.dumps(recon["conflicts"], ensure_ascii=False))
    cost = sum(c.get("cost_usd", 0) for r in results for c in r["calls"])
    out = HERE / f"smoke_{which}.json"
    out.write_text(json.dumps({"backend": which, "model": backend.model,
                               "run_at": datetime.now().astimezone().isoformat(),
                               "n_calls": calls, "total_cost_usd": round(cost, 6),
                               "results": results, "reconciled": recon},
                              ensure_ascii=False, indent=1))
    print(f"wrote {out}  ({calls} model calls)")
    failed = [r for r in results if "error" in r]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
