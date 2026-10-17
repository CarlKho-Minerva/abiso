import shutil
import sys
from datetime import datetime

import pytest

from abiso.places import BARANGAYS, canonical
from abiso.schema import Post
from collect import facebook, website
from collect.store import append_posts, load_posts
from collect.timeparse import PH, from_epoch, parse_fb_time

LA = "America/Los_Angeles"
# 2026-10-03 18:58 PDT == 2026-10-04 09:58 +08:00
NOW = datetime(2026, 10, 4, 9, 58, tzinfo=PH)


# --- timestamps -------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("15h", datetime(2026, 10, 3, 18, 58, tzinfo=PH)),
    ("15 hours ago", datetime(2026, 10, 3, 18, 58, tzinfo=PH)),
    ("an hour ago", datetime(2026, 10, 4, 8, 58, tzinfo=PH)),
    ("5m", datetime(2026, 10, 4, 9, 53, tzinfo=PH)),
    ("Just now", NOW),
    # absolute strings are in the viewer's zone (LA, PDT = UTC-7): 15:00 PDT = 06:00 next day PH
    ("Yesterday at 3:00 PM", datetime(2026, 10, 3, 6, 0, tzinfo=PH)),
    ("August 5 at 9:30 AM", datetime(2026, 8, 6, 0, 30, tzinfo=PH)),
    ("June 27, 2019 at 1:05 PM", datetime(2019, 6, 28, 4, 5, tzinfo=PH)),
])
def test_parse_fb_time_resolves(raw, expected):
    assert parse_fb_time(raw, NOW, LA) == expected


@pytest.mark.parametrize("raw", ["August 5", "June 27, 2019", "2d", "Yesterday", "", "sometime"])
def test_parse_fb_time_refuses_to_invent_a_time_of_day(raw):
    assert parse_fb_time(raw, NOW, LA) is None


def test_month_without_year_in_the_future_means_last_year():
    assert parse_fb_time("December 30 at 8:00 AM", NOW, LA).year == 2025


def test_epoch_is_ph_time():
    assert from_epoch(1791023105).isoformat() == "2026-10-03T18:25:05+08:00"


# --- facebook ---------------------------------------------------------------

def snap(**kw):
    base = {"cookie_names": ["wd", "dpr"], "login_button": True, "create_account": True, "wall": False}
    return base | kw


def test_assert_logged_out_passes_on_logged_out_ui():
    facebook.assert_logged_out(snap())


@pytest.mark.parametrize("bad", [
    snap(cookie_names=["c_user", "xs"]),
    snap(cookie_names=["xs"]),
    snap(login_button=False, create_account=False),
])
def test_assert_logged_out_aborts_on_any_session_sign(bad):
    with pytest.raises(facebook.LoggedInError):
        facebook.assert_logged_out(bad)


ART = {
    "id": "pfbid02YUk", "numeric_id": "122228981816564979",
    "permalink": "https://www.facebook.com/LLCInformationOffice/posts/pfbid02YUk",
    "raw_time": "15h", "text": "ABISO: walay klase ugma", "fallback_text": None,
    "images": [{"src": "https://scontent.example/x.jpg", "alt": None}],
    "creation_time": "1791023105", "see_more": False,
}


def test_to_post_prefers_epoch_and_numeric_id():
    p = facebook.to_post("LLCInformationOffice", ART, NOW, LA)
    assert p.post_id == "122228981816564979"
    assert p.posted_at.isoformat() == "2026-10-03T18:25:05+08:00"
    assert p.capture_method == "dom" and p.media[0].url.endswith("x.jpg")


def test_to_post_keeps_raw_time_in_text_when_unresolvable():
    art = ART | {"creation_time": None, "numeric_id": None, "raw_time": "August 5"}
    p = facebook.to_post("LLCInformationOffice", art, NOW, LA)
    assert p.post_id == "pfbid02YUk"
    assert p.posted_at is None
    assert p.text.endswith("[timestamp as shown, unresolved: August 5]")


def test_permalink_pass_fills_text_time_and_id(monkeypatch):
    feed = {"pfbid0A": {"id": "pfbid0A", "permalink": "https://www.facebook.com/P/posts/pfbid0A", "see_more": True,
                        "text": "CTMS TRAFFIC ADVISORY (CTM", "creation_time": None, "numeric_id": None, "images": []},
            "123": {"id": "123", "permalink": "https://x/123", "see_more": False, "text": "complete",
                    "creation_time": "1791023105", "numeric_id": "123", "images": []}}
    full = {"id": "pfbid0A", "see_more": False, "text": "CTMS TRAFFIC ADVISORY (CTMS) full text",
            "creation_time": "1791000000", "numeric_id": "999", "images": [{"src": "s", "alt": None}]}
    visited = []
    monkeypatch.setattr(facebook.time, "sleep", lambda s: None)
    monkeypatch.setattr(facebook.dia, "set_url", lambda tab, url: visited.append(url))
    monkeypatch.setattr(facebook, "_snapshot", lambda tab: snap(url="https://www.facebook.com/P/posts/pfbid0A",
                                                               closable_dialog=False, articles=[full]))
    assert facebook.enrich_from_permalinks("tab", feed) == 1
    assert visited == ["https://www.facebook.com/P/posts/pfbid0A"]  # complete posts are not revisited
    got = feed["pfbid0A"]
    assert (got["text"], got["creation_time"], got["numeric_id"], len(got["images"])) == (full["text"], "1791000000", "999", 1)


def test_permalink_pass_stops_at_login_redirect(monkeypatch):
    feed = {k: {"id": k, "permalink": f"https://x/{k}", "see_more": True, "text": "t", "images": []} for k in "ab"}
    calls = []
    monkeypatch.setattr(facebook.time, "sleep", lambda s: None)
    monkeypatch.setattr(facebook.dia, "set_url", lambda tab, url: calls.append(url))
    monkeypatch.setattr(facebook, "_snapshot", lambda tab: snap(url="https://www.facebook.com/login/?next=x", wall=True,
                                                               closable_dialog=False, articles=[]))
    assert facebook.enrich_from_permalinks("tab", feed) == 0
    assert len(calls) == 1


@pytest.mark.parametrize("raw, clean", [
    ("#OneLapuLapu See less", "#OneLapuLapu"),
    ("Walay klase ugma… See more", "Walay klase ugma"),
    ("Brownout sa Basak\nSee more", "Brownout sa Basak"),
    ("Please see more details below.", "Please see more details below."),  # the city's own words stay
])
def test_strip_toggle(raw, clean):
    assert facebook.strip_toggle(raw) == clean


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("osascript"), reason="needs osascript")
def test_applescript_quoting_round_trips_js_source():
    from collect import dia
    src = 'a.replace(/\\s*(?:…\\s*)?See more$/, "") + \'\\n\' + "Engaño"\n\tdone'
    assert dia._osa(f"return {dia._q(src)}") == src


# --- website ----------------------------------------------------------------

POSTVIEW = """<html><body><div class="pages-banner">
<span class="display-5 fw-bold text-uppercase">  SKYWAY UPDATE  </span>
<img class="card-img-top" src="https://lapulapucity.gov.ph/storage/compressed/50/News/a b.jpg" alt="Image Posted for Mayor&#039;s Trophies">
<div class="col-md-8"><p><h1>SKYWAY UPDATE</h1><div dir="auto">Gipahigayon&nbsp;namu ang meeting.</div></p></div>
</div><footer>x</footer></body></html>"""

ARTICLE = """<div class="pages-banner"><img src="https://lapulapucity.gov.ph/storage/x.jpg" alt="Image Posted for Meeting">
<span class="h1">Meeting</span><p><i class="fa fa-calendar"></i>
   28 September 2023
</p><span class="text-justify"><p>PRESS RELEASE</p><p>Barangay Caohagan was recognized.</p></span></div><footer>"""


def test_item_links_sorted_and_deduped():
    html = '<a href="postview/10"><a href="https://lapulapucity.gov.ph/postview/2"><a href="articles/13"><a href="postview/2">'
    assert website.item_links(html) == ["articles/13", "postview/2", "postview/10"]


def test_parse_postview_has_no_date_and_drops_repeated_title():
    it = website.parse_item("postview/19", POSTVIEW)
    assert it["title"] == "SKYWAY UPDATE"
    assert it["body"] == "Gipahigayon namu ang meeting."
    assert it["shown_date"] is None
    assert it["images"][0][1] == "Image Posted for Mayor's Trophies"


def test_parse_article_keeps_date_in_text_not_posted_at():
    it = website.parse_item("articles/13", ARTICLE)
    p = website.to_post("articles/13", it, NOW)
    assert p.posted_at is None
    assert "[date as shown on the site, no time given: 28 September 2023]" in p.text
    assert p.post_id == "llcgov:articles:13" and p.capture_method == "website"


# --- store ------------------------------------------------------------------

def test_append_posts_dedupes_by_post_id(tmp_path):
    path = tmp_path / "posts.jsonl"
    p = Post(post_id="1", page="x", url="u", captured_at=NOW, capture_method="dom", text="a")
    assert append_posts([p, p], path) == (1, 1)
    assert append_posts([p.model_copy(update={"text": "changed"})], path) == (0, 1)
    assert [q.text for q in load_posts(path)] == ["a"]


def test_load_posts_names_the_bad_line(tmp_path):
    path = tmp_path / "posts.jsonl"
    path.write_text('{"post_id": "1"}\n')
    with pytest.raises(ValueError, match=":1:"):
        load_posts(path)


# --- places -----------------------------------------------------------------

def test_thirty_barangays_and_aliases():
    assert len(BARANGAYS) == 30 == len(set(BARANGAYS))
    assert canonical("Brgy. Punta Engano") == "Punta Engaño"
    assert canonical("caw oy") is None  # a space is not a hyphen: stay strict
    assert canonical("Cawoy") == "Caw-oy"
    assert canonical("Caohagan") == "Cawhagan"
    assert canonical("Sta. Rosa") == "Santa Rosa"
    assert canonical("Olango") is None
