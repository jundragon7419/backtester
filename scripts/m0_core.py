"""M0 핵심 3개 확인: 기준가 역산 계수, 대비 방향 표현, 상장폐지 종목 포함.

사용 (저장소 루트, PowerShell):
    .\\.venv\\Scripts\\python.exe scripts\\m0_core.py
    .\\.venv\\Scripts\\python.exe scripts\\m0_core.py --delisted-code 117930 --delisted-date 20170306

원문은 data/raw/{service}/{basDd}.json에 저장되고, 이미 있으면 다시 호출하지 않는다.
"""

import argparse
import json
import sys

from krx_backtester.data.krx_client import DEFAULT_RAW_DIR, fetch


def load(service: str, bas_dd: str) -> list[dict]:
    path = DEFAULT_RAW_DIR / service / f"{bas_dd}.json"
    if not path.exists():
        r = fetch(service, bas_dd)
        print(f"[call] {service} {bas_dd} -> HTTP {r.status_code}")
        if r.status_code != 200:
            print(r.text)
            sys.exit(1)
    body = json.loads(path.read_text(encoding="utf-8"))
    if "OutBlock_1" not in body:
        print(f"[stop] {service} {bas_dd}: OutBlock_1 없음: {body}")
        sys.exit(1)
    return body["OutBlock_1"]


def row(rows: list[dict], code: str) -> dict | None:
    return next((x for x in rows if x["ISU_CD"] == code), None)


def num(s: str) -> float:
    return float(s.replace(",", ""))


def check_factor() -> None:
    print("\n== 1. 기준가 역산 보정식 (005930)")
    for d in ("20180427", "20180503", "20180504"):
        print(d, row(load("stk_bydd_trd", d), "005930"))
    prev = row(load("stk_bydd_trd", "20180503"), "005930") or row(load("stk_bydd_trd", "20180427"), "005930")
    cur = row(load("stk_bydd_trd", "20180504"), "005930")
    base = num(cur["TDD_CLSPRC"]) - num(cur["CMPPREVDD_PRC"])
    factor = base / num(prev["TDD_CLSPRC"])
    print(f"기준가 = {base:g}, 전일 종가({prev['BAS_DD']}) = {prev['TDD_CLSPRC']}, 계수 = {factor:.6f}, 1/50 = {1/50:.6f}")


def check_direction() -> None:
    print("\n== 2. 대비 방향 표현 (20180504)")
    rows = load("stk_bydd_trd", "20180504")
    print("필드 목록:", list(rows[0].keys()))
    down = [x for x in rows if x["FLUC_RT"] not in ("-", "") and num(x["FLUC_RT"]) < 0]
    print(f"전체 {len(rows)}행, FLUC_RT < 0 인 행 {len(down)}개. 예시 3개:")
    for x in down[:3]:
        print({k: x[k] for k in ("ISU_CD", "ISU_NM", "TDD_CLSPRC", "CMPPREVDD_PRC", "FLUC_RT")})
    print("하락 행 중 CMPPREVDD_PRC가 '-'로 시작하는 비율:",
          sum(x["CMPPREVDD_PRC"].startswith("-") for x in down), "/", len(down))


def check_delisted(code: str, bas_dd: str) -> None:
    print(f"\n== 3. 상장폐지 종목 포함 ({code}, {bas_dd})")
    print(row(load("stk_bydd_trd", bas_dd), code) or row(load("ksq_bydd_trd", bas_dd), code))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--delisted-code")
    p.add_argument("--delisted-date")
    a = p.parse_args()
    check_factor()
    check_direction()
    if a.delisted_code and a.delisted_date:
        check_delisted(a.delisted_code, a.delisted_date)


if __name__ == "__main__":
    main()
