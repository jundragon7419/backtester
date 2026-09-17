"""first/last_seen, is_right_censored 산출."""

from datetime import date

from conftest import FakeKrx, daily_row, trading_day


def test_last_day_instrument_is_right_censored_and_earlier_exit_is_delisted(con, make_collector):
    fake = FakeKrx()
    trading_day(fake, "20260112", kospi=["000001", "000002"], kosdaq=["000009"])
    trading_day(fake, "20260113", kospi=["000001", "000002"], kosdaq=["000009"])
    trading_day(fake, "20260114", kospi=["000001"], kosdaq=["000009"])
    # 마지막 날 000001은 정지 형태 행(시가 0, 거래량 0)이어도 등장으로 셈
    fake.responses[("stk_bydd_trd", "20260114")] = [daily_row("000001", open_price="0", volume="0")]

    make_collector(fake).run(date(2026, 1, 12), date(2026, 1, 14))

    rows = {
        code: rest
        for code, *rest in con.execute(
            "SELECT code, first_seen_date, last_seen_date, delisted_date, is_right_censored FROM instruments"
        ).fetchall()
    }
    assert rows["000001"] == [date(2026, 1, 12), date(2026, 1, 14), None, True]
    assert rows["000002"] == [date(2026, 1, 12), date(2026, 1, 13), date(2026, 1, 13), False]
