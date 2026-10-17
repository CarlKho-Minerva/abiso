import json
from datetime import date, datetime

import pytest

from abiso import compile as C
from abiso.schema import DisruptionType, Media, Post, Status
from tests.fixtures.barangays import LAPULAPU_BARANGAYS as B

POSTED = datetime.fromisoformat("2026-10-03T19:40:00+08:00")   # a Saturday in Manila


def post(text="ABISO", media=None, posted_at=POSTED, pid="p1"):
    return Post(post_id=pid, page="LLCPIO", url=f"https://example.invalid/{pid}", posted_at=posted_at,
                captured_at=datetime.fromisoformat("2026-10-03T21:00:00+08:00"), text=text,
                media=media or [], capture_method="dom")


def ev(**kw):
    base = {"type": "power", "status": "scheduled",
            "areas": [{"place": "portions of Brgy. Basak", "detail": "Feeder 12"}],
            "start": "2026-10-04T08:00:00+08:00", "end": "2026-10-04T17:00:00+08:00",
            "all_day": False, "summary": "No power, Basak, Sun 8 AM to 5 PM", "confidence": 0.9}
    base.update(kw)
    return base


class Fake:
    """Returns queued replies in order and records what it was sent."""

    def __init__(self, *texts, supports_images=True):
        self.texts = list(texts)
        self.model = "fake-1"
        self.supports_images = supports_images
        self.calls = []

    def generate(self, system, turns, schema):
        self.calls.append({"system": system, "turns": turns, "schema": schema})
        return C.ModelReply(text=self.texts.pop(0), model=self.model, input_tokens=10, output_tokens=5)


def out(*events):
    return json.dumps({"events": list(events)})


# ---------------------------------------------------------------- happy path
def test_compile_fills_provenance_and_canonical_places():
    b = Fake(out(ev()))
    events = C.compile_post(post(), b, B)
    assert len(events) == 1
    e = events[0]
    assert e.type == DisruptionType.power and e.status == Status.scheduled
    assert e.source_post_ids == ["p1"] and e.model == "fake-1"
    assert [a.barangay for a in e.areas] == ["Basak"]
    assert e.areas[0].detail == "portions of Brgy. Basak; Feeder 12"
    assert e.event_id.startswith("ev_") and len(b.calls) == 1


def test_event_ids_are_deterministic_and_distinct():
    a = C.compile_post(post(), Fake(out(ev(), ev(start="2026-10-05T08:00:00+08:00",
                                                  end="2026-10-05T09:00:00+08:00"))), B)
    a2 = C.compile_post(post(), Fake(out(ev(), ev(start="2026-10-05T08:00:00+08:00",
                                                   end="2026-10-05T09:00:00+08:00"))), B)
    assert [x.event_id for x in a] == [x.event_id for x in a2]
    assert a[0].event_id != a[1].event_id


def test_times_normalised_to_manila():
    e = C.compile_post(post(), Fake(out(ev(start="2026-10-04T00:00:00Z", end=None))), B)[0]
    assert e.start.isoformat() == "2026-10-04T08:00:00+08:00"
    naive = C.compile_post(post(), Fake(out(ev(start="2026-10-04T08:00:00", end=None))), B)[0]
    assert naive.start.isoformat() == "2026-10-04T08:00:00+08:00"


def test_not_disruption_is_kept_not_dropped():
    nd = ev(type="not_disruption", areas=[{"place": "Basak", "detail": None}], start=None, end=None,
            summary="Fiesta greeting")
    events = C.compile_post(post("Happy fiesta!"), Fake(out(nd)), B)
    assert len(events) == 1 and events[0].type == DisruptionType.not_disruption
    assert events[0].areas == []   # a greeting naming a barangay is not an affected area


def test_unmatched_place_kept_as_detail():
    e = C.compile_post(post(), Fake(out(ev(areas=[{"place": "Feeder 12", "detail": None}]))), B)[0]
    assert len(e.areas) == 1 and e.areas[0].barangay is None and e.areas[0].detail == "Feeder 12"


def test_fenced_json_is_accepted():
    e = C.compile_post(post(), Fake("```json\n" + out(ev()) + "\n```"), B)
    assert len(e) == 1


# ---------------------------------------------------------------- retry, never drop
def test_invalid_then_valid_retries_once_with_the_error():
    b = Fake("not json", out(ev()))
    r = C.compile_post_detailed(post(), b, B)
    assert len(r.events) == 1 and len(r.replies) == 2
    retry_turns = b.calls[1]["turns"]
    assert [t.role for t in retry_turns] == ["user", "model", "user"]
    assert "failed validation" in retry_turns[2].text


@pytest.mark.parametrize("bad", [
    "not json",
    json.dumps({"events": []}),                                     # min one event
    json.dumps({"events": [ev(type="earthquake")]}),                 # unknown type
    json.dumps({"events": [ev(confidence=1.5)]}),
    json.dumps({"events": [ev(end="2026-10-04T07:00:00+08:00")]}),   # end before start
    json.dumps({"events": [{k: v for k, v in ev().items() if k != "areas"}]}),  # required field missing
])
def test_invalid_twice_raises(bad):
    b = Fake(bad, bad)
    with pytest.raises(C.CompileError) as ei:
        C.compile_post(post(), b, B)
    assert ei.value.post_id == "p1" and len(ei.value.replies) == 2


# ---------------------------------------------------------------- images
def test_image_post_sends_image_bytes(tmp_path):
    img = tmp_path / "sched.png"
    img.write_bytes(b"\x89PNG fake")
    b = Fake(out(ev()))
    r = C.compile_post_detailed(post(media=[Media(kind="image", path=str(img), alt="brownout list")]), b, B)
    sent = b.calls[0]["turns"][0]
    assert r.used_images and len(sent.images) == 1 and sent.images[0].data == b"\x89PNG fake"
    assert sent.images[0].mime == "image/png"
    assert "Image 1 alt text: brownout list" in sent.text


def test_text_only_backend_gets_alt_text_not_images(tmp_path):
    img = tmp_path / "sched.jpg"
    img.write_bytes(b"jpeg")
    b = Fake(out(ev()), supports_images=False)
    r = C.compile_post_detailed(post(media=[Media(kind="image", path=str(img), alt="Basak 8-5")]), b, B)
    assert not r.used_images and b.calls[0]["turns"][0].images == []
    assert "Basak 8-5" in b.calls[0]["turns"][0].text


def test_image_without_source_is_loud():
    with pytest.raises(C.MediaError):
        C.compile_post(post(media=[Media(kind="image")]), Fake(out(ev())), B)


def test_gemma_messages_format():
    g = C.GemmaVLLM()
    msgs = g._messages("SYS", [C.Turn("user", "hi", [C.ImagePart("image/png", b"x")])])
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1]["content"][1]["image_url"]["url"] == "data:image/png;base64,eA=="
    assert C.GemmaVLLM(supports_images=False)._messages("S", [C.Turn("user", "hi", [C.ImagePart("image/png", b"x")])])[1]["content"] == "hi"


# ---------------------------------------------------------------- dates
@pytest.mark.parametrize("word, expected", [
    ("ugma", date(2026, 10, 4)), ("bukas", date(2026, 10, 4)), ("tomorrow", date(2026, 10, 4)),
    ("karon", date(2026, 10, 3)), ("gahapon", date(2026, 10, 2)), ("sa makalawa", date(2026, 10, 5)),
    ("Lunes", date(2026, 10, 5)), ("monday", date(2026, 10, 5)), ("Sabado", date(2026, 10, 3)),
    ("Domingo", date(2026, 10, 4)), ("Biyernes", date(2026, 10, 9)), ("sometime", None),
])
def test_resolve_relative(word, expected):
    assert C.resolve_relative(word, date(2026, 10, 3)) == expected


def test_anchor_uses_manila_not_utc():
    # 17:30 UTC on Oct 3 is 01:30 on Oct 4 in Manila: "ugma" must be Oct 5.
    p = post(posted_at=datetime.fromisoformat("2026-10-03T17:30:00+00:00"))
    assert C.anchor_date(p) == date(2026, 10, 4)
    assert "ugma = 2026-10-05" in C.date_table(p).replace("tomorrow / bukas / ", "")


def test_date_table_unknown_posting_date():
    t = C.date_table(post(posted_at=None))
    assert "UNKNOWN" in t and "2026" not in t


def test_prompt_contains_date_table_and_barangays():
    text = C.build_user_text(post("Walay klase ugma"), B)
    assert "ugma = 2026-10-04" in text.replace("tomorrow / bukas / ", "")
    assert "Marigondon" in text and "Walay klase ugma" in text


# ---------------------------------------------------------------- schema + env
def test_output_schema_is_self_contained_and_strict():
    s = C.output_json_schema()
    assert "$ref" not in json.dumps(s) and "$defs" not in s
    item = s["properties"]["events"]["items"]
    assert set(item["required"]) == set(item["properties"])
    assert set(item["properties"]["areas"]["items"]["required"]) == {"place", "detail"}


def test_read_env_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ABISO_TEST_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text("OTHER=1\nexport ABISO_TEST_KEY='sekret'\n")
    assert C.read_env_key("ABISO_TEST_KEY", f) == "sekret"
    with pytest.raises(RuntimeError):
        C.read_env_key("MISSING_KEY_XYZ", f)
