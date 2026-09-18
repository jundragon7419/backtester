"""보정 계수 산출과 이벤트 분류: 무거래 구간 귀속 (P2-3)."""

from datetime import date, timedelta

import pytest
from conftest import insert_daily, load_prices, price_row

from krx_backtester.data.adjust import build_adj_factors
from krx_backtester.data.normalize import build_prices

DAY = date(2020, 1, 2)


def rows(con) -> list[dict]:
    cols = "date, code, factor, component_count, first_component_date, event_type, known_date"
    keys = [c.strip() for c in cols.split(",")]
    return [dict(zip(keys, r)) for r in con.execute(f"SELECT {cols} FROM adj_factors ORDER BY date").fetchall()]


def test_factors_inside_no_trade_run_are_multiplied_into_resume_day(con):
    """무거래 중 계수와 재개일 계수를 곱해 재개일에 귀속한다."""
    load_prices(con, [
        (DAY, price_row("000001", 1000, shares="100")),
        (DAY + timedelta(days=1), price_row("000001", 500, 0, no_trade=True, shares="100")),  # 기준가 500
        (DAY + timedelta(days=2), price_row("000001", 260, 10, shares="400")),  # 기준가 250 = 500 × 0.5
    ])
    build_adj_factors(con)

    (event,) = rows(con)
    assert event["date"] == DAY + timedelta(days=2)
    assert event["component_count"] == 2
    assert event["first_component_date"] == DAY + timedelta(days=1)
    assert event["factor"] == pytest.approx(0.25)  # 0.5 × 0.5
    assert event["event_type"] == "share_change"  # 주식수도 4배


def test_no_trade_event_alone_is_attributed_to_next_trading_day(con):
    load_prices(con, [
        (DAY, price_row("000002", 1000, shares="100")),
        (DAY + timedelta(days=1), price_row("000002", 500, 0, no_trade=True, shares="100")),
        (DAY + timedelta(days=2), price_row("000002", 510, 10, shares="100")),  # 기준가 500 = 직전 종가
    ])
    build_adj_factors(con)

    (event,) = rows(con)
    assert event["date"] == DAY + timedelta(days=2) and event["component_count"] == 1
    assert event["first_component_date"] == DAY + timedelta(days=1)
    assert event["factor"] == pytest.approx(0.5)


def test_event_without_later_trading_day_is_unattributed(con):
    days = [DAY + timedelta(days=i) for i in range(3)]
    for day in days:  # 거래일 달력 유지용
        insert_daily(con, day, "KOSPI", price_row("000009", 500))
    insert_daily(con, days[0], "KOSPI", price_row("000003", 1000))
    insert_daily(con, days[1], "KOSPI", price_row("000003", 500, 0, no_trade=True))
    insert_daily(con, days[2], "KOSPI", price_row("000003", 500, 0, no_trade=True))
    build_prices(con)
    build_adj_factors(con)

    event = next(r for r in rows(con) if r["code"] == "000003")
    assert event["event_type"] == "unattributed"
    assert event["date"] == days[1]


def test_known_date_is_attribution_day(con):
    load_prices(con, [
        (DAY, price_row("000004", 1000, shares="100")),
        (DAY + timedelta(days=1), price_row("000004", 500, 0, no_trade=True, shares="100")),
        (DAY + timedelta(days=2), price_row("000004", 510, 10, shares="100")),
    ])
    build_adj_factors(con)

    (event,) = rows(con)
    assert event["known_date"] == event["date"] == DAY + timedelta(days=2)


def test_summary_counts_by_type(con):
    load_prices(con, [
        (DAY, price_row("000005", 34950, shares="100")),
        (DAY + timedelta(days=1), price_row("000005", 30350, 3250, shares="100")),
    ])
    result = build_adj_factors(con)

    assert result.events == 1 and result.by_type == {"rights": 1}
    assert result.multi_component == 0
