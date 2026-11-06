"""The single-file frontend has no JS test runner, so these checks guard the wiring that
breaks silently when sections are moved: a view with no section, a loader with no view,
or a byId() for an element that no longer exists."""

import re
from pathlib import Path

INDEX = (Path(__file__).parent.parent / "src/portfolio/web/static/index.html").read_text(
    encoding="utf-8"
)
# Created by JS at runtime, so they are not in the static markup.
DYNAMIC_IDS = {"check-result-price", "check-result-signal", "today-hero", "today-attention",
               "today-near", "today-recent"}


def _array(name: str) -> list[str]:
    match = re.search(rf"var {name} = \[(.*?)\];", INDEX, re.S)
    assert match, name
    return re.findall(r"'([a-z]+)'", match.group(1))


def test_there_are_exactly_three_main_tabs():
    hrefs = re.findall(r'<a href="#(\w+)" id="link-\w+">', INDEX)
    assert hrefs == ["today", "stocks", "manage"]
    assert _array("TAB_NAMES") == ["today", "stocks", "manage"]


def test_every_view_has_a_section_and_a_loader():
    views = _array("ALL_VIEWS")
    loaders = set(re.findall(r"^  (\w+): ", INDEX.split("var LOADERS = {")[1].split("};")[0], re.M))
    for view in views:
        assert f'<section id="view-{view}"' in INDEX, view
        assert view in loaders, view


def test_every_second_level_segment_points_at_a_real_view():
    block = INDEX.split("var TAB_VIEWS = {")[1].split("};")[0]
    for view in re.findall(r"\['(\w+)', '", block):
        assert f'id="view-{view}"' in INDEX, view


def test_every_byid_target_exists_in_the_markup():
    html_ids = set(re.findall(r'\bid="([\w-]+)"', INDEX))
    used = set(re.findall(r"byId\('([\w-]+)'\)", INDEX))
    assert not (used - html_ids - DYNAMIC_IDS)


def test_up_down_colors_follow_the_market_convention():
    assert ".pos-tw,.neg-us{color:var(--red)}" in INDEX
    assert ".neg-tw,.pos-us{color:var(--green)}" in INDEX
    assert "'▲ '" in INDEX and "'▼ '" in INDEX  # direction never relies on colour alone


def test_old_seven_tab_hashes_are_gone():
    assert "link-signals" not in INDEX and "tab-watchlist" not in INDEX
