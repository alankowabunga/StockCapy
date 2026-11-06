import pandas as pd
import pytest
from conftest import make_settings

from portfolio.repository import save_price_history
from portfolio.service import load_close_history
from portfolio.store import open_store


def _seed_closes(settings, symbol, closes):
    with open_store(settings.database_path) as connection:
        save_price_history(connection, symbol, closes)


def _daily_series(count, start="2026-01-01", step=1.0):
    index = pd.date_range(start, periods=count, freq="D")
    return pd.Series([100.0 + step * i for i in range(count)], index=index)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def test_close_history_is_empty_for_an_unknown_symbol(settings):
    assert load_close_history(settings, "MSFT", 180) == []


def test_close_history_returns_the_stored_closes_oldest_first(settings):
    _seed_closes(settings, "MSFT", _daily_series(3))
    points = load_close_history(settings, "MSFT", 180)
    assert [point.price_date for point in points] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert points[-1].close == pytest.approx(102.0)


def test_lookback_window_is_cut_from_the_newest_stored_date(settings):
    """The cache can be a few days stale; a window measured from today would drop it all."""
    _seed_closes(settings, "MSFT", _daily_series(40))
    points = load_close_history(settings, "MSFT", 5)
    assert [point.price_date for point in points] == [
        "2026-02-05", "2026-02-06", "2026-02-07", "2026-02-08", "2026-02-09",
    ]


def test_moving_average_is_filled_from_history_before_the_window(settings):
    _seed_closes(settings, "MSFT", _daily_series(80))
    points = load_close_history(settings, "MSFT", 5)
    assert points[-1].moving_average == pytest.approx(pd.Series(
        [100.0 + i for i in range(20, 80)]
    ).mean())


def test_moving_average_is_null_while_the_sixty_day_window_is_unfilled(settings):
    _seed_closes(settings, "MSFT", _daily_series(10))
    points = load_close_history(settings, "MSFT", 180)
    assert all(point.moving_average is None for point in points)


def test_a_nonsense_lookback_still_returns_the_newest_day(settings):
    _seed_closes(settings, "MSFT", _daily_series(4))
    points = load_close_history(settings, "MSFT", 0)
    assert [point.price_date for point in points] == ["2026-01-04"]
