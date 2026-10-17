"""Download a captured image into data/raw/media/ (gitignored). Returns the repo-relative path."""
from __future__ import annotations

import sys
import urllib.parse
import urllib.request
from pathlib import Path

from collect.store import MEDIA, ROOT

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def download(url: str | None, stem: str, dest: Path = MEDIA) -> str | None:
    """Fetch url to dest/<stem>.<ext>. On failure, warn loudly and return None (the URL stays in the Post)."""
    if not url:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    url = urllib.parse.quote(url, safe=":/?&=%#+,;@!~*'()$[]")  # site paths contain raw spaces
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype = r.headers.get_content_type()
            data = r.read()
    except Exception as e:
        print(f"WARN media download failed for {stem}: {e}", file=sys.stderr)
        return None
    out = dest / f"{stem}{_EXT.get(ctype, '.bin')}"
    out.write_bytes(data)
    try:
        return str(out.relative_to(ROOT))
    except ValueError:
        return str(out)
