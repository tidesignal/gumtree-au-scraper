"""The empty-result guard: an actor that returns nothing must not report success.

Why: downstream diffing (GONE = "was there yesterday, absent today") reads a
silently empty scrape as "everything sold at once" and writes that to disk.
"""

import pytest

from src.main import RunState, evaluate_run
from src.parser import SelectorsMatchedNothing, looks_blocked, parse_search_page

GUMTREE_SHELL = (
    "<html><head><title>rtx 4070 | Gumtree Australia Local Classifieds</title></head>"
    "<body><div id='react-root'><div class='search-results-page'>"
    "<h1 class='breadcrumbs__summary--enhanced'>33 Results: <strong>rtx 4070 </strong>in Australia</h1>"
    "<section class='search-results-page__user-ad-collection'></section>"  # cards renamed / missing
    "</div></div></body></html>"
)


def test_page_loaded_but_selectors_matched_nothing_raises():
    with pytest.raises(SelectorsMatchedNothing):
        parse_search_page(GUMTREE_SHELL, source_url="https://www.gumtree.com.au/s-rtx+4070/k0")


def test_genuine_zero_results_page_is_not_an_error():
    search = {"results": {"main": [], "top": []}, "searchMeta": {"numberFound": 0, "zeroSearchResults": True, "pagination": {}}}
    items, meta = parse_search_page("<html></html>", source_url="x", app_data={"search": search})
    assert items == [] and meta.zero_results is True


def test_evaluate_run_refuses_success_with_zero_items():
    s = RunState(max_items=10)
    assert evaluate_run(s) is not None  # nothing happened at all
    s = RunState(max_items=10, empty_pages=1)
    assert "no listing matched the selectors" in evaluate_run(s)
    s = RunState(max_items=10, blocked_hits=3, failed_requests=1)
    msg = evaluate_run(s)
    assert "blocked" in msg and "failed" in msg


def test_evaluate_run_allows_genuine_empty_search_only_when_nothing_else_went_wrong():
    assert evaluate_run(RunState(max_items=10, zero_result_pages=1)) is None
    assert evaluate_run(RunState(max_items=10, zero_result_pages=1, empty_pages=1)) is not None
    assert evaluate_run(RunState(max_items=10, zero_result_pages=1, blocked_hits=1)) is not None


def test_evaluate_run_ok_with_items():
    assert evaluate_run(RunState(max_items=10, pushed=3, blocked_hits=2)) is None


def test_run_state_dedupes_and_caps():
    s = RunState(max_items=3)
    fresh = s.take([{"id": "a"}, {"id": "b"}, {"id": "a"}, {"id": "c"}, {"id": "d"}])
    assert [it["id"] for it in fresh] == ["a", "b", "c"]
    assert s.reached is True
    assert s.take([{"id": "e"}]) == []


@pytest.mark.parametrize(
    "status,title,html,expected",
    [
        (403, "", "<html><head></head><body></body></html>", True),
        (403, "Access denied", "<html>...Access has been denied to the requested page...</html>", True),
        (200, "Access denied", "<html>Peakhour</html>", True),
        (200, "", "<html>" + "x" * 100 + "</html>", True),  # empty title + tiny body = challenge shell
        (200, "rtx 4070 | Gumtree Australia Local Classifieds", "<html>" + "x" * 10000 + "</html>", False),
        (None, "rtx 4070 | Gumtree Australia Local Classifieds", "<html>" + "x" * 10000 + "</html>", False),
    ],
)
def test_looks_blocked(status, title, html, expected):
    assert looks_blocked(status, title, html) is expected
