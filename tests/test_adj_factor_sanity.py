"""보정 계수 타당성 검사 (P2-2)."""

from datetime import date, timedelta

import pytest
from conftest import insert_daily, load_prices, price_row

from krx_backtester.data.adjust import PricesOutOfDate, build_adj_factors
from krx_backtester.data.normalize import build_prices

DAY = date(2020, 1, 2)


def events(con) -> dict:
    cols = ("date, code, factor, base_price, prev_close, prev_date, component_count, "
            "first_component_date, event_type, rights_value_per_share, known_date")
    keys = [c.strip() for c in cols.split(",")]
    return {r[0]: dict(zip(keys, r)) for r in con.execute(f"SELECT {cols} FROM adj_factors ORDER BY date").fetchall()}


def test_normal_days_have_no_event(con):
    load_prices(con, [(DAY, price_row("000001", 1000)), (DAY + timedelta(days=1), price_row("000001", 1100, 100))])
    build_adj_factors(con)

    assert events(con) == {}


def test_day_after_no_trade_row_uses_previous_row_close(con):
    """기세로 종가가 움직인 다음 날도 계수 1. 직전 비정지 종가로 나눴다면 이벤트로 잡혔을 케이스."""
    load_prices(con, [
        (DAY, price_row("000002", 1000)),
        (DAY + timedelta(days=1), price_row("000002", 1050, 50, no_trade=True)),
        (DAY + timedelta(days=2), price_row("000002", 1080, 30)),  # 기준가 1050 = 직전 행 종가
    ])
    build_adj_factors(con)

    assert events(con) == {}


def test_factor_is_exact_and_auditable(con):
    load_prices(con, [
        (DAY, price_row("000003", 5000)),
        (DAY + timedelta(days=1), price_row("000003", 5000, no_trade=True)),
        (DAY + timedelta(days=2), price_row("000003", 1010, 10)),  # 기준가 1000 = 5000 / 5
    ])
    build_adj_factors(con)

    event = events(con)[DAY + timedelta(days=2)]
    assert event["factor"] == pytest.approx(0.2)
    assert event["base_price"] == 1000 and event["prev_close"] == 5000
    assert event["prev_date"] == DAY + timedelta(days=1)


def test_one_tick_difference_is_excluded(con):
    # 15,000원대는 임시 호가표에서 개편 전 50원
    load_prices(con, [(DAY, price_row("000004", 15000)), (DAY + timedelta(days=1), price_row("000004", 15100, 50))])
    result = build_adj_factors(con)

    assert result.candidates == 1 and result.within_one_tick == 1
    assert events(con) == {}


def test_gap_in_series_is_excluded(con):
    days = [DAY + timedelta(days=i) for i in range(4)]
    for day in days:  # 거래일 달력을 만들기 위한 다른 종목
        insert_daily(con, day, "KOSPI", price_row("000009", 500))
    insert_daily(con, days[0], "KOSPI", price_row("000005", 1000))
    insert_daily(con, days[3], "KOSPI", price_row("000005", 300))  # 재상장: 기준가 300 ≠ 직전 종가 1000
    build_prices(con)
    result = build_adj_factors(con)

    assert result.prev_gap == 1
    assert events(con) == {}


def test_first_row_has_no_factor(con):
    load_prices(con, [(DAY, price_row("000006", 1000, 100))])
    build_adj_factors(con)

    assert events(con) == {}


def test_rebuild_is_idempotent(con):
    load_prices(con, [(DAY, price_row("000007", 5000)), (DAY + timedelta(days=1), price_row("000007", 1010, 10))])
    first = build_adj_factors(con)
    second = build_adj_factors(con)

    assert first == second
    assert con.execute("SELECT count(*) FROM adj_factors").fetchone()[0] == 1


def test_stale_prices_stops_with_message(con):
    load_prices(con, [(DAY, price_row("000008", 1000))])
    insert_daily(con, DAY + timedelta(days=1), "KOSPI", price_row("000008", 1100, 100))

    with pytest.raises(PricesOutOfDate):
        build_adj_factors(con)


def test_normalize_clears_stale_adj_factors(con):
    load_prices(con, [(DAY, price_row("000010", 5000)), (DAY + timedelta(days=1), price_row("000010", 1010, 10))])
    build_adj_factors(con)
    assert con.execute("SELECT count(*) FROM adj_factors").fetchone()[0] == 1

    build_prices(con)
    assert con.execute("SELECT count(*) FROM adj_factors").fetchone()[0] == 0
