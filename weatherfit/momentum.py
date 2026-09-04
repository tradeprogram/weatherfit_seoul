"""트렌드 엔진 — 무엇이 유행인지는 모른다. 무엇이 **움직이는지**만 잰다.

트렌드를 따라가는 코드는 트렌드와 함께 죽는다. '요즘 성수동이 뜬다'를
코드에 적으면 성수동이 식는 날 코드도 같이 식는다. 올해 목록을 넣으면
내년에 목록을 다시 넣어야 한다.

그래서 이 모듈은 유행을 **모른다**. 시계열을 받아 움직임만 재고, 무엇이
뜨는지는 데이터가 말하게 둔다. 유행이 바뀌어도 엔진은 그대로다.

    엔진   숫자가 조회수인지 소비액인지 방문객인지 모른다
    소스   시계열을 가져오는 함수 하나. 붙였다 뗐다 한다
    비교   두 소스가 어긋나는 지점이 곧 발견이다

축은 셋이다.

    level     지금 얼마나 큰가            최근 12기간 합
    momentum  작년 같은 때보다 늘었나      전년 동기비 (계절성이 약분된다)
    surge     제 이력보다 최근이 튀었나     최근 3기간 비율의 z점수

전년 **동기**와 비교하는 것이 요점이다. 관광은 계절성이 지배적이라
전월과 비교하면 "여름에 한강이 뜬다"가 트렌드로 잡힌다.

`level`만 단위에 매인다. 조회수 5만과 소비 비율 44%는 같은 자리에
놓을 수 없다. 그래서 정규화는 **소스 안에서만** 한다. momentum과 surge는
비율과 z점수라 단위가 없고, 그래서 소스를 가로질러 비교할 수 있다.
이 비대칭이 소스를 꽂았다 뺐다 할 수 있게 하는 전부다.

**이 지표들은 서로 다른 것을 잰다.** 위키백과는 '새로 알아보는 사람'이지
'가는 사람'이 아니다. 성수동은 뉴스 한 번에 조회수가 7배로 튀었다
내려앉았고, 북촌한옥마을은 조회수가 40.8% 줄었지만 실측 외국인 방문은
늘었다. 이미 아는 곳은 검색하지 않는다. 그러니 어느 한 소스로 순위를
매기면 안 되고, 어긋나는 지점을 읽어야 한다.

    python -m weatherfit.momentum sources           # 무엇이 꽂혀 있나
    python -m weatherfit.momentum build wikipedia   # 한 소스 수집
    python -m weatherfit.momentum stats wikipedia
    python -m weatherfit.momentum diverge           # 소스 간 불일치
"""
from __future__ import annotations

import csv
import json
import math
import re
import statistics as st
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TREND_DIR = ROOT / "data" / "trend"

PERIOD = 12            # 월 자료의 계절 주기. 분기 자료면 4다.
MAX_RATIO = 6.0        # 한 해에 이보다 크게 뛰면 자료가 바뀐 것이다 (아래 참조)


# ----------------------------------------------------------------- 엔진
#
# 여기 아래로는 숫자가 무엇을 뜻하는지 모른다. 그게 요점이다.

def axes(values: list[float], min_total: float = 0.0,
         period: int = PERIOD) -> dict | None:
    """세 축. 자료가 모자라면 None — 0으로 채우면 '안 뜬다'는 거짓말이 된다.

    비교 간격은 **반드시 계절 주기와 같아야 한다.** 처음엔 있는 자료를
    최대한 쓰려고 `min(12, n // 2)`로 뒀는데, 그러면 20개월짜리 계열이
    10개월 전과 비교된다. 4월을 전해 4월이 아니라 그해 6월과 재는 셈이라
    계절 성분이 하나도 지워지지 않는다. 계절성을 없애려고 도입한 방법이
    조용히 계절성을 재고 있었다.

    그래서 주기의 두 배가 안 되면 계산하지 않는다. 짧은 자료로 억지
    답을 내는 것보다 모른다고 하는 편이 낫다.
    """
    n = len(values)
    if n < 2 * period:
        return None
    last, prev = values[-period:], values[-2 * period:-period]
    base = sum(prev)
    if base <= 0 or base < min_total:
        return None

    # 전년 동기비 — 같은 때끼리 나누므로 계절 성분이 약분된다
    yoy = (sum(last) - base) / base
    ratio = [last[i] / prev[i] for i in range(period) if prev[i] > 0]
    if len(ratio) < 3:
        return None
    sd = st.pstdev(ratio)
    surge = (st.mean(ratio[-3:]) - st.mean(ratio)) / sd if sd > 1e-9 else 0.0

    return {"level": round(sum(last), 3), "yoy": round(yoy, 4),
            "surge": round(surge, 3), "periods": n}


MARKET_MIN = 8         # 이보다 적으면 '시장'이 무엇인지 알 수 없다


def market_adjust(all_axes: list[dict]) -> float:
    """소스 전체가 함께 움직인 몫을 구한다. 중앙값을 시장 수익률로 본다.

    이걸 빼지 않으면 플랫폼의 사정을 장소의 사정으로 읽는다. 한국어
    위키백과가 그랬다 — 106곳 중 94곳이 하락, 중앙값 -41.0%다. 서울의
    명소가 일제히 식었을 리 없고, 한국어권에서 위키백과를 찾는 일 자체가
    줄어든 것이다(영어판은 같은 기간 중앙값 +3.3%였다).

    그대로 두면 한국어 쪽은 전부 '식는 중'이 되어 아무 정보도 없고, 두
    소스를 나란히 놓으면 모든 장소가 '어긋난다'고 나온다. 실제로 어긋나는
    곳이 아니라 플랫폼이 다르다는 사실만 37번 반복해 읽는 셈이다.

    그래서 각 개체의 전년비에서 그 소스의 중앙값을 뺀다. 남는 것이
    **그 개체만의 몫**이고, 소스를 가로질러 비교할 수 있는 것도 이 값뿐이다.
    경제학에서 변이할당분석(shift-share)이라 부르는 분해와 같다.

    평균이 아니라 중앙값을 쓰는 이유는 청와대(+419%) 하나가 시장 전체를
    끌어올리기 때문이다. 112곳 기준으로 평균은 +12.0%, 중앙값은 +3.3%다.
    """
    ys = sorted(a["yoy"] for a in all_axes if a and not suspect(a))
    # 개체가 몇 개 없으면 시장을 뺄 수 없다. 하나뿐이면 중앙값이 곧
    # 그 자신이라 조정값이 0이 되고, 무엇이 오르든 '꾸준함'이 된다.
    # 모르는 것을 0으로 채우면 안 된다 — 조정 자체를 하지 않는다.
    if len(ys) < MARKET_MIN:
        return 0.0
    mid = len(ys) // 2
    return ys[mid] if len(ys) % 2 else (ys[mid - 1] + ys[mid]) / 2


def suspect(a: dict | None) -> bool:
    """한 해에 몇 배씩 뛴 값은 트렌드가 아니라 자료가 바뀐 자국이다.

    남산서울타워 한국어 문서가 전년비 +2,625%로 한국어 쪽 1위였다.
    27배다. 서울에서 두 번째로 유명한 탑이 한 해 만에 27배로 알려졌을
    리 없고, 문서 제목이 바뀌면서 조회수가 옛 제목과 갈린 것이다.

    어디서 끊을지는 자료가 말해 준다. 두 소스 218곳에서 27.3배가 홀로
    떨어져 있고 그다음이 5.2배(청와대 — 실제 뉴스였다)다. 사이가 비어
    있으니 6배에서 끊으면 인공물만 걸린다.

    이건 위키백과만의 문제가 아니다. 어떤 소스든 집계 방식이 바뀌거나
    항목이 새로 생기면 분모가 무너지고 비율이 폭발한다. 그래서 소스가
    아니라 엔진이 잡는다 — 데이터가 바뀌어도 남아 있어야 하는 방어다.
    """
    return bool(a) and a["yoy"] > MAX_RATIO - 1.0


def excess(a: dict | None) -> float:
    """시장을 뺀 그 개체만의 전년비. 없으면 원값을 쓴다."""
    if not a:
        return 0.0
    return a["rel"] if "rel" in a else a["yoy"]


LABEL = {
    "rising": "뜨는 중", "spike": "최근 급등", "peaked": "올랐다 진정",
    "steady": "꾸준함", "fading": "식는 중", "unknown": "자료 없음",
    "suspect": "기준 흔들림",
}


def classify(a: dict | None) -> str:
    """말로 옮긴다. 숫자만 주면 사용자가 판단할 수 없다.

    순서가 중요하다. **급등을 먼저 걸러야 한다** — 뉴스로 한 달 튄 곳도
    전년비는 크게 나오기 때문이다. 처음엔 '전년비가 크고 급등도 크면
    뜨는 중'으로 뒀는데, 그러면 두 가지가 반대로 잡혔다.

      뉴스 스파이크가 '뜨는 중'이 된다 — 전년비 조건을 먼저 통과한다.
      꾸준히 오르는 곳이 안 잡힌다 — 매기간 같은 비율로 늘면 비율의
        분산이 0이라 급등이 0이 된다. 가장 뚜렷한 상승인데 걸러진다.

    둘의 차이는 '늘어난 양'이 아니라 **'최근에 몰렸는가'**다.
    """
    if not a:
        return "unknown"
    if suspect(a):
        return "suspect"         # 자료가 바뀐 자국이다 — 트렌드가 아니다
    y = excess(a)                # 시장이 함께 움직인 몫은 빼고 본다
    if a["surge"] >= 1.2:
        return "spike"           # 최근 몇 기간에 몰렸다 = 뉴스일 가능성
    if y >= 0.15:
        # 전년비만 보면 청와대가 +419%로 1위다. 그런데 급등이 -1.15 —
        # 오른 건 작년 일이고 최근 석 달은 제 평균보다 낮다. 이미 지나간
        # 상승을 '뜨는 중'이라 적으면 오늘 갈 곳을 잘못 고르게 된다.
        return "rising" if a["surge"] >= -0.5 else "peaked"
    if y <= -0.15:
        return "fading"
    return "steady"


def score(a: dict, max_log: float) -> dict:
    """세 축을 0~1로 옮긴다. 합치지 않는다 — 뜻이 다른 값이다.

    `max_log`는 **같은 소스 안에서** 구한 최댓값이어야 한다. 조회수 5만과
    소비 비율 44%를 한 자로 재면 아무 뜻도 없는 수가 나온다.
    """
    lv = math.log1p(max(a["level"], 0)) / max_log if max_log else 0.0
    return {
        "level": round(max(0.0, min(1.0, lv)), 3),
        # 전년비는 ±50%를 양 끝으로 본다. 두 배로 뛰는 곳은 드물고,
        # 드문 것에 눈금을 맞추면 나머지가 전부 가운데로 뭉친다.
        "momentum": round(max(0.0, min(1.0, (excess(a) + 0.5) / 1.0)), 3),
        "surge": round(max(0.0, min(1.0, (a["surge"] + 2.0) / 4.0)), 3),
    }


DIVERGE_MIN = 0.30     # 전년비가 이만큼 어긋나면 볼 만하다


def divergence(a: dict, b: dict) -> dict:
    """두 소스가 같은 개체를 두고 다른 말을 하는 정도.

    이게 이 모듈에서 가장 쓸모 있는 값이다. 한 소스만 보면 순위표가
    나오지만, 두 소스가 어긋나는 지점에는 이유가 있다.

      관심은 느는데 방문이 안 는다   →  아직 안 간 곳. 지금이 기회다
      방문은 느는데 관심이 준다     →  이미 아는 곳. 검색할 이유가 없다

    북촌한옥마을이 두 번째다. 조회수는 40.8% 줄었는데 실측 방문은 늘었다.
    조회수만 보면 '식는 중'이라 추천에서 빼게 되는데, 사실은 그 반대다.
    """
    gap = excess(a) - excess(b)
    return {"gap": round(gap, 4), "notable": abs(gap) >= DIVERGE_MIN,
            "lead": "a" if gap > 0 else "b"}


# ----------------------------------------------------------------- 소스
#
# 시계열을 가져오는 함수 하나가 곧 소스다. 엔진을 건드리지 않고 꽂는다.

@dataclass
class Source:
    name: str
    kind: str                  # attention | visits | spend | search
    unit: str
    entity: str                # place | category
    min_total: float = 0.0
    period: int = PERIOD       # 계절 주기. 월 자료는 12, 분기 자료는 4.
    note: str = ""
    fetch: object = None


SOURCES: dict[str, Source] = {}


def source(**kw):
    """소스를 등록한다. 새 데이터를 붙이는 데 필요한 전부다."""
    def wrap(fn):
        SOURCES[kw["name"]] = Source(fetch=fn, **kw)
        return fn
    return wrap


def path_of(name: str) -> Path:
    return TREND_DIR / f"{name}.json"


# ------------------------------------------------- 소스 1 · 위키백과 조회수

PAGEVIEWS = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
             "/{wiki}.wikipedia/all-access/user/{title}/monthly/{start}/{end}")
WIKI_API = "https://{wiki}.wikipedia.org/w/api.php"
UA = {"User-Agent": "weatherfit-seoul/1.0 (tourism course planner; "
                    "https://github.com/tradeprogram/weatherfit_seoul)"}

GEO_MAX_KM = 1.5       # 지점 단위 명소다. 인기도(3km)보다 좁게 본다.
GEO_WIDE_KM = 6.0      # 산·하천·공원은 넓다. 아래 설명 참조.

# 넓게 퍼진 지형. 문서 좌표는 산이면 정상, 하천이면 중간쯤을 찍는데
# 관광 데이터는 사람이 들어가는 입구를 찍는다. 청계천은 11km짜리
# 하천이라 둘이 2.4km 어긋나는 게 정상인데, 지점 기준으로 재면
# '다른 장소'가 되어 멀쩡한 매칭이 떨어진다.
_WIDE = re.compile(r"(산|천|강|공원|호수|섬|숲|길|도성|계곡|저수지)$")


def _max_km(title: str) -> float:
    return GEO_WIDE_KM if _WIDE.search(title.strip()) else GEO_MAX_KM


def _window(now=None) -> tuple[str, str]:
    """진행 중인 달은 뺀다. 며칠치만 집계돼 있어 '급감'으로 보인다."""
    t = now or time.gmtime()
    return f"{t.tm_year - 2}{t.tm_mon:02d}0100", f"{t.tm_year}{t.tm_mon:02d}0100"


def _views(title: str, wiki: str, session, tries: int = 3) -> list[float]:
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
            return [float(i["views"]) for i in r.json().get("items", [])
                    if i["timestamp"][:6] < cur]
        time.sleep(0.8 * (k + 1))
    return []


def _translate(ko_title: str, wiki: str, session) -> str:
    """한국어 문서 제목 → 다른 어권 제목. 없으면 빈 문자열."""
    if wiki == "ko":
        return ko_title
    try:
        r = session.get(WIKI_API.format(wiki="ko"), timeout=20, params={
            "action": "query", "prop": "langlinks", "titles": ko_title,
            "lllang": wiki, "format": "json", "redirects": 1})
        r.raise_for_status()
        for pg in (r.json().get("query", {}).get("pages") or {}).values():
            for ll in pg.get("langlinks") or []:
                return ll.get("*", "")
    except Exception:
        pass
    return ""


def _coords(session, wiki: str, titles: list[str]) -> dict:
    from .popularity import _coords_batch
    import weatherfit.popularity as _pop

    keep, out = _pop.WIKI_API, {}
    _pop.WIKI_API = WIKI_API.format(wiki=wiki)
    try:
        for i in range(0, len(titles), 50):
            out.update(_coords_batch(session, titles[i:i + 50]))
            time.sleep(0.15)
    finally:
        _pop.WIKI_API = keep
    return out


def _verify_wiki(rows: dict, wiki: str, session, verbose: bool = True) -> int:
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
    좌표가 있어도 거리를 넘으면 다른 장소다.
    """
    from .index import build_index
    from .report import load
    from .routing import haversine_m

    where = {p.cid: (p.lat, p.lon)
             for p in build_index(load()).places if p.lat and p.lon}
    titles = sorted({r["ref"] for r in rows.values() if r.get("ref")})
    coords = _coords(session, wiki, titles)

    kept = 0
    for cid, r in rows.items():
        at, here = coords.get(r.get("ref", "")), where.get(cid)
        if not at or not here:
            r["why"] = "좌표 없는 문서 · 장소가 아닐 수 있다"
        else:
            km = haversine_m(here[0], here[1], at[0], at[1]) / 1000.0
            r["geo_km"] = round(km, 2)
            cap = _max_km(r.get("label", ""))
            r["why"] = "" if km <= cap else f"{km:,.1f}km 떨어진 문서"
        if r["why"]:
            r["values"] = []
        else:
            kept += 1
    if verbose:
        print(f"  좌표 검증 통과 {kept}/{len(titles)}건")
    return kept


@source(name="wikipedia", kind="attention", unit="월간 조회수", entity="place",
        min_total=240,
        note="'가는 사람'이 아니라 '새로 알아보는 사람'을 잰다. "
             "영어판을 쓴다 — 외국인 관광객이 보는 쪽이다.")
def _fetch_wikipedia(lang: str = "en", limit: int | None = None,
                     delay: float = 0.25, verbose: bool = True) -> dict:
    """검증된 위키 매칭이 있는 장소의 24개월 시계열.

    영어판을 기본으로 두는 이유: 이 서비스가 보는 사람은 외국인 관광객이다.
    북촌한옥마을은 영어판이 한국어판의 128배인데, 한국어로 재면 '거의
    안 알려진 곳'이 된다.
    """
    from .popularity import load_cache

    pop = load_cache()
    targets = [(cid, v) for cid, v in pop.items()
               if v.get("wiki_title") and v.get("geo_ok", True)]
    if limit:
        targets = targets[:limit]

    se = requests.Session()
    se.headers.update(UA)
    wiki = lang if lang in ("en", "ko", "ja") else "en"

    rows: dict[str, dict] = {}
    for n, (cid, v) in enumerate(targets, 1):
        ref = _translate(v["wiki_title"], wiki, se)
        rows[cid] = {"label": v.get("title", ""), "ref": ref, "why": "",
                     "values": _views(ref, wiki, se) if ref else []}
        time.sleep(delay)
        if verbose and n % 40 == 0:
            print(f"  {n}/{len(targets)}", flush=True)

    _verify_wiki(rows, wiki, se, verbose)
    return rows


# ------------------------------------------- 소스 2 · 위키백과 한국어판

@source(name="wikipedia_ko", kind="attention", unit="월간 조회수(한국어)",
        entity="place", min_total=240,
        note="같은 장소를 한국인 쪽에서 잰다. 영어판과 어긋나는 곳이 곧 "
             "'내국인은 아는데 외국인은 모르는 곳'이거나 그 반대다.")
def _fetch_wikipedia_ko(**kw) -> dict:
    """소스를 하나 더 붙이는 데 든 코드가 이게 전부다.

    영어판과 **같은 개체**를 재기 때문에 비로소 비교가 성립한다. 소비
    통계는 업종이라 장소와 겹치는 개체가 하나도 없어서, 소스가 둘이어도
    `diverge`가 볼 것이 없었다. 축을 늘리는 것과 비교할 수 있게 되는 것은
    다른 일이다.
    """
    kw["lang"] = "ko"
    return _fetch_wikipedia(**kw)


# --------------------------------------- 소스 3 · 한국관광데이터랩 한류 소비

DATALAB_DIR = ROOT / "data" / "datalab"


@source(name="datalab_spend", kind="spend", unit="업종별 비율(%)",
        entity="category",
        note="장소가 아니라 업종이다. 개별 명소는 못 보지만 '무엇에 돈을 "
             "쓰는가'가 바뀌는 건 보인다.")
def _fetch_datalab(verbose: bool = True, **_) -> dict:
    """한국관광데이터랩 내려받기 CSV. 열은 기준년(월) · 업종 · 비율.

    소스가 파일이라 네트워크가 필요 없다. 새 CSV를 같은 폴더에 넣으면
    엔진은 그대로 두고 값만 갱신된다.
    """
    got: dict[str, dict] = {}
    files = sorted(DATALAB_DIR.glob("*업종별 추이.csv"))
    for f in files:
        which = "소비액" if "소비액" in f.name else "소비건수"
        by: dict[str, list] = {}
        with f.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                cat = (row.get("업종") or "").strip()
                per = (row.get("기준년(월)") or "").strip()
                try:
                    val = float(row.get("비율") or 0)
                except ValueError:
                    continue
                if cat and per:
                    by.setdefault(cat, []).append((per, val))
        for cat, pairs in by.items():
            pairs.sort()
            got[f"{which}·{cat}"] = {
                "label": f"{cat} ({which})", "ref": f.name, "why": "",
                "values": [v for _, v in pairs]}
    if verbose:
        print(f"  {len(files)}개 파일 · {len(got)}개 계열")
    return got


# ----------------------------------------------------------------- 수집

def _finish(rows: dict) -> float:
    """축이 나온 뒤의 뒷일 — 시장 조정, 판정, 정규화.

    수집과 나눠 둔다. 문턱 하나 고칠 때마다 292곳을 6분씩 다시 받는 건
    말이 안 되고, 무엇보다 **같은 원자료에서 같은 답이 나오는지**를
    확인할 수 없게 된다.
    """
    have = [r["axes"] for r in rows.values() if r.get("axes")]
    # 시장 효과를 먼저 뗀다. 이걸 빼야 소스를 가로질러 비교할 수 있다.
    market = market_adjust(have)
    for a in have:
        a.pop("rel", None)
        if len(have) >= MARKET_MIN:
            a["rel"] = round(a["yoy"] - market, 4)
    for r in rows.values():
        r["trend"] = classify(r.get("axes"))       # 조정값으로 다시 판정한다

    # 정규화는 소스 **안에서만** 한다. 단위가 다른 값을 한 자로 재면 안 된다.
    max_log = max((math.log1p(max(a["level"], 0)) for a in have),
                  default=0.0) or 1.0
    for r in rows.values():
        r.pop("score", None)
        if r.get("axes"):
            r["score"] = score(r["axes"], max_log)
    return market


def recompute(name: str, verbose: bool = True) -> Path:
    """이미 받아 둔 축으로 뒷일만 다시 한다. 네트워크를 타지 않는다."""
    t = table(name)
    if not t.get("series"):
        raise SystemExit(f"먼저 수집해야 합니다: "
                         f"python -m weatherfit.momentum build {name}")
    rows = t["series"]
    market = _finish(rows)
    have = [r for r in rows.values() if r.get("axes")]
    t["meta"].update({"market": round(market, 4),
                      "market_adjusted": len(have) >= MARKET_MIN,
                      "recomputed_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                     time.gmtime())})
    path_of(name).write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    reset()
    if verbose:
        print(f"{name}: {len(have)}건 재계산 · 시장 {market * 100:+.1f}%")
    return path_of(name)



def build(name: str, verbose: bool = True, **kw) -> Path:
    """한 소스를 수집해 저장한다. 엔진은 소스가 무엇인지 모른다."""
    src = SOURCES.get(name)
    if not src:
        raise SystemExit(f"모르는 소스입니다: {name} "
                         f"(있는 것: {', '.join(SOURCES)})")

    rows = src.fetch(verbose=verbose, **kw)
    lengths = []
    for r in rows.values():
        vals = r.get("values") or []
        if vals:
            lengths.append(len(vals))
        a = axes(vals, src.min_total, src.period)
        r.pop("values", None)          # 원계열은 무겁다. 축만 남긴다
        r["axes"] = a
        r["trend"] = classify(a)

    market = _finish(rows)
    have = [r["axes"] for r in rows.values() if r["axes"]]

    TREND_DIR.mkdir(parents=True, exist_ok=True)
    out = path_of(name)
    out.write_text(json.dumps({
        "meta": {"source": name, "kind": src.kind, "unit": src.unit,
                 "entity": src.entity, "note": src.note,
                 "built_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                 "rows": len(rows), "computed": len(have),
                 "market": round(market, 4),
                 "market_adjusted": len(have) >= MARKET_MIN,
                 # 0건이 나왔을 때 왜인지 말할 수 있어야 한다. 자료가
                 # 짧아서인지, 매칭이 안 돼서인지, 잡음 문턱에 걸려서인지.
                 "needs": 2 * src.period, "longest": max(lengths, default=0),
                 "with_series": len(lengths)},
        "series": rows}, ensure_ascii=False), encoding="utf-8")
    if verbose:
        print(f"완료 {len(have)}/{len(rows)}건 → {out}")
    return out


# ----------------------------------------------------------------- 조회

_cache: dict[str, dict] = {}


def table(name: str) -> dict:
    if name not in _cache:
        try:
            _cache[name] = json.loads(path_of(name).read_text(encoding="utf-8"))
        except Exception:
            _cache[name] = {"meta": {}, "series": {}}
    return _cache[name]


def reset() -> None:
    _cache.clear()


def of(entity: str, name: str = "wikipedia") -> dict | None:
    """한 개체의 트렌드. 없으면 None — 모르면 모른다고 한다."""
    row = (table(name).get("series") or {}).get(entity)
    return row if row and row.get("score") else None


def compare(entity: str, a: str, b: str) -> dict | None:
    """두 소스가 같은 개체를 두고 하는 말을 나란히 놓는다."""
    ra, rb = of(entity, a), of(entity, b)
    if not (ra and rb):
        return None
    return {"entity": entity, a: ra["axes"], b: rb["axes"],
            **divergence(ra["axes"], rb["axes"])}


def _why_empty(m: dict) -> str:
    """한 건도 계산되지 않은 이유를 말한다.

    빈 결과를 그냥 두면 고장으로 보인다. 자료가 짧아서 못 낸 것과
    코드가 잘못된 것은 완전히 다른 일인데, 화면에는 똑같이 0으로 나온다.
    """
    need, longest = m.get("needs", 0), m.get("longest", 0)
    out = [f"{m.get('source')}: {m.get('rows')}건 중 계산된 것이 없습니다."]
    if not m.get("with_series"):
        out.append("  시계열을 하나도 못 받았습니다 — 소스 연결을 보세요.")
    elif longest < need:
        out += [f"  가장 긴 계열이 {longest}기간인데 {need}기간이 필요합니다.",
                "  전년 동기비는 같은 달끼리 비교해야 계절성이 지워집니다.",
                f"  {need - longest}기간을 더 받아 오면 그대로 계산됩니다."]
    else:
        out.append("  잡음 문턱(min_total)에 전부 걸렸습니다.")
    return chr(10).join(out)



def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    from collections import Counter

    ap = argparse.ArgumentParser(description="트렌드 엔진")
    ap.add_argument("action",
                    choices=["sources", "build", "recompute", "stats",
                             "diverge"])
    ap.add_argument("name", nargs="?", default="wikipedia")
    ap.add_argument("--against", default="datalab_spend")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    if a.action == "sources":
        print(f"{len(SOURCES)}개 소스가 꽂혀 있습니다\n")
        for s in SOURCES.values():
            t = table(s.name).get("meta", {})
            if not t:
                state = "아직 수집 안 함"
            else:
                state = f"{t.get('computed')}/{t.get('rows')}건 · "                         f"{t.get('built_at', '')[:10]}"
                if not t.get("computed"):
                    state += f" · 자료 {t.get('longest')}/{t.get('needs')}기간"
            print(f"  {s.name:16} {s.kind:10} {s.entity:9} {state}")
            print(f"  {'':16} {s.unit} — {s.note}\n")
        return

    if a.action == "recompute":
        recompute(a.name)
        return

    if a.action == "build":
        kw = ({"limit": a.limit} if a.name.startswith("wikipedia") else {})
        if a.name == "wikipedia":
            kw["lang"] = a.lang
        build(a.name, **kw)
        return

    if a.action == "diverge":
        one, two = table(a.name), table(a.against)
        rows = [(e, one["series"][e], two["series"][e])
                for e in set(one.get("series", {})) & set(two.get("series", {}))
                if one["series"][e].get("score") and two["series"][e].get("score")]
        print(f"{a.name} ↔ {a.against} · 양쪽 다 계산된 개체 {len(rows)}개")
        if not rows:
            print()
            print("겹치는 개체가 없습니다. 한쪽이 장소고 한쪽이 업종이면")
            print("같은 자로 잴 대상이 없습니다 — 축을 하나 더 만드는 것과")
            print("비교할 수 있게 되는 것은 다른 일입니다.")
            return

        scored = sorted(
            ((e, x, y, divergence(x["axes"], y["axes"])) for e, x, y in rows),
            key=lambda t: -abs(t[3]["gap"]))
        hit = [t for t in scored if t[3]["notable"]]
        print(f"뚜렷하게 어긋나는 곳 {len(hit)}개 "
              f"(전년비 {DIVERGE_MIN * 100:.0f}%p 이상)")
        for e, x, y, d in hit[:12]:
            lead = a.name if d["lead"] == "a" else a.against
            print(f"  {x['label'][:18]:20} {a.name} {excess(x['axes']) * 100:+7.1f}% · "
                  f"{a.against} {excess(y['axes']) * 100:+7.1f}% → {lead} 쪽이 앞선다")
        agree = len(scored) - len(hit)
        print()
        print(f"나머지 {agree}개는 두 소스가 같은 방향을 말합니다.")
        return

    t = table(a.name)
    m = t.get("meta") or {}
    rows = [r for r in (t.get("series") or {}).values() if r.get("score")]
    if not rows:
        if not m:
            raise SystemExit(f"아직 수집하지 않았습니다. "
                             f"python -m weatherfit.momentum build {a.name}")
        raise SystemExit(_why_empty(m))
    print(f"{m.get('source')} · {m.get('kind')} · {m.get('unit')} · "
          f"{m.get('computed')}/{m.get('rows')}건")
    if m.get("market_adjusted"):
        print(f"이 소스 전체가 {m['market'] * 100:+.1f}% 움직였습니다. "
              f"아래 전년비는 그 몫을 뺀 값입니다.")
    print("분포:", {LABEL[k]: v for k, v in
                  Counter(r["trend"] for r in rows).items()})
    for kind in ("rising", "spike", "peaked", "fading", "suspect"):
        got = sorted((r for r in rows if r["trend"] == kind),
                     key=lambda r: -abs(excess(r["axes"])))
        if not got:
            continue
        print()
        print(f"{LABEL[kind]} ({len(got)}개)")
        for r in got[:8]:
            x = r["axes"]
            raw = (f" (원값 {x['yoy'] * 100:+.1f}%)"
                   if "rel" in x and abs(x["rel"] - x["yoy"]) > 0.01 else "")
            print(f"  {r['label'][:24]:26} 전년비 {excess(x) * 100:+7.1f}% · "
                  f"급등 {x['surge']:+5.2f} · 수준 {x['level']:>9,.0f}{raw}")


if __name__ == "__main__":
    main()
