import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer

import pytest

from abiso.schema import Post
from label import server
from label.server import PH, build_label, latest_labels

NOW = datetime(2026, 10, 4, 10, 0, tzinfo=PH)


def test_build_label_attaches_ph_offset_and_cleans_tags():
    lab = build_label({
        "post_id": "1", "type": "power", "status": "scheduled", "barangays": ["Basak", "Basak", "Pusok"],
        "start": "2026-10-05T08:00", "end": "2026-10-05T17:00", "all_day": False,
        "notes": " feeder 12 ", "tags": ["RSS", "rss", " interesting  person "],
    }, {"1"}, now=NOW)
    assert lab.start.isoformat() == "2026-10-05T08:00:00+08:00"
    assert lab.barangays == ["Basak", "Pusok"]
    assert lab.tags == ["rss", "interesting person"]
    assert lab.notes == "feeder 12"


@pytest.mark.parametrize("payload, msg", [
    ({"post_id": "nope", "type": "power"}, "unknown post_id"),
    ({"post_id": "1", "type": "power", "barangays": ["Lahug"]}, "not Lapu-Lapu"),
    ({"post_id": "1", "type": "flood"}, "flood"),
])
def test_build_label_rejects(payload, msg):
    with pytest.raises(ValueError, match=msg):
        build_label(payload, {"1"}, now=NOW)


@pytest.fixture
def live(tmp_path):
    posts = tmp_path / "posts.jsonl"
    labels = tmp_path / "labels.jsonl"
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.jpg").write_bytes(b"jpg")
    for pid in ("1", "2"):
        p = Post(post_id=pid, page="LLCInformationOffice", url=f"https://x/{pid}", captured_at=NOW,
                 capture_method="dom", text=f"post {pid}")
        with posts.open("a") as f:
            f.write(p.model_dump_json() + "\n")
    handler = type("H", (server.Handler,), {"posts_path": posts, "labels_path": labels, "media_dir": media})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", labels
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read()


def post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_end_to_end_label_append_only(live):
    base, labels = live
    code, body = get(base + "/")
    assert code == 200 and b"Abiso" in body
    assert b"most common type, every barangay named (union), and earliest start to latest end" in body
    st = json.loads(get(base + "/api/state")[1])
    assert (st["total"], st["labelled"]) == (2, 0)
    assert len(st["barangays"]) == 30 and {"opportunity", "person", "interesting", "rss"} <= set(st["quick_tags"])

    code, out = post(base + "/api/label", {"post_id": "1", "type": "not_disruption", "tags": ["person"]})
    assert code == 200 and (out["labelled"], out["total"]) == (1, 2)
    code, out = post(base + "/api/label", {"post_id": "1", "type": "power", "barangays": ["Basak"]})
    assert code == 200 and out["labelled"] == 1

    lines = labels.read_text().splitlines()
    assert len(lines) == 2  # relabel appends, never rewrites
    assert latest_labels(labels)["1"]["type"] == "power"

    code, out = post(base + "/api/label", {"post_id": "1", "type": "power", "barangays": ["Cebu City"]})
    assert code == 400 and "not Lapu-Lapu" in out["error"]
    assert len(labels.read_text().splitlines()) == 2  # a rejected label writes nothing


def test_media_served_and_traversal_blocked(live):
    base, _ = live
    assert get(base + "/media/a.jpg") == (200, b"jpg")
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/media/..%2Fposts.jsonl")
    assert e.value.code == 404


def test_no_model_output_reaches_the_page(live):
    """The page never receives the scored systems' Events (Gemma/Gemini compile output).
    Protocol amendment, Carl 2026-10-08: Claude Haiku label DRAFTS (data/labels/drafts.jsonl) may be
    shown for review; they are Label-shaped, a different model family, and saved as source=haiku_reviewed."""
    base, _ = live
    st = json.loads(get(base + "/api/state")[1])
    assert set(st) == {"posts", "labels", "total", "labelled", "barangays", "types", "statuses", "quick_tags", "drafts"}
    assert all("event_id" not in d and "model" not in d for d in st["drafts"].values())
    assert all(set(p) == set(Post.model_fields) for p in st["posts"])
