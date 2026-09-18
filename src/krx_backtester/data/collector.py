"""기간 단위 수집, 호출 한도 관리, collection_log 기록 (P1)."""

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import duckdb
import pandas as pd
import requests

from krx_backtester.data.krx_client import BASE_INFO_FIELDS, DAILY_FIELDS, FIELDS, INDEX_FIELDS, KrxResponse

KST = timezone(timedelta(hours=9))
AVAILABLE_HOUR = 8
DAILY_SERVICES = {"stk_bydd_trd": "KOSPI", "ksq_bydd_trd": "KOSDAQ"}
BASE_INFO_SERVICES = {"KOSPI": "stk_isu_base_info", "KOSDAQ": "ksq_isu_base_info"}
INDEX_SERVICE = "kospi_dd_trd"
DONE_STATUSES = ("ok", "holiday")

Fetch = Callable[[str, str], KrxResponse]


class StopCollection(Exception):
    """수집을 중단해야 하는 조건: 호출 예산 도달, 연속 오류, 응답 필드 불일치."""


@dataclass
class CollectResult:
    calls: int
    dates_processed: int
    not_yet_available_dates: int
    stop_reason: str | None


def available_at(d: date) -> datetime:
    """D일 데이터 제공 시각. 공휴일 달력이 없으므로 D 다음 평일 08시(KST)로 보수적으로 계산."""
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, AVAILABLE_HOUR, tzinfo=KST)


def weekdays(start: date, end: date) -> list[date]:
    """주말은 호출 대상에서 뺀다. 평일 휴장일은 빈 응답으로 판정."""
    days = (end - start).days + 1
    return [start + timedelta(days=i) for i in range(days) if (start + timedelta(days=i)).weekday() < 5]


def done_keys(con: duckdb.DuckDBPyConnection) -> set[tuple[str, date]]:
    rows = con.execute(
        "SELECT DISTINCT service, bas_dd FROM collection_log WHERE status IN ('ok', 'holiday')"
    ).fetchall()
    return {(service, bas_dd) for service, bas_dd in rows}


def _resp_message(text: str) -> str:
    try:
        return json.loads(text).get("respMsg", text[:200])
    except (ValueError, AttributeError):
        return text[:200]


class Collector:
    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        fetch: Fetch,
        max_calls: int,
        request_delay_seconds: float,
        max_consecutive_errors: int,
        now: Callable[[], datetime] = lambda: datetime.now(KST),
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.con = con
        self.fetch = fetch
        self.max_calls = max_calls
        self.delay = request_delay_seconds
        self.max_consecutive_errors = max_consecutive_errors
        self.now = now
        self.sleep = sleep
        self.calls = 0
        self.consecutive_errors = 0
        self.done: dict[tuple[str, date], str] = {}
        self.not_yet_logged: set[tuple[str, date]] = set()
        # 종목별 최초 등장일과 (최초 등장일, 시장) 집합. 날짜마다 전체 스캔하지 않으려고 실행 시작 때 한 번만 집계
        self.first_seen: dict[str, date] = {}
        self.first_days: set[tuple[date, str]] = set()

    def run(self, start: date, end: date) -> CollectResult:
        started = self.now()
        self.done = {
            (s, d): st
            for s, d, st in self.con.execute(
                "SELECT service, bas_dd, arg_max(status, rowid) FROM collection_log "
                "WHERE status IN ('ok', 'holiday') GROUP BY service, bas_dd"
            ).fetchall()
        }
        self.not_yet_logged = {
            (s, d)
            for s, d in self.con.execute(
                "SELECT DISTINCT service, bas_dd FROM collection_log WHERE status = 'not_yet_available'"
            ).fetchall()
        }
        self.first_seen = {}
        self.first_days = set()
        for code, first_date, market in self.con.execute(
            "SELECT code, min(date), arg_min(market, date) FROM raw_daily GROUP BY code"
        ).fetchall():
            self.first_seen[code] = first_date
            self.first_days.add((first_date, market))
        processed = not_yet = 0
        stop_reason = None
        try:
            for d in weekdays(start, end):
                if started < available_at(d):
                    self._log_not_yet(d)
                    not_yet += 1
                    continue
                self._collect_date(d)
                processed += 1
        except StopCollection as e:
            stop_reason = str(e)
        finally:
            rebuild_instruments(self.con)
        return CollectResult(self.calls, processed, not_yet, stop_reason)

    # --- 날짜 단위 처리 ---

    def _collect_date(self, d: date) -> None:
        pending = [s for s in DAILY_SERVICES if (s, d) not in self.done]
        if self.calls + len(pending) > self.max_calls:
            raise StopCollection(f"호출 예산 {self.max_calls}회 도달")

        # 결과: "ok"/"holiday"(이전 실행 완료), None(오류), list(이번 응답 행)
        results: dict[str, str | list | None] = {}
        called: dict[str, datetime] = {}
        for service in DAILY_SERVICES:
            if (service, d) in self.done:
                results[service] = self.done[(service, d)]
                continue
            got = self._call(service, d)
            results[service] = None if got is None else got[0]
            if got is not None:
                called[service] = got[1]

        def is_empty(r):
            return r == "holiday" or r == []

        if all(is_empty(r) for r in results.values()):
            for service in called:
                self._log(service, d, "holiday", 200, called[service], 0, None)
            if (INDEX_SERVICE, d) not in self.done:
                self._log(INDEX_SERVICE, d, "holiday", None, None, 0, "일별매매 휴장일, 호출 안 함")
            return

        has_trading = False
        for service, r in results.items():
            if r == "ok" or (isinstance(r, list) and r):
                has_trading = True
            if isinstance(r, list) and r:
                self._store_daily(service, d, r)
                self._log(service, d, "ok", 200, called[service], len(r), None)
            elif r == []:
                other = next(v for s, v in results.items() if s != service)
                message = "다른 시장 호출 실패로 휴장일 판정 보류" if other is None else "다른 시장은 데이터가 있는데 빈 응답"
                self._log(service, d, "error", 200, called[service], 0, message)
        if not has_trading:
            return

        if (INDEX_SERVICE, d) not in self.done:
            got = self._call(INDEX_SERVICE, d)
            if got is not None:
                rows, called_at = got
                if rows:
                    self._store_index(d, rows)
                    self._log(INDEX_SERVICE, d, "ok", 200, called_at, len(rows), None)
                else:
                    self._log(INDEX_SERVICE, d, "error", 200, called_at, 0, "거래일인데 지수 빈 응답")

        for market, service in BASE_INFO_SERVICES.items():
            if (service, d) in self.done or not self._has_new_codes(d, market):
                continue
            got = self._call(service, d)
            if got is not None:
                rows, called_at = got
                if rows:
                    self._store_base_info(d, market, rows)
                    self._log(service, d, "ok", 200, called_at, len(rows), None)
                else:
                    self._log(service, d, "error", 200, called_at, 0, "신규 코드 등장일인데 빈 응답")

    def _has_new_codes(self, d: date, market: str) -> bool:
        """해당 시장의 d일 종목 중 적재된 이전 날짜에 한 번도 나오지 않은 단축코드가 있는지."""
        return (d, market) in self.first_days

    # --- 호출과 기록 ---

    def _call(self, service: str, d: date) -> tuple[list[dict], datetime] | None:
        """호출 1회. 실패 호출도 예산에 포함. 성공이면 (행, 호출 시각), 실패면 None."""
        if self.calls >= self.max_calls:
            raise StopCollection(f"호출 예산 {self.max_calls}회 도달")
        if self.calls > 0:
            self.sleep(self.delay)
        self.calls += 1
        called_at = self.now().replace(tzinfo=None)
        try:
            resp = self.fetch(service, d.strftime("%Y%m%d"))
        except requests.RequestException as e:
            return self._fail(service, d, None, called_at, type(e).__name__)
        if resp.status_code != 200:
            return self._fail(service, d, resp.status_code, called_at, _resp_message(resp.text))
        try:
            rows = json.loads(resp.text)["OutBlock_1"]
        except (ValueError, KeyError, TypeError):
            return self._fail(service, d, 200, called_at, "OutBlock_1 없음")
        if rows and set(rows[0]) != set(FIELDS[service]):
            diff = sorted(set(rows[0]) ^ set(FIELDS[service]))
            self._log(service, d, "error", 200, called_at, len(rows), f"응답 필드 불일치: {diff}")
            raise StopCollection(f"{service} {d} 응답 필드가 M0 결과와 다름: {diff}")
        self.consecutive_errors = 0
        return rows, called_at

    def _fail(self, service, d, http_status, called_at, message) -> None:
        self._log(service, d, "error", http_status, called_at, None, message)
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.max_consecutive_errors:
            raise StopCollection(f"연속 오류 {self.consecutive_errors}회 (마지막: {service} {d} {http_status} {message})")
        return None

    def _log(self, service, d, status, http_status, called_at, row_count, message) -> None:
        self.con.execute(
            "INSERT INTO collection_log VALUES (?, ?, ?, ?, ?, ?, ?)",
            [service, d, status, http_status, called_at, row_count, message],
        )
        if status in DONE_STATUSES:
            self.done[(service, d)] = status

    def _log_not_yet(self, d: date) -> None:
        for service in (*DAILY_SERVICES, INDEX_SERVICE):
            if (service, d) not in self.done and (service, d) not in self.not_yet_logged:
                self._log(service, d, "not_yet_available", None, None, None, f"제공 시각 {available_at(d):%Y-%m-%d %H:%M} KST 이전")
                self.not_yet_logged.add((service, d))

    # --- 원시 적재 (문자열 그대로) ---

    def _insert(self, table: str, prefix_sql: str, params: list, fields: tuple[str, ...], rows: list[dict]) -> None:
        frame = pd.DataFrame(rows, columns=list(fields), dtype="string")
        self.con.register("incoming", frame)
        try:
            self.con.execute(f"INSERT INTO {table} SELECT {prefix_sql}, {', '.join(fields)} FROM incoming", params)
        finally:
            self.con.unregister("incoming")

    def _store_daily(self, service: str, d: date, rows: list[dict]) -> None:
        market = DAILY_SERVICES[service]
        self.con.execute("DELETE FROM raw_daily WHERE date = ? AND market = ?", [d, market])
        self._insert("raw_daily", "?::DATE, ?, ISU_CD", [d, market], DAILY_FIELDS, rows)
        for row in rows:
            code = row["ISU_CD"]
            if code not in self.first_seen or d < self.first_seen[code]:
                self.first_seen[code] = d
                self.first_days.add((d, market))

    def _store_index(self, d: date, rows: list[dict]) -> None:
        self.con.execute("DELETE FROM raw_index WHERE date = ?", [d])
        self._insert("raw_index", "?::DATE", [d], INDEX_FIELDS, rows)

    def _store_base_info(self, d: date, market: str, rows: list[dict]) -> None:
        self.con.execute("DELETE FROM raw_isu_base WHERE date = ? AND market = ?", [d, market])
        self._insert("raw_isu_base", "?::DATE, ?, ISU_SRT_CD", [d, market], BASE_INFO_FIELDS, rows)


def rebuild_instruments(con: duckdb.DuckDBPyConnection) -> None:
    """first/last_seen은 raw_daily 등장일(정지 형태 행 포함). 적재된 마지막 날보다 이르면 폐지, 같으면 우측 검열."""
    con.execute("DELETE FROM instruments")
    con.execute(
        """
        INSERT INTO instruments
        WITH seen AS (
            SELECT code, min(date) AS first_seen, max(date) AS last_seen,
                   arg_max(ISU_NM, date) AS name, arg_max(market, date) AS market
            FROM raw_daily GROUP BY code
        ),
        std AS (
            SELECT code, arg_max(ISU_CD, date) AS isu_std_cd,
                   arg_max(KIND_STKCERT_TP_NM, date) AS stock_kind
            FROM raw_isu_base GROUP BY code
        ),
        bounds AS (SELECT max(date) AS last_loaded FROM raw_daily)
        SELECT s.code, std.isu_std_cd, s.name, s.market, s.first_seen, s.last_seen,
               CASE WHEN s.last_seen < b.last_loaded THEN s.last_seen END,
               s.last_seen = b.last_loaded,
               CASE WHEN std.stock_kind IS NULL THEN NULL ELSE std.stock_kind <> '보통주' END
        FROM seen s LEFT JOIN std USING (code) CROSS JOIN bounds b
        """
    )


@dataclass
class Estimate:
    trading_dates: int
    not_yet_available_dates: int
    daily_and_index_calls: int
    base_info_calls_known: int
    base_info_calls_max_unknown: int
    max_calls_per_day: int

    @property
    def min_total(self) -> int:
        return self.daily_and_index_calls + self.base_info_calls_known

    @property
    def max_total(self) -> int:
        return self.min_total + self.base_info_calls_max_unknown

    def days(self, total: int) -> int:
        return math.ceil(total / self.max_calls_per_day) if total else 0


def estimate(con: duckdb.DuckDBPyConnection, start: date, end: date, now: datetime, max_calls_per_day: int) -> Estimate:
    """호출 없이 필요한 호출 수를 센다. ok·holiday는 제외. 휴장일의 지수 호출도 포함한 상한 쪽 추정."""
    done = done_keys(con)
    dates = weekdays(start, end)
    available = [d for d in dates if now >= available_at(d)]
    services = (*DAILY_SERVICES, INDEX_SERVICE)
    daily_calls = sum(1 for d in available for s in services if (s, d) not in done)

    # 적재된 날짜: 신규 코드 등장 여부를 알 수 있으므로 정확히 셈
    known = 0
    for d, market in con.execute(
        "SELECT DISTINCT first_date, market FROM ("
        "  SELECT min(date) AS first_date, arg_min(market, date) AS market FROM raw_daily GROUP BY code"
        ") WHERE first_date BETWEEN ? AND ?",
        [start, end],
    ).fetchall():
        if (BASE_INFO_SERVICES[market], d) not in done:
            known += 1
    # 미적재 날짜: 적재 전에는 신규 코드 등장 여부를 알 수 없으므로 시장당 1회를 상한으로 둠
    loaded = {d for (d,) in con.execute("SELECT DISTINCT date FROM raw_daily").fetchall()}
    unknown_dates = [d for d in available if d not in loaded and not all((s, d) in done for s in DAILY_SERVICES)]
    return Estimate(
        trading_dates=len(available),
        not_yet_available_dates=len(dates) - len(available),
        daily_and_index_calls=daily_calls,
        base_info_calls_known=known,
        base_info_calls_max_unknown=2 * len(unknown_dates),
        max_calls_per_day=max_calls_per_day,
    )


def status(con: duckdb.DuckDBPyConnection, today: date) -> dict:
    """서비스별 적재 범위·상태별 건수(키별 최신 상태), 오늘(KST) 호출 수."""
    ranges = con.execute(
        "SELECT service, min(bas_dd), max(bas_dd), count(*) FROM collection_log WHERE status = 'ok' GROUP BY service ORDER BY service"
    ).fetchall()
    counts = con.execute(
        """
        SELECT service, latest, count(*) FROM (
            SELECT service, bas_dd, arg_max(status, rowid) AS latest FROM collection_log GROUP BY service, bas_dd
        ) GROUP BY service, latest ORDER BY service, latest
        """
    ).fetchall()
    calls_today = con.execute(
        "SELECT count(*) FROM collection_log WHERE called_at IS NOT NULL AND CAST(called_at AS DATE) = ?", [today]
    ).fetchone()[0]
    return {"ranges": ranges, "counts": counts, "calls_today": calls_today}
