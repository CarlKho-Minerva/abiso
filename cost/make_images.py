"""Render the SYNTHETIC notice images for the cost workload.

City notices on Facebook are usually a 1080x1080 or 1080x1350 JPEG with the
notice set in large type. These stand-ins match that size and format so image
token counts and vision prefill time are representative. Every image carries a
"SYNTHETIC" banner so it can never be mistaken for a real city post.

    uv run --with pillow python cost/make_images.py
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
SAMPLES = HERE / "samples"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def render(text: str, out: Path, header: str) -> None:
    img = Image.new("RGB", (1080, 1350), (250, 247, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1080, 150], fill=(160, 20, 30))
    d.text((60, 52), f"{header.upper()} (SYNTHETIC)", font=ImageFont.truetype(FONT, 34), fill="white")
    y = 220
    for i, line in enumerate(text.split("\n")):
        size = 64 if i < 2 else 40
        while d.textlength(line, font=ImageFont.truetype(FONT, size)) > 960:
            size -= 2
        d.text((60, y), line, font=ImageFont.truetype(FONT, size), fill=(20, 20, 20))
        y += size + 26
    d.text((60, 1270), "SYNTHETIC - generated for cost measurement, not a real notice",
           font=ImageFont.truetype(FONT, 28), fill=(120, 120, 120))
    img.save(out, "JPEG", quality=85)


def main() -> None:
    for line in (SAMPLES / "posts.jsonl").read_text().splitlines():
        p = json.loads(line)
        if p["image"]:
            out = SAMPLES / p["image"]
            out.parent.mkdir(parents=True, exist_ok=True)
            render(p["image_text"], out, p["page"])
            print(out)


if __name__ == "__main__":
    main()
