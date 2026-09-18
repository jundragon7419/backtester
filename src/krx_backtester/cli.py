"""수집·정제·탐색·검증·OOS·출력 흐름의 CLI 명령."""

import argparse
from datetime import date, datetime
from pathlib import Path

from krx_backtester.config import key_expiry_warning, load_config
from krx_backtester.data import collector, krx_client, normalize
from krx_backtester.data.schema import connect


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m krx_backtester")
    sub = parser.add_subparsers(dest="command", required=True)

    est = sub.add_parser("estimate", help="호출 없이 필요한 호출 수와 예상 소요 일수 출력")
    est.add_argument("--start", required=True, type=date.fromisoformat)
    est.add_argument("--end", required=True, type=date.fromisoformat)

    col = sub.add_parser("collect", help="기간 수집 실행")
    col.add_argument("--start", required=True, type=date.fromisoformat)
    col.add_argument("--end", required=True, type=date.fromisoformat)
    col.add_argument("--max-calls", type=int, default=None, help="이번 실행의 호출 예산(실패 호출 포함)")

    sub.add_parser("status", help="서비스별 적재 범위, 상태별 건수, 오늘 사용한 호출 수")
    sub.add_parser("normalize", help="raw_daily → prices 정규화 (전량 재생성, API 호출 없음)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = load_config()
    now = datetime.now(collector.KST)
    warning = key_expiry_warning(config, now.date())
    if warning:
        print(warning)

    con = connect(Path(config["data"]["db_path"]))
    col_cfg = config["collector"]

    if args.command == "estimate":
        est = collector.estimate(con, args.start, args.end, now, col_cfg["default_max_calls"])
        print(f"기간 {args.start} ~ {args.end} (주말 제외)")
        print(f"  호출 대상 평일: {est.trading_dates}일, 제공 시각 전이라 제외: {est.not_yet_available_dates}일")
        print(f"  일별매매 2개 + 지수: {est.daily_and_index_calls}회 (이미 ok·holiday 제외, 휴장일 포함 상한)")
        print(f"  종목기본정보: 확정 {est.base_info_calls_known}회 + 미적재 날짜 상한 {est.base_info_calls_max_unknown}회")
        print(f"  합계: {est.min_total} ~ {est.max_total}회")
        print(
            f"  예상 소요: {est.days(est.min_total)} ~ {est.days(est.max_total)}일 "
            f"(하루 {est.max_calls_per_day}회 기준)"
        )
        return 0

    if args.command == "collect":
        max_calls = args.max_calls if args.max_calls is not None else col_cfg["default_max_calls"]
        raw_dir = Path(config["data"]["raw_dir"])
        runner = collector.Collector(
            con,
            fetch=lambda service, bas_dd: krx_client.fetch(service, bas_dd, raw_dir),
            max_calls=max_calls,
            request_delay_seconds=col_cfg["request_delay_seconds"],
            max_consecutive_errors=col_cfg["max_consecutive_errors"],
        )
        result = runner.run(args.start, args.end)
        print(f"호출 {result.calls}회, 처리한 날짜 {result.dates_processed}일, 제공 시각 전 {result.not_yet_available_dates}일")
        if result.stop_reason:
            print(f"중단: {result.stop_reason}")
        return 1 if result.stop_reason else 0

    if args.command == "normalize":
        result = normalize.build_prices(con)
        print(f"prices {result.rows:,}행 생성")
        print(f"  정지·무거래(시가 0) {result.halted:,}행, 그중 종가가 움직인 행 {result.no_trade_with_close_move:,}")
        print(f"  관리종목 판별 가능(코스닥 {normalize.MANAGED_FROM} 이후) {result.managed_known:,}행")
        return 0

    info = collector.status(con, now.date())
    print("서비스별 적재 범위 (ok):")
    for service, first, last, n in info["ranges"]:
        print(f"  {service}: {first} ~ {last} ({n}일)")
    print("서비스별 상태 건수 (날짜별 최신 상태):")
    for service, st, n in info["counts"]:
        print(f"  {service} {st}: {n}")
    print(f"오늘({now.date()}, KST) 사용한 호출 수: {info['calls_today']}")
    return 0
