"""수집기: 이어받기, 상태 분류, 호출 예산, 제공 시각, 종목기본정보 호출 조건."""

from datetime import date, datetime

from conftest import FakeKrx, daily_row, trading_day

from krx_backtester.data.collector import KST
from krx_backtester.data.krx_client import KrxResponse


def latest_status(con, service: str, d: date) -> tuple:
    return con.execute(
        "SELECT arg_max(status, rowid), arg_max(http_status, rowid), arg_max(message, rowid), "
        "arg_max(called_at, rowid) FROM collection_log WHERE service = ? AND bas_dd = ?",
        [service, d],
    ).fetchone()


def test_resume_after_stop_has_no_duplicate_calls(con, make_collector):
    fake = FakeKrx()
    for bas_dd in ("20260112", "20260113", "20260114"):
        trading_day(fake, bas_dd, kospi=["000001"], kosdaq=["000002"])

    first = make_collector(fake, max_calls=4).run(date(2026, 1, 12), date(2026, 1, 14))
    assert first.stop_reason and "예산" in first.stop_reason
    first_calls = list(fake.calls)

    fake.calls.clear()
    second = make_collector(fake).run(date(2026, 1, 12), date(2026, 1, 14))
    assert second.stop_reason is None

    ok_in_first = set(first_calls)  # 첫 실행 호출은 모두 성공 응답
    assert not ok_in_first & set(fake.calls)
    assert len(fake.calls) == len(set(fake.calls))

    fake.calls.clear()
    third = make_collector(fake).run(date(2026, 1, 12), date(2026, 1, 14))
    assert third.calls == 0 and fake.calls == []


def test_empty_responses_are_classified_as_holiday_not_yet_or_error(con, make_collector):
    fake = FakeKrx()
    # 2026-01-12: 두 시장 모두 빈 응답 → 휴장일, 지수는 호출하지 않음
    # 2026-01-13: 유가증권 인증 오류, 코스닥 빈 응답 → 둘 다 error (휴장일로 판정하지 않음)
    fake.responses[("stk_bydd_trd", "20260113")] = KrxResponse(401, '{"respMsg":"Unauthorized API Call","respCode":"401"}', None)
    # 2026-01-14: 유가증권만 데이터 → 코스닥 빈 응답은 error
    fake.responses[("stk_bydd_trd", "20260114")] = [daily_row("000001")]
    now = datetime(2026, 1, 15, 12, tzinfo=KST)  # 01-15 데이터는 01-16 08시 이후 제공

    result = make_collector(fake, now=now).run(date(2026, 1, 12), date(2026, 1, 15))

    assert latest_status(con, "stk_bydd_trd", date(2026, 1, 12))[0] == "holiday"
    assert latest_status(con, "ksq_bydd_trd", date(2026, 1, 12))[0] == "holiday"
    idx = latest_status(con, "kospi_dd_trd", date(2026, 1, 12))
    assert idx[0] == "holiday" and idx[3] is None
    assert ("kospi_dd_trd", "20260112") not in fake.calls

    stk13 = latest_status(con, "stk_bydd_trd", date(2026, 1, 13))
    assert stk13[:3] == ("error", 401, "Unauthorized API Call")
    assert latest_status(con, "ksq_bydd_trd", date(2026, 1, 13))[0] == "error"

    assert latest_status(con, "stk_bydd_trd", date(2026, 1, 14))[0] == "ok"
    assert latest_status(con, "ksq_bydd_trd", date(2026, 1, 14))[0] == "error"

    for service in ("stk_bydd_trd", "ksq_bydd_trd", "kospi_dd_trd"):
        assert latest_status(con, service, date(2026, 1, 15))[0] == "not_yet_available"
    assert not [c for c in fake.calls if c[1] == "20260115"]
    assert result.not_yet_available_dates == 1


def test_max_calls_stops_and_counts_failed_calls(con, make_collector):
    fake = FakeKrx()
    for bas_dd in ("20260112", "20260113", "20260114"):
        for service in ("stk_bydd_trd", "ksq_bydd_trd"):
            fake.responses[(service, bas_dd)] = KrxResponse(500, "server error", None)

    result = make_collector(fake, max_calls=4).run(date(2026, 1, 12), date(2026, 1, 14))

    assert result.calls == 4
    assert len(fake.calls) == 4
    assert "예산" in result.stop_reason
    logged = con.execute("SELECT count(*) FROM collection_log WHERE status = 'error' AND called_at IS NOT NULL").fetchone()[0]
    assert logged == 4  # 실패 호출 4회가 모두 예산을 소모, 셋째 날은 시작하지 않음


def test_no_call_before_next_weekday_0800_kst(con, make_collector):
    fake = FakeKrx()
    trading_day(fake, "20260115", kospi=["000001"], kosdaq=["000002"])  # 목요일
    trading_day(fake, "20260116", kospi=["000001"], kosdaq=["000002"])  # 금요일

    make_collector(fake, now=datetime(2026, 1, 16, 7, 59, tzinfo=KST)).run(date(2026, 1, 15), date(2026, 1, 15))
    assert fake.calls == []

    make_collector(fake, now=datetime(2026, 1, 17, 12, 0, tzinfo=KST)).run(date(2026, 1, 16), date(2026, 1, 16))
    assert fake.calls == []  # 금요일 데이터는 다음 평일(월) 08시 이후

    make_collector(fake, now=datetime(2026, 1, 16, 8, 0, tzinfo=KST)).run(date(2026, 1, 15), date(2026, 1, 15))
    assert ("stk_bydd_trd", "20260115") in fake.calls


def test_base_info_called_only_on_new_code_dates(con, make_collector):
    fake = FakeKrx()
    trading_day(fake, "20260112", kospi=["000001", "000003"], kosdaq=["000002"])
    trading_day(fake, "20260113", kospi=["000001", "000003"], kosdaq=["000002"])
    trading_day(fake, "20260114", kospi=["000001", "000003", "000004"], kosdaq=["000002"])

    make_collector(fake).run(date(2026, 1, 12), date(2026, 1, 14))

    base_calls = sorted(c for c in fake.calls if c[0].endswith("isu_base_info"))
    assert base_calls == [
        ("ksq_isu_base_info", "20260112"),
        ("stk_isu_base_info", "20260112"),
        ("stk_isu_base_info", "20260114"),
    ]
    assert con.execute("SELECT isu_std_cd FROM instruments WHERE code = '000004'").fetchone()[0] == "KR7000004000"
