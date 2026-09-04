"""트렌드 모멘텀 — 수준이 아니라 '변화'를 잰다.

인기도(popularity.py)는 **얼마나 알려졌나**를 잰다. 그것만 보면 추천은
늘 같은 곳으로 간다. 경복궁·남산타워는 언제나 1위라, 순위표에는
아무 정보가 없다. 트렌드는 순위가 아니라 **움직임**에 있다.

그래서 축을 셋으로 나눈다.

    level     지금 얼마나 큰가            최근 12개월 조회수
    momentum  작년 같은 달보다 늘었나      전년 동월비 (계절성이 상쇄된다)
    surge     제 이력보다 최근이 튀었나     최근 3개월 비율의 z점수

`momentum`을 전년 **동월**과 비교하는 것이 요점이다. 관광은 계절성이
지배적이라 전월과 비교하면 "여름에 한강이 뜬다"가 트렌드로 잡힌다.
같은 달끼리 나누면 계절 성분이 약분된다.

세 축을 나눠야 구분되는 것이 있다. 기울기는 완만한데 최근에만 튄 곳
(뉴스 스파이크)과, 꾸준히 오르는 곳은 다른 현상이다.

**한계를 분명히 해 둔다. 이건 '방문'이 아니라 '관심'이다.**
성수동은 2025년 2월에 조회수가 7배로 튀었다가 그대로 내려앉았다.
방문객이 7배가 됐을 리 없다 — 뉴스가 났을 뿐이다. 반대로 북촌한옥마을은
조회수가 2년간 40% 줄었지만 서울AI재단 실측으로는 외국인 방문이
늘었다. 이미 아는 곳은 검색하지 않기 때문이다.

그러니 이 지표는 "가는 사람"이 아니라 **"새로 알아보는 사람"**을 뜻한다.
방문 실측(유동인구)과 나란히 놓을 때 비로소 쓸모가 생긴다 — 둘이
어긋나는 지점이 곧 발견이다.

    python -m weatherfit.momentum build     # 위키백과 24개월 수집 (약 3분)
    python -m weatherfit.momentum stats     # 계산된 값 확인
"""
from __future__ import annotations

import json
import math
import re
import statistics as st
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "momentum.json"

PAGEVIEWS = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
             "/{wiki}.wikipedia/all-access/user/{title}/monthly/{start}/{end}")
UA = {"User-Agent": "weatherfit-seoul/1.0 (tourism course planner; "
                    "https://github.com/tradeprogram/weatherfit_seoul)"}

MONTHS = 25            # 24개월 비교 + 진행 중인 달 한 칸
MIN_MONTHS = 20        # 이보다 짧으면 전년 동월비를 못 낸다
MIN_VIEWS = 240        # 12개월 합이 이보다 적으면 잡음이다 (월 20회)


def _window(now=None) -> tuple[str, str]:
    """진행 중인 달은 뺀다. 며칠치만 집계돼 있어 '급감'으로 보인다."""
    t = now or time.gmtime()
    y, m = t.tm_year, t.tm_mon
    sy, sm = y - 2, m
    return f"{sy}{sm:02d}0100", f"{y}{m:02d}0100"


def series(title: str, wiki: str, session: requests.Session,
           tries: int = 3) -> list[tuple[str, int]]:
    """월별 조회수. 실패하면 빈 목록."""
    start, end = _window()
    url = PAGEVIEWS.format(wiki=wiki, start=start, end=end,
                           title=requests.utils.quote(
                               title.replace(" ", "_"), safe=""))
    for k in range(tries):
        try:
            r = session.get(url, timeout=25)
        except Exception:
            time.sleep(0.8 * (k + 1))
            continue
        if r.status_code == 404:
            return []                      # 문서가 없다 — 재시도할 이유가 없다
        if r.ok:
            cur = end[:6]
            return [(i["timestamp"][:6], int(i["views"]))
                    for i in r.json().get("items", [])
                    if i["timestamp"][:6] < cur]
        time.sleep(0.8 * (k + 1))
    return []


def axes(views: list[int]) -> dict | None:
    """세 축. 자료가 모자라면 None — 0으로 채우면 '안 뜬다'는 거짓말이 된다."""
    n = len(views)
    if n < MIN_MONTHS:
        return None
    half = min(12, n // 2)
    last, prev = views[-half:], views[-2 * half:-half]
    if sum(prev) < MIN_VIEWS:
        return None

    # 전년 동월비 — 같은 달끼리 나누므로 계절 성분이 약분된다
    yoy = (sum(last) - sum(prev)) / sum(prev)
    ratio = [last[i] / max(prev[i], 1) for i in range(half)]
    sd = st.pstdev(ratio)
    surge = (st.mean(ratio[-3:]) - st.mean(ratio)) / sd if sd > 1e-9 else 0.0

    return {
        "level": sum(last),
        "yoy": round(yoy, 4),
        "surge": round(surge, 3),
        "months": n,
    }


def score(a: dict, max_log: float) -> dict:
    """세 축을 0~1로 옮긴다. 합치지 않는다 — 뜻이 다른 값이다."""
    return {
        "level": round(math.log1p(a["level"]) / max_log, 3) if max_log else 0.0,
        # 전년비는 ±50%를 양 끝으로 본다. 두 배로 뛰는 곳은 드물고,
        # 드문 것에 눈금을 맞추면 나머지가 전부 가운데로 뭉친다.
        "momentum": round(max(0.0, min(1.0, (a["yoy"] + 0.5) / 1.0)), 3),
        "surge": round(max(0.0, min(1.0, (a["surge"] + 2.0) / 4.0)), 3),
    }


LABEL = {
    "rising": "뜨는 중", "spike": "최근 급등", "peaked": "올랐다 진정",
    "steady": "꾸준함", "fading": "식는 중", "unknown": "자료 없음",
}


def classify(a: dict | None) -> str:
    """말로 옮긴다. 숫자만 주면 사용자가 판단할 수 없다.

    순서가 중요하다. **급등을 먼저 걸러야 한다** — 뉴스로 한 달 튄 곳도
    전년비는 크게 나오기 때문이다. 처음엔 '전년비가 크고 급등도 크면
    뜨는 중'으로 뒀는데, 그러면 두 가지가 반대로 잡혔다.

      뉴스 스파이크가 '뜨는 중'이 된다 — 전년비 조건을 먼저 통과한다.
      꾸준히 오르는 곳이 안 잡힌다 — 매달 같은 비율로 늘면 비율의
        분산이 0이라 급등이 0이 된다. 가장 뚜렷한 상승인데 걸러진다.

    둘의 차이는 '늘어난 양'이 아니라 **'최근에 몰렸는가'**다.
    """
    if not a:
        return "unknown"
    if a["surge"] >= 1.2:
        return "spike"           # 최근 몇 달에 몰렸다 = 뉴스일 가능성
    if a["yoy"] >= 0.15:
        # 전년비만 보면 청와대가 +419%로 1위다. 그런데 급등이 -1.15 —
        # 오른 건 작년 일이고 최근 석 달은 제 평균보다 낮다. 이미 지나간
        # 상승을 '뜨는 중'이라 적으면 오늘 갈 곳을 잘못 고르게 된다.
        return "rising" if a["surge"] >= -0.5 else "peaked"
    if a["yoy"] <= -0.15:
        return "fading"
    return "steady"


# ------------------------------------------------------- 어권 이동과 검증

_LANGLINK = "https://{wiki}.wikipedia.org/w/api.php"
GEO_MAX_KM = 1.5       # 지점 단위 명소다. 인기도(3km)보다 좁게 본다.
GEO_WIDE_KM = 6.0      # 산·하천·공원은 넓다. 아래 설명 참조.

# 넓게 퍼진 지형. 문서 좌표는 산이면 정상, 하천이면 중간쯤을 찍는데
# 관광 데이터는 사람이 들어가는 입구를 찍는다. 청계천은 11km짜리
# 하천이라 둘이 2.4km 어긋나는 게 정상인데, 지점 기준으로 재면
# '다른 장소'가 되어 멀쩡한 매칭이 떨어진다.
_WIDE = re.compile(r"(산|천|강|공원|호수|섬|숲|길|도성|계곡|저수지)$")


def _max_km(title: str) -> float:
    return GEO_WIDE_KM if _WIDE.search(title.strip()) else GEO_MAX_KM


def _translate(ko_title: str, wiki: str, session: requests.Session) -> str:
    """한국어 문서 제목 → 다른 어권 제목. 없으면 빈 문자열."""
    if wiki == "ko":
        return ko_title
    try:
        r = session.get(_LANGLINK.format(wiki="ko"), timeout=20, params={
            "action": "query", "prop": "langlinks", "titles": ko_title,
            "lllang": wiki, "format": "json", "redirects": 1,
        })
        r.raise_for_status()
        for pg in (r.json().get("query", {}).get("pages") or {}).values():
            for ll in pg.get("langlinks") or []:
                return ll.get("*", "")
    except Exception:
        pass
    return ""


def _coords(session: requests.Session, wiki: str, titles: list[str]) -> dict:
    """문서 제목 → (위도, 경도) 또는 None. 50개씩 묶어 묻는다."""
    from .popularity import _coords_batch
    import weatherfit.popularity as _pop

    keep, out = _pop.WIKI_API, {}
    _pop.WIKI_API = _LANGLINK.format(wiki=wiki)
    try:
        for i in range(0, len(titles), 50):
            out.update(_coords_batch(session, titles[i:i + 50]))
            time.sleep(0.15)
    finally:
        _pop.WIKI_API = keep
    return out


def _verify(rows: dict, wiki: str, session: requests.Session,
            verbose: bool = True) -> int:
    """영어 문서가 **그 자리에 있는 장소**를 가리키는지 좌표로 확인한다.

    어권을 옮기는 순간 매칭이 새로 깨진다. 한국어 쪽에서 멀쩡히 맞은
    문서가 언어 링크를 타고 엉뚱한 데로 간다 —

        국립극장     → National Theatre        런던의 극장이다
        국립기상박물관 → National Palace Museum   경복궁 안의 다른 박물관
        예지원       → Ye Ji-won               배우 이름
        YG 엔터테인먼트 → YG Entertainment        건물이 아니라 회사
        일렉트릭 쇼크  → Electric Shock (disambiguation)

    조회수가 35만인 YG 엔터테인먼트를 그대로 두면 '관심이 가장 큰 명소'가
    소속사가 된다. 좌표가 없는 문서(회사·인물·노래·동음이의)는 전부 떨어지고,
    좌표가 있어도 1.5km를 넘으면 다른 장소다.
    """
    from .index import build_index
    from .report import load
    from .routing import haversine_m

    where = {p.cid: (p.lat, p.lon)
             for p in build_index(load()).places if p.lat and p.lon}
    titles = sorted({r["wiki_title"] for r in rows.values() if r["wiki_title"]})
    coords = _coords(session, wiki, titles)

    kept = 0
    for cid, r in rows.items():
        at, here = coords.get(r["wiki_title"]), where.get(cid)
        if not at or not here:
            r["geo_km"], r["why"] = None, "좌표 없는 문서 · 장소가 아닐 수 있다"
        else:
            km = haversine_m(here[0], here[1], at[0], at[1]) / 1000.0
            cap = _max_km(r.get("title", ""))
            r["geo_km"] = round(km, 2)
            r["why"] = "" if km <= cap else f"{km:,.1f}km 떨어진 문서"
        if r["why"]:
            r.pop("level", None)          # 점수를 못 받게 한다
            r["trend"] = "unknown"
        else:
            kept += 1
    if verbose:
        print(f"  좌표 검증 통과 {kept}/{len(titles)}건")
    return kept


# ----------------------------------------------------------------- 수집

def build(lang: str = "en", limit: int | None = None,
          delay: float = 0.25, verbose: bool = True) -> Path:
    """검증된 위키 매칭이 있는 장소의 24개월 시계열을 받아 축을 계산한다.

    기본을 영어판으로 두는 이유: 이 서비스가 보는 사람은 외국인 관광객이다.
    한국어판 조회수는 한국인의 관심을 잰다. 북촌한옥마을은 영어판이
    한국어판의 128배인데, 한국어로 재면 '거의 안 알려진 곳'이 된다.
    """
    from .popularity import load_cache

    pop = load_cache()
    targets = [(cid, v) for cid, v in pop.items()
               if v.get("wiki_title") and v.get("geo_ok", True)]
    if limit:
        targets = targets[:limit]

    session = requests.Session()
    session.headers.update(UA)
    wiki = {"en": "en", "ko": "ko", "ja": "ja"}.get(lang, "en")

    rows: dict[str, dict] = {}
    ok = 0
    for n, (cid, v) in enumerate(targets, 1):
        title = v["wiki_title"]
        # 어권을 바꾸면 문서 제목도 달라진다. 언어 간 링크로 옮긴다.
        target = _translate(title, wiki, session) if wiki != "ko" else title
        got = series(target, wiki, session) if target else []
        a = axes([c for _, c in got])
        rows[cid] = {
            "title": v.get("title", ""), "wiki_title": target or "",
            "wiki": wiki, **(a or {}), "trend": classify(a),
        }
        if a:
            ok += 1
        time.sleep(delay)
        if verbose and n % 40 == 0:
            print(f"  {n}/{len(targets)} · 계산됨 {ok}", flush=True)

    ok = _verify(rows, wiki, session, verbose)

    have = [r for r in rows.values() if r.get("level")]
    max_log = max((math.log1p(r["level"]) for r in have), default=0.0) or 1.0
    for r in rows.values():
        if r.get("level"):
            r["score"] = score(r, max_log)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(
        {"meta": {"built_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                  "wiki": wiki, "targets": len(targets), "computed": ok,
                  "note": "위키백과 월별 조회수 기준. 방문이 아니라 관심을 잰다."},
         "place": rows}, ensure_ascii=False), encoding="utf-8")
    if verbose:
        print(f"완료 {ok}/{len(targets)}건 계산 → {CACHE}")
    return CACHE


# ----------------------------------------------------------------- 조회

_table: dict | None = None


def table() -> dict:
    global _table
    if _table is None:
        try:
            _table = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            _table = {"meta": {}, "place": {}}
    return _table


def reset() -> None:
    global _table
    _table = None


def of(cid: str) -> dict | None:
    """한 장소의 트렌드. 없으면 None — 모르면 모른다고 한다."""
    row = (table().get("place") or {}).get(cid)
    return row if row and row.get("score") else None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    ap = argparse.ArgumentParser(description="트렌드 모멘텀")
    ap.add_argument("action", choices=["build", "stats"])
    ap.add_argument("--lang", default="en")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    if a.action == "build":
        build(lang=a.lang, limit=a.limit)
        return

    t = table()
    rows = [r for r in (t.get("place") or {}).values() if r.get("score")]
    if not rows:
        raise SystemExit("아직 만들지 않았습니다. python -m weatherfit.momentum build")
    m = t["meta"]
    print(f"{m.get('computed')}/{m.get('targets')}건 · {m.get('wiki')}위키 · "
          f"{m.get('built_at','')[:10]}")
    from collections import Counter
    print("분포:", dict(Counter(r["trend"] for r in rows)))
    for kind, title in (("rising", "뜨는 중"), ("spike", "최근 급등"),
                        ("fading", "식는 중")):
        got = [r for r in rows if r["trend"] == kind]
        got.sort(key=lambda r: -abs(r["yoy"]))
        print(f"\n{title} ({len(got)}곳)")
        for r in got[:8]:
            print(f"  {r['title'][:24]:26} 전년비 {r['yoy']*100:+6.1f}% · "
                  f"급등 {r['surge']:+5.2f} · 조회 {r['level']:,}")


if __name__ == "__main__":
    main()
