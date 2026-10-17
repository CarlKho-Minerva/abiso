from datetime import datetime

from abiso.compile import ISSUER_HINTS
from collect import sites
from collect.issuers import ISSUER_BY_PAGE
from collect.store import POSTS, load_posts


def test_every_stored_page_has_a_known_issuer():
    pages = {p.page for p in load_posts(POSTS)}
    assert pages <= ISSUER_BY_PAGE.keys(), pages - ISSUER_BY_PAGE.keys()
    assert set(ISSUER_BY_PAGE.values()) <= ISSUER_HINTS.keys()
from collect.timeparse import PH

NOW = datetime(2026, 10, 4, 10, 0, tzinfo=PH)

MCWD_HTML = """<div id="other_region"><h3 class="t-Card-title">IGNORE</h3><div class="t-Card-desc"><p>x</p></div></div>
<div class="t-Card-info">January 01, 2026</div>
<div id="water_interruption_id">
<li><h3 class="t-Card-title">INTERRUPTION</h3><h4>#CARD_SUBTITLE#</h4><div class="t-Card-body">
<div class="t-Card-desc"><div class="is-markdownified"><p>EWSI,All pumps in Lapu-Lapu City are not operational due to MECO &#x2F; 4Oct,7am-12nn &#x2F; AA: Whole area of Lapu-Lapu City</p>
</div></div>
        <div class="t-Card-info">October 04, 2026</div></div></li>
<li><h3 class="t-Card-title">EMERGENCY</h3><div class="t-Card-body">
<div class="t-Card-desc"><div class="is-markdownified"><p>EWSI,PWRI-Mambaling pumps shut off/AA:Mambaling,BasakSanNicolas</p>
</div></div>
        <div class="t-Card-info">October 03, 2026</div></div></li>
</div>"""


def test_mcwd_cards_reads_only_the_interruption_region():
    cards = sites.mcwd_cards(MCWD_HTML)
    assert [c["category"] for c in cards] == ["INTERRUPTION", "EMERGENCY"]
    assert cards[0]["text"].startswith("EWSI,All pumps in Lapu-Lapu City are not operational due to MECO / 4Oct")
    assert cards[1]["date"] == "October 03, 2026"


def test_mcwd_select_quota_keeps_page_order_and_negatives():
    cards = [{"text": (f"Lapu-Lapu City {i}" if i % 2 == 0 else f"BasakSanNicolas {i}"), "date": str(i)}
             for i in range(20)]
    chosen = sites.mcwd_select(cards, 8)
    assert len(chosen) == 8 and sum("Lapu" in c["text"] for c in chosen) == 6
    dates = [int(c["date"]) for c in chosen]
    assert dates == sorted(dates)  # page order (newest first) is kept


def test_mcwd_select_when_few_lapu_cards_fills_with_others():
    cards = [{"text": "Lapu-Lapu", "date": "0"}] + [{"text": "Talisay", "date": str(i)} for i in range(1, 10)]
    assert len(sites.mcwd_select(cards, 5)) == 5


def test_mcwd_posts_keep_the_date_out_of_posted_at():
    cards = sites.mcwd_cards(MCWD_HTML)[:1]
    p = sites.mcwd_posts(cards, NOW)[0]
    assert p.posted_at is None and p.page == "mcwd.gov.ph"
    assert p.text.endswith("[date as shown on the site, no time given: October 04, 2026]")
    assert p.post_id == sites.mcwd_posts(cards, NOW)[0].post_id  # stable across runs, so dedupe works


PAGASA_HTML = """<div class="tab-content">
<div id="rainfalls" class="tab-pane fade in active"><div>As of today, there is no Heavy Rainfall Warning Issued.</div></div>
<div id="thunderstorms" class="tab-pane fade in"><div>Thunderstorm Advisory No. 3  #VISPRSD<br />
Issued at 08:52 AM 04 October 2026<br /><br />Moderate to Heavy rainshowers over #Cebu(Medellin).</div></div>
<div id="special-forecasts" class="tab-pane fade in"><div>pdf</div></div>"""


def test_pagasa_skips_no_warning_and_reads_issue_time():
    items = sites.pagasa_items(PAGASA_HTML)
    assert len(items) == 1 and items[0]["tab"] == "thunderstorms"
    assert items[0]["issued"] == datetime(2026, 10, 4, 8, 52, tzinfo=PH)


def test_meco_post_from_wordpress_gmt():
    item = {"id": 12008, "date_gmt": "2026-10-01T23:56:38", "link": "https://mecomactan.com/x/",
            "title": {"rendered": "OCTOBER 01, 2026 OUTAGE NOTICES"},
            "content": {"rendered": '<p><img class="a" src="https://mecomactan.com/wp-content/uploads/2026/10/1.jpg" alt="" /></p>'}}
    p = sites.meco_posts([item], NOW)[0]
    assert p.posted_at.isoformat() == "2026-10-02T07:56:38+08:00"
    assert p.post_id == "meco:wp:12008" and p.media[0].url.endswith("/1.jpg") and p.media[0].alt is None
    assert "[the notices are in the attached images]" in p.text


def test_deped_title_only_posts():
    item = {"id": 7, "date_gmt": "2026-10-02T08:00:06", "link": "https://depedlapulapu.net.ph/download/x/",
            "title": {"rendered": "DM No. 663, s. 2026 &#8211; PISA"}}
    p = sites.deped_posts([item], NOW)[0]
    assert p.text.startswith("DM No. 663, s. 2026 – PISA") and p.posted_at.hour == 16
