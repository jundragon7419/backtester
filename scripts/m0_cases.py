"""M0 나머지 항목 확인: 거래정지, 유상증자 권리락, 인적분할, 관리종목 표기, 응답 필드.

핵심 3개(m0_core.py) 통과 후 실행한다. 사용 (저장소 루트, PowerShell):
    .\\.venv\\Scripts\\python.exe scripts\\m0_cases.py
"""

from collections import Counter

from m0_core import find, load, num


def factor_line(prev: dict | None, cur: dict | None) -> str:
    if not prev or not cur:
        return "계산 불가 (행 없음)"
    close, diff, prev_close = num(cur["TDD_CLSPRC"]), num(cur["CMPPREVDD_PRC"]), num(prev["TDD_CLSPRC"])
    if None in (close, diff, prev_close) or not prev_close:
        return "계산 불가 (숫자 아님)"
    base = close - diff
    return f"기준가 = {base:g}, 전일 종가 = {prev_close:g}, 계수 = {base / prev_close:.6f}, 전일 종가 − 기준가 = {prev_close - base:g}"


def halt() -> None:
    print("\n== 거래정지: 신라젠 (코스닥)")
    for d in ("20200429", "20200504", "20200506", "20210601", "20221012", "20221013"):
        print(d, find(load("ksq_bydd_trd", d), "215600", "신라젠"))


def rights() -> None:
    print("\n== 유상증자 권리락: 대한항공 (권리락일 2021-01-25)")
    rows = {d: find(load("stk_bydd_trd", d), "003490", "대한항공") for d in ("20210122", "20210125", "20210331")}
    for d, x in rows.items():
        print(d, x)
    print(factor_line(rows["20210122"], rows["20210125"]))


def spinoff() -> None:
    print("\n== 인적분할: SK텔레콤 2021 (정지 10-26~11-26, 재상장 11-29)")
    for d in ("20211025", "20211026", "20211129"):
        rows = load("stk_bydd_trd", d)
        print(d, "SKT:", find(rows, "017670", "SK텔레콤"))
        print(d, "SK스퀘어:", find(rows, "402340", "SK스퀘어"))
    before = find(load("stk_bydd_trd", "20211025"), "017670", "SK텔레콤")
    after = find(load("stk_bydd_trd", "20211129"), "017670", "SK텔레콤")
    print(factor_line(before, after))


def managed() -> None:
    print("\n== 관리종목 표기: SECT_TP_NM 고유값")
    for service, d in (("ksq_bydd_trd", "20150102"), ("ksq_bydd_trd", "20190102"), ("ksq_bydd_trd", "20230102"), ("stk_bydd_trd", "20230102")):
        print(service, d, Counter(x["SECT_TP_NM"] for x in load(service, d)).most_common())


def fields() -> None:
    print("\n== 응답 필드 실측 (20180504)")
    for service in ("stk_bydd_trd", "ksq_bydd_trd", "stk_isu_base_info", "ksq_isu_base_info", "kospi_dd_trd"):
        rows = load(service, "20180504")
        print(service, len(rows), "행", list(rows[0].keys()) if rows else "빈 응답")
    print("삼성전자 LIST_SHRS·MKTCAP 시점별 여부:")
    for d in ("20180427", "20180504"):
        x = find(load("stk_bydd_trd", d), "005930", "삼성전자")
        print(d, {k: x[k] for k in ("TDD_CLSPRC", "LIST_SHRS", "MKTCAP")})


def main() -> None:
    halt()
    rights()
    spinoff()
    managed()
    fields()  # kospi_dd_trd 권한 오류 시 load가 종료하므로 마지막에 실행


if __name__ == "__main__":
    main()
