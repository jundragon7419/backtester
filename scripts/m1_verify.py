"""M1 완료 기준 검증 (읽기 전용, API 호출 없음).

사용 (저장소 루트, PowerShell):
    .\\.venv\\Scripts\\python.exe scripts\\m1_verify.py
"""

from datetime import date
from pathlib import Path

import duckdb

from krx_backtester.config import load_config
from krx_backtester.data.collector import DAILY_SERVICES, INDEX_SERVICE, available_at, weekdays

FIRST_DATE = date(2010, 1, 4)
CASES = {"117930": "한진해운", "215600": "신라젠", "005930": "삼성전자"}


def check(label: str, passed: bool, detail: str = "") -> None:
    print(f"[{'통과' if passed else '실패'}] {label}{(' — ' + detail) if detail else ''}")


def main() -> None:
    config = load_config()
    con = duckdb.connect(config["data"]["db_path"], read_only=True)
    last_date = con.execute("SELECT max(date) FROM raw_daily").fetchone()[0]
    print(f"적재 마지막 날: {last_date}")

    latest = {
        (service, bas_dd): status
        for service, bas_dd, status in con.execute(
            "SELECT service, bas_dd, arg_max(status, rowid) FROM collection_log GROUP BY service, bas_dd"
        ).fetchall()
    }
    expected_dates = weekdays(FIRST_DATE, last_date)
    services = (*DAILY_SERVICES, INDEX_SERVICE)

    missing = [(s, d) for d in expected_dates for s in services if latest.get((s, d)) not in ("ok", "holiday")]
    check("전체 기간 적재 (평일 × 3개 서비스가 ok 또는 holiday)", not missing,
          f"평일 {len(expected_dates)}일, 미완료 {len(missing)}건" + (f" 예: {missing[:5]}" if missing else ""))

    errors = con.execute("SELECT count(*) FROM collection_log WHERE status = 'error'").fetchone()[0]
    remaining_errors = sum(1 for (s, d), st in latest.items() if st == "error")
    check("오류 잔존 없음", remaining_errors == 0, f"누적 오류 기록 {errors}건, 미해결 {remaining_errors}건")

    early = [
        (s, d, t) for s, d, t in con.execute(
            "SELECT service, bas_dd, called_at FROM collection_log WHERE called_at IS NOT NULL"
        ).fetchall() if t.replace(tzinfo=available_at(d).tzinfo) < available_at(d)
    ]
    check("D+1 08시(KST) 이전 요청 0건", not early, f"{len(early)}건" + (f" 예: {early[:3]}" if early else ""))

    by_status = con.execute(
        "SELECT status, count(*) FROM (SELECT service, bas_dd, arg_max(status, rowid) AS status "
        "FROM collection_log GROUP BY service, bas_dd) GROUP BY status ORDER BY status"
    ).fetchall()
    print("상태별 건수(서비스·날짜 기준):", dict(by_status))

    holidays = con.execute(
        "SELECT year(bas_dd), count(*) FROM collection_log WHERE service = 'stk_bydd_trd' AND status = 'holiday' "
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()
    counts = [n for _, n in holidays]
    check("휴장일 분포가 연 6~20일", all(6 <= n <= 20 for n in counts[:-1]),
          ", ".join(f"{y}:{n}" for y, n in holidays))

    rows, dates, codes = con.execute("SELECT count(*), count(DISTINCT date), count(DISTINCT code) FROM raw_daily").fetchone()
    print(f"raw_daily: {rows:,}행, {dates}일, {codes}종목")
    index_rows = con.execute("SELECT count(*), count(DISTINCT date) FROM raw_index").fetchone()
    print(f"raw_index: {index_rows[0]:,}행, {index_rows[1]}일")

    total, std_missing, delisted, censored = con.execute(
        "SELECT count(*), count(*) FILTER (isu_std_cd IS NULL), count(delisted_date), "
        "count(*) FILTER (is_right_censored) FROM instruments"
    ).fetchone()
    check("instruments 표준코드 누락 없음", std_missing == 0,
          f"종목 {total}, 표준코드 누락 {std_missing}, 폐지 {delisted}, 우측 검열 {censored}")

    for code, name in CASES.items():
        row = con.execute(
            "SELECT name, first_seen_date, last_seen_date, delisted_date, is_right_censored FROM instruments WHERE code = ?",
            [code],
        ).fetchone()
        print(f"사례 {name}({code}): {row}")

    gaps = con.execute(
        """
        WITH per_date AS (
            SELECT date, count(*) FILTER (market = 'KOSPI') AS kospi, count(*) FILTER (market = 'KOSDAQ') AS kosdaq
            FROM raw_daily GROUP BY date
        )
        SELECT date, kospi, kosdaq FROM per_date WHERE kospi = 0 OR kosdaq = 0 ORDER BY date
        """
    ).fetchall()
    check("거래일에 한 시장만 비어 있는 날 없음", not gaps, f"{len(gaps)}건" + (f" 예: {gaps[:5]}" if gaps else ""))

    drops = con.execute(
        """
        WITH per_date AS (SELECT date, count(*) AS n FROM raw_daily GROUP BY date),
        seq AS (SELECT date, n, lag(n) OVER (ORDER BY date) AS prev FROM per_date)
        SELECT date, prev, n FROM seq WHERE prev IS NOT NULL AND n < prev * 0.9 ORDER BY date
        """
    ).fetchall()
    check("전 거래일 대비 행 수 10% 이상 급감 없음", not drops, f"{len(drops)}건" + (f" {drops[:5]}" if drops else ""))

    print("\n지수명 변경 후보 (IDX_NM별 첫·마지막 등장일, 전체 구간이 아닌 것만):")
    first_day, last_day = con.execute("SELECT min(date), max(date) FROM raw_index").fetchone()
    for name, first, last, n in con.execute(
        "SELECT IDX_NM, min(date), max(date), count(*) FROM raw_index GROUP BY IDX_NM ORDER BY min(date), IDX_NM"
    ).fetchall():
        if first != first_day or last != last_day:
            print(f"  {name}: {first} ~ {last} ({n}일)")

    db_mb = Path(config["data"]["db_path"]).stat().st_size / 1024 / 1024
    raw = list(Path(config["data"]["raw_dir"]).rglob("*.json"))
    raw_mb = sum(p.stat().st_size for p in raw) / 1024 / 1024
    print(f"\n용량: DB {db_mb:,.0f}MB, 원문 {len(raw):,}개 {raw_mb:,.0f}MB")


if __name__ == "__main__":
    main()
