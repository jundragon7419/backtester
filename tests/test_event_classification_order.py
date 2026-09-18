"""이벤트 분류 순서 ①~④ (P2-3, 불변 규칙 24)."""

from datetime import date, timedelta

from conftest import load_prices, price_row

from krx_backtester.data.adjust import build_adj_factors

DAY = date(2020, 1, 2)


def classify(con, series) -> dict:
    load_prices(con, series)
    build_adj_factors(con)
    rows = con.execute(
        "SELECT date, event_type, factor, rights_value_per_share, component_count FROM adj_factors ORDER BY date"
    ).fetchall()
    return {r[0]: dict(zip(("date", "event_type", "factor", "rights_value", "components"), r)) for r in rows}


def test_split_after_no_trade_is_share_change(con):
    """직전이 무거래 + 주식수 5배 + 계수 1/5 → ① share_change."""
    result = classify(con, [
        (DAY, price_row("000001", 5000, shares="100")),
        (DAY + timedelta(days=1), price_row("000001", 5000, no_trade=True, shares="100")),
        (DAY + timedelta(days=2), price_row("000001", 1010, 10, shares="500")),
    ])

    assert result[DAY + timedelta(days=2)]["event_type"] == "share_change"


def test_non_integer_split_ratio_is_share_change(con):
    """5:2 분할처럼 정수가 아닌 비율도 주식수가 맞으면 ①."""
    result = classify(con, [
        (DAY, price_row("000002", 1000, shares="100")),
        (DAY + timedelta(days=1), price_row("000002", 1000, no_trade=True, shares="100")),
        (DAY + timedelta(days=2), price_row("000002", 405, 5, shares="250")),  # 기준가 400 = 1000 × 0.4
    ])

    assert result[DAY + timedelta(days=2)]["event_type"] == "share_change"


def test_resume_without_share_change_is_restructure(con):
    """정지 후 재개인데 주식수가 그대로면 ② restructure (신라젠 형태)."""
    result = classify(con, [
        (DAY, price_row("000003", 12100, shares="100")),
        (DAY + timedelta(days=1), price_row("000003", 12100, no_trade=True, shares="100")),
        (DAY + timedelta(days=2), price_row("000003", 10850, 2470, shares="100")),
    ])

    assert result[DAY + timedelta(days=2)]["event_type"] == "restructure"


def test_share_change_not_matching_factor_is_restructure(con):
    """주식수는 변했지만 계수와 맞지 않으면 ② (SK텔레콤 형태)."""
    result = classify(con, [
        (DAY, price_row("000004", 309500, shares="100")),
        (DAY + timedelta(days=1), price_row("000004", 309500, no_trade=True, shares="100")),
        (DAY + timedelta(days=2), price_row("000004", 57900, 4500, shares="304")),  # 계수 0.1725 ≠ 1/3.04
    ])

    assert result[DAY + timedelta(days=2)]["event_type"] == "restructure"


def test_unchanged_shares_with_near_one_factor_is_not_share_change(con):
    """계수가 1에 가까워 오차 안에 들어와도 주식수가 그대로면 ①이 아니다."""
    result = classify(con, [
        (DAY, price_row("000005", 100000, shares="100")),
        (DAY + timedelta(days=1), price_row("000005", 100000, no_trade=True, shares="100")),
        (DAY + timedelta(days=2), price_row("000005", 101000, 400, shares="100")),  # 계수 1.006
    ])

    assert result[DAY + timedelta(days=2)]["event_type"] == "restructure"


def test_price_drop_without_no_trade_is_rights(con):
    """무거래 없이 계수 < 1 → ③ rights, 권리 가치 = 전일 종가 − 기준가."""
    result = classify(con, [
        (DAY, price_row("000006", 34950, shares="100")),
        (DAY + timedelta(days=1), price_row("000006", 30350, 3250, shares="100")),  # 기준가 27100
    ])

    event = result[DAY + timedelta(days=1)]
    assert event["event_type"] == "rights"
    assert event["rights_value"] == 34950 - 27100


def test_price_rise_without_no_trade_is_unverified(con):
    result = classify(con, [
        (DAY, price_row("000007", 10000, shares="100")),
        (DAY + timedelta(days=1), price_row("000007", 12000, -1000, shares="100")),  # 기준가 13000
    ])

    assert result[DAY + timedelta(days=1)]["event_type"] == "unverified"
