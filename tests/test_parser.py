"""Parser tests against real gumtree.com.au pages captured 2026-09-11 (tests/fixtures)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.parser import (
    SelectorsMatchedNothing,
    build_item,
    classify,
    extract_app_data,
    parse_listing_page,
    parse_posted_at,
    parse_price,
    parse_search_app_data,
    parse_search_dom,
    parse_search_page,
)

FIXTURES = Path(__file__).parent / "fixtures"
# 2026-09-11 22:00 Sydney (AEST, +10:00) = 12:00 UTC. The rtx4070 page was captured that evening.
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
SRC = "https://www.gumtree.com.au/s-rtx+4070/k0"


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# APP_DATA path (primary)
# --------------------------------------------------------------------------- #
def test_app_data_search_page_parses_all_24_listings():
    items, meta = parse_search_app_data(load_json("rtx4070_search.json"), source_url=SRC, page=1, now=NOW)
    assert len(items) == 24
    assert len({it["id"] for it in items}) == 24
    assert meta.number_found == 33
    assert meta.next_page_url == "https://www.gumtree.com.au/s-rtx+4070/page-2/k0"
    assert meta.is_last_page is False
    assert meta.zero_results is False
    assert meta.source == "app_data"


def test_app_data_first_listing_fields():
    items, _ = parse_search_app_data(load_json("rtx4070_search.json"), source_url=SRC, now=NOW)
    it = items[0]
    assert it["id"] == "1344519187"
    assert it["title"] == "ASUS TUF Gaming RTX 4070 Ti 12GB (OC Edition)"
    assert it["price"] == 950 and isinstance(it["price"], int)
    assert it["priceText"] == "$950"
    assert it["priceType"] == "NEGOTIABLE" and it["isNegotiable"] is True
    assert it["currency"] == "AUD"
    assert (it["suburb"], it["area"], it["state"]) == ("Dural", "Hornsby Area", "NSW")
    assert it["location"] == "Dural, NSW"
    assert it["postedAt"] == "8 hours ago"
    assert it["postedAtIso"] == "2026-09-11T14:00:00+10:00"
    assert it["url"] == "https://www.gumtree.com.au/web/listing/components/1344519187"
    assert it["category"] == "components"
    assert it["imageUrl"].startswith("https://images.gumtree.com.au/")
    assert len(it["imageUrls"]) == 2
    assert it["snippet"].startswith("ASUS TUF Gaming RTX 4070 Ti (OC Edition)")
    assert it["isWanted"] is False and it["isFree"] is False and it["isPromoted"] is False
    assert it["sellerType"] is None
    assert it["kind"] == "item" and it["priceable"] is True
    assert it["searchUrl"] == SRC and it["page"] == 1
    assert it["scrapedAt"] == "2026-09-11T12:00:00+00:00"
    assert "description" not in it  # only with includeDescription


def test_app_data_absolute_dates_and_categories():
    items, _ = parse_search_app_data(load_json("rtx4070_search.json"), source_url=SRC, now=NOW)
    by_id = {it["id"]: it for it in items}
    assert by_id["1344486171"]["postedAt"] == "09/09/2026"
    assert by_id["1344486171"]["postedAtIso"] == "2026-09-09"
    assert by_id["1344486171"]["category"] == "desktops"
    assert by_id["1344466910"]["priceType"] == "FIXED" and by_id["1344466910"]["isNegotiable"] is False
    assert by_id["1344204894"]["category"] == "other-electronics-computers"


def test_top_ads_come_first_and_are_flagged_promoted():
    items, meta = parse_search_app_data(load_json("exercise_bike_sydney_search.json"), source_url=SRC, now=NOW)
    assert len(items) == 5  # 2 top + 3 main in the reduced fixture
    assert [it["isPromoted"] for it in items] == [True, True, False, False, False]
    assert items[0]["isFeatured"] is True and items[0]["id"] == "1344417432"
    assert meta.number_found == 195 and meta.last_page == 9
    yesterday = next(it for it in items if it["postedAt"] == "Yesterday")
    assert yesterday["postedAtIso"] == "2026-09-10"


def test_wanted_swap_and_free_rows_are_kept_but_not_priceable():
    raw_rows = [
        {"id": "1", "title": "Wanted: SWAP/TRADE Corsair Vengeance LPX 32GB", "priceText": "", "priceType": "SWAP_TRADE",
         "isWanted": True, "url": "/web/listing/components/1", "age": "Yesterday"},
        {"id": "2", "title": "Wanted: FREE Removal - Old/Broken Desktop Computer", "priceText": "", "priceType": "GIVE_AWAY",
         "isWanted": True, "isFree": True, "url": "/web/listing/components/2"},
        {"id": "3", "title": "Wanted: WTB - DDR4 Ram 16 and 32 kits", "priceText": "$50", "priceType": "NEGOTIABLE",
         "isWanted": True, "url": "/web/listing/components/3"},
        {"id": "4", "title": "Old monitor", "priceText": "", "priceType": "GIVE_AWAY", "isFree": True, "url": "/web/listing/monitors/4"},
    ]
    items, _ = parse_search_app_data({"results": {"main": raw_rows}, "searchMeta": {}}, source_url=SRC, now=NOW)
    assert [it["kind"] for it in items] == ["wanted", "wanted", "wanted", "free"]
    assert all(it["priceable"] is False for it in items)
    assert items[0]["isSwap"] is True and items[0]["price"] is None
    assert items[2]["price"] == 50 and items[2]["isWanted"] is True
    assert items[3]["isFree"] is True and items[3]["price"] is None


def test_rows_with_no_title_and_no_price_are_never_emitted():
    rows = [
        {"id": "1", "title": "", "priceText": "", "url": "/web/listing/x/1"},
        {"id": "", "title": "no id", "priceText": "$5"},
        {"id": "3", "title": "", "priceText": "$5", "url": "/web/listing/x/3"},  # price but no title: kept
    ]
    items, _ = parse_search_app_data({"results": {"main": rows}, "searchMeta": {}}, source_url=SRC, now=NOW)
    assert [it["id"] for it in items] == ["3"]


def test_seller_type_flags():
    rows = [
        {"id": "1", "title": "Car", "priceText": "$1", "isPostedByCarDealer": True, "isB2CPlus": True},
        {"id": "2", "title": "Shop item", "priceText": "$1", "isB2CPlus": True},
        {"id": "3", "title": "Private", "priceText": "$1"},
    ]
    items, _ = parse_search_app_data({"results": {"main": rows}, "searchMeta": {}}, source_url=SRC, now=NOW)
    assert [it["sellerType"] for it in items] == ["dealer", "business", None]


# --------------------------------------------------------------------------- #
# DOM fallback path
# --------------------------------------------------------------------------- #
def test_dom_fallback_on_real_cards_skips_sponsored_blocks():
    html = load_text("rtx4070_page.html")
    assert "fuse-ads" in html  # the sponsored block is present in the fixture
    items, meta = parse_search_dom(html, source_url=SRC, page=1, now=NOW)
    assert len(items) == 6
    assert [it["id"] for it in items][:3] == ["1344519187", "1344486171", "1344466910"]
    first = items[0]
    assert first["price"] == 950 and first["priceType"] == "NEGOTIABLE" and first["isNegotiable"] is True
    assert first["location"] == "Dural, NSW" and first["suburb"] == "Dural" and first["state"] == "NSW"
    assert first["postedAtIso"] == "2026-09-11T14:00:00+10:00"
    assert first["url"] == "https://www.gumtree.com.au/web/listing/components/1344519187"
    assert first["category"] == "components"
    alienware = items[2]
    assert alienware["price"] == 2400 and alienware["priceType"] == "FIXED" and alienware["isNegotiable"] is False
    assert meta.next_page_url == "https://www.gumtree.com.au/s-rtx+4070/page-2/k0"
    assert meta.number_found == 33
    assert meta.source == "dom"


def test_parse_search_page_prefers_app_data_when_embedded():
    search = load_json("rtx4070_search.json")
    html = (
        "<html><head><title>rtx 4070 | Gumtree Australia Local Classifieds</title>"
        "<script>(function() {window.APP_DATA = " + json.dumps({"search": search}) + ";})();</script></head>"
        + load_text("rtx4070_page.html")
    )
    assert extract_app_data(html)["search"]["searchMeta"]["numberFound"] == 33
    items, meta = parse_search_page(html, source_url=SRC, now=NOW)
    assert len(items) == 24 and meta.source == "app_data"


def test_parse_search_page_falls_back_to_dom_without_app_data():
    items, meta = parse_search_page(load_text("rtx4070_page.html"), source_url=SRC, now=NOW)
    assert len(items) == 6 and meta.source == "dom"


# --------------------------------------------------------------------------- #
# Listing page
# --------------------------------------------------------------------------- #
def test_listing_page_fields():
    extra = parse_listing_page(load_text("listing_1344519187.html"))
    assert extra["description"].startswith("ASUS TUF Gaming RTX 4070 Ti (OC Edition)\n\nUsed in good condition")
    assert extra["description"].endswith("recommended minimum 750w power supply")
    assert extra["price"] == 950 and extra["priceType"] == "NEGOTIABLE" and extra["currency"] == "AUD"
    assert extra["condition"] == "Used"
    assert (extra["suburb"], extra["state"], extra["postcode"]) == ("Dural", "NSW", "2158")
    assert extra["sellerType"] == "private"
    assert extra["categoryName"] == "Components"
    assert extra["listingStatus"] == "ACTIVE"
    # Personal data on the listing page is not extracted.
    assert not any(k in extra for k in ("sellerName", "name", "phone", "profileUrl"))


def test_listing_page_empty_html_is_empty_dict():
    assert parse_listing_page("") == {}
    assert parse_listing_page("<html><body>nothing here</body></html>") == {}


# --------------------------------------------------------------------------- #
# Field parsers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [("$950", 950), ("$1,800", 1800), ("$19.99", 19.99), ("$8,099", 8099), ("Swap/Trade", None), ("Free", None), ("", None), (None, None),
     ("$950 negotiable", 950)],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("8 hours ago", "2026-09-11T14:00:00+10:00"),
        ("49 minutes ago", "2026-09-11T21:11:00+10:00"),
        ("Just now", "2026-09-11T22:00:00+10:00"),
        ("Yesterday", "2026-09-10"),
        ("2 days ago", "2026-09-09"),
        ("1 week ago", "2026-09-04"),
        ("09/09/2026", "2026-09-09"),
        ("27/04/2023", "2023-04-27"),
        ("31/02/2026", None),
        ("", None),
        (None, None),
        ("sometime", None),
    ],
)
def test_parse_posted_at(raw, expected):
    assert parse_posted_at(raw, NOW) == expected


@pytest.mark.parametrize(
    "title,price_type,is_wanted,is_free,expected",
    [
        ("ASUS TUF RTX 4070 Ti", "NEGOTIABLE", False, False, "item"),
        ("Wanted: RTX 4070", None, False, False, "wanted"),
        ("WTB 4070 super", None, False, False, "wanted"),
        ("Looking for a gaming pc", None, False, False, "wanted"),
        ("RTX 4070", "FIXED", True, False, "wanted"),  # Gumtree flag wins even without the word
        ("Trade my 4070 for 4080", "SWAP_TRADE", False, False, "swap"),
        ("Old TV", "GIVE_AWAY", False, True, "free"),
        ("RTX 3080 faulty - for parts", "FIXED", False, False, "dead"),
        ("Laptop not working, spares or repairs", "FIXED", False, False, "dead"),
        ("Ryzen 7 + B650 + 32GB combo", "FIXED", False, False, "bundle"),
        ("Wanted: gaming PC 7800X3D combo", "FIXED", False, False, "wanted"),  # wanted beats bundle
        ("Electric,ian services", "FIXED", False, False, "item"),  # in-word punctuation is normalised
    ],
)
def test_classify(title, price_type, is_wanted, is_free, expected):
    assert classify(title, price_type, is_wanted, is_free) == expected


def test_build_item_infers_price_type_for_dom_rows():
    common = dict(id="1", title="x", suburb=None, area=None, state=None, posted_raw=None, url=None, image_url=None,
                  image_urls=None, snippet=None, is_wanted=False, is_free=False, is_promoted=False, is_featured=False,
                  is_urgent=False, is_price_drop=False, previous_price_text=None, seller_type=None, source_url=SRC,
                  page=1, now=NOW)
    assert build_item(price_text="$10", price_type=None, is_negotiable=True, **common)["priceType"] == "NEGOTIABLE"
    assert build_item(price_text="$10", price_type=None, is_negotiable=False, **common)["priceType"] == "FIXED"
    assert build_item(price_text="Swap/Trade", price_type=None, is_negotiable=False, **common)["priceType"] == "SWAP_TRADE"
    free = build_item(price_text="Free", price_type=None, is_negotiable=False, **common)
    assert free["priceType"] == "GIVE_AWAY" and free["isFree"] is True and free["kind"] == "free"


# --------------------------------------------------------------------------- #
# URL builder / input handling (no network)
# --------------------------------------------------------------------------- #
def test_build_search_url_forms():
    from src.main import build_search_url, config_from_input

    assert build_search_url("rtx 4070") == "https://www.gumtree.com.au/s-rtx+4070/k0"
    assert build_search_url(None, category="18552") == "https://www.gumtree.com.au/s-all/c18552"
    assert build_search_url(None, location="sydney") == "https://www.gumtree.com.au/s-all/l3003435"
    assert build_search_url("rtx 4070", category="18552") == "https://www.gumtree.com.au/s-all/rtx+4070/k0c18552"
    assert build_search_url("exercise bike", location="3003435") == "https://www.gumtree.com.au/s-all/exercise+bike/k0l3003435"
    assert build_search_url(None, "sydney", "18552") == "https://www.gumtree.com.au/s-all/all/c18552l3003435"
    assert (
        build_search_url("rtx 4070", "sydney", "18552", ad_type="wanted", sort_by="price_asc")
        == "https://www.gumtree.com.au/s-all/all/rtx+4070/k0c18552l3003435?sort=price_asc&ad=wanted"
    )
    with pytest.raises(ValueError):
        build_search_url(None)
    with pytest.raises(ValueError):
        build_search_url("x", location="melbourne")  # unknown alias must not be guessed

    cfg = config_from_input({"searchUrls": [{"url": "https://www.gumtree.com.au/s-components/c18552"}], "keyword": "ignored"})
    assert cfg.start_urls == ["https://www.gumtree.com.au/s-components/c18552"]
    with pytest.raises(ValueError):
        config_from_input({"searchUrls": [{"url": "https://www.gumtree.com/s-x/k0"}]})  # UK site
    with pytest.raises(ValueError):
        config_from_input({})
