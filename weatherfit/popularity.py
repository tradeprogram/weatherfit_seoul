"""인기도 — '관광지 중의 관광지'를 가리는 외부 신호.

비짓서울 API에는 평점도 방문자 수도 없다. 그래서 밖에서 가져온다.
문제는 흔히 기대하는 곳에 데이터가 없다는 점이다.

    카카오맵    로컬 API는 이름·주소·좌표·카테고리만 준다. **평점 없음.**
                평점은 웹에만 있고 크롤링은 약관 위반이라 쓰지 않는다.
                대신 '카카오맵에 등재된 장소인가'와 카테고리 정밀도를 얻는다.
    네이버      지역 검색 API도 평점을 주지 않는다. 데이터랩 검색어 트렌드는
                상대 검색량을 주므로 관심도의 대리 지표가 된다.
    구글        Places API는 평점과 리뷰 수를 준다. 유료(과금 계정 필요).
    위키백과    **키가 필요 없고 실측 조회수를 준다.** 문서가 있다는 것 자체가
                '알려진 곳'이라는 신호이고, 월별 조회수는 관심의 크기다.
                롯데월드타워 14,191 · 경복궁 9,266 · 리움 493 (3개월)

그래서 위키백과를 기본 신호로 쓰고 나머지는 키가 있을 때 더한다.

조회는 느리다(3,788건 × 2회 호출). 요청 경로에서 하지 않는다.

    python -m weatherfit.popularity build          # data/popularity.json 생성
    python -m weatherfit.popularity build --limit 200
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from difflib import SequenceMatcher
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "popularity.json"
UA = {"User-Agent": "weatherfit-seoul/1.0 (tourism course planner; "
                    "https://github.com/tradeprogram/weatherfit_seoul)"}

WIKI_API = "https://ko.wikipedia.org/w/api.php"
PAGEVIEWS = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
             "/ko.wikipedia/all-access/user/{title}/monthly/{start}/{end}")

# 제목 매칭 임계값. 낮추면 "성수연방 → 박정희" 같은 오매칭이 들어온다.
MATCH_MIN = 0.55

_BRANCH = re.compile(r"\s*(본점|[가-힣A-Za-z0-9]+점|\d+호점|지점)\s*$")
_PAREN = re.compile(r"\([^)]*\)")
_NOISE = re.compile(r"[\[\]<>《》〈〉·:：,，/]")


def normalize(title: str) -> str:
    """지점명·괄호·기호를 걷어낸 핵심 이름."""
    t = _PAREN.sub(" ", title)
    t = _NOISE.sub(" ", t)
    t = _BRANCH.sub("", t.strip())
    return re.sub(r"\s+", " ", t).strip()


def similar(a: str, b: str) -> float:
    a2, b2 = normalize(a).replace(" ", ""), normalize(b).replace(" ", "")
    if not a2 or not b2:
        return 0.0
    if a2 == b2:
        return 1.0
    if a2 in b2 or b2 in a2:
        # 포함은 신호지만 확신은 아니다. '히말라야'가 '히말라야산맥'에
        # 들어 있다고 종각의 카레집이 산맥인 것은 아니다. 길이 차이만큼 깎는다.
        return min(len(a2), len(b2)) / max(len(a2), len(b2))
    return SequenceMatcher(None, a2, b2).ratio()


@dataclass
class Popularity:
    cid: str
    title: str
    wiki_title: str = ""
    wiki_views: int = 0            # 최근 3개월 합계
    match: float = 0.0
    geo_km: float = -1.0           # 위키 문서 좌표와의 거리. -1이면 확인 못 함
    geo_ok: bool = True            # 좌표·이름 검증을 통과했는가
    kakao_id: str = ""
    kakao_category: str = ""
    google_rating: float = 0.0
    google_reviews: int = 0
    naver_trend: float = 0.0       # 0~100 상대 검색량
    score: float = 0.0             # 0~1로 정규화한 최종 인기도
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------- 위키백과

class WikiProvider:
    """키가 필요 없는 기본 신호. 문서 존재 + 월별 조회수."""

    name = "wikipedia"

    def __init__(self, session: requests.Session, timeout: int = 12):
        self.s = session
        self.timeout = timeout

    def resolve(self, title: str) -> tuple[str, float]:
        """장소 이름 → 위키백과 문서 제목. 애매하면 빈 문자열."""
        q = normalize(title)
        if len(q) < 2:
            return "", 0.0
        try:
            r = self.s.get(WIKI_API, timeout=self.timeout, params={
                "action": "query", "list": "search", "srsearch": q,
                "format": "json", "srlimit": 3,
            })
            r.raise_for_status()
            hits = r.json().get("query", {}).get("search", [])
        except Exception:
            return "", 0.0

        best, best_score = "", 0.0
        for h in hits:
            sc = similar(q, h["title"])
            if sc > best_score:
                best, best_score = h["title"], sc
        return (best, best_score) if best_score >= MATCH_MIN else ("", best_score)

    def views(self, wiki_title: str, months: int = 3) -> int:
        if not wiki_title:
            return 0
        now = time.gmtime()
        y, m = now.tm_year, now.tm_mon
        sm = m - months
        sy = y
        while sm <= 0:
            sm += 12
            sy -= 1
        url = PAGEVIEWS.format(
            title=requests.utils.quote(wiki_title.replace(" ", "_"), safe=""),
            start=f"{sy}{sm:02d}0100", end=f"{y}{m:02d}0100")
        try:
            r = self.s.get(url, timeout=self.timeout)
            if r.status_code != 200:
                return 0
            return sum(i["views"] for i in r.json().get("items", []))
        except Exception:
            return 0


# ----------------------------------------------------------------- 카카오

class KakaoProvider:
    """카카오 로컬 — 평점은 주지 않는다. 등재 여부와 카테고리 정밀도만 얻는다."""

    name = "kakao"
    URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

    def __init__(self, session: requests.Session, key: str, timeout: int = 10):
        self.s, self.key, self.timeout = session, key, timeout

    def lookup(self, title: str, lat: float, lon: float) -> dict:
        try:
            r = self.s.get(self.URL, timeout=self.timeout,
                           headers={"Authorization": f"KakaoAK {self.key}"},
                           params={"query": normalize(title), "x": lon, "y": lat,
                                   "radius": 500, "size": 5, "sort": "accuracy"})
            r.raise_for_status()
            docs = r.json().get("documents", [])
        except Exception:
            return {}
        for d in docs:
            if similar(title, d.get("place_name", "")) >= MATCH_MIN:
                return {"kakao_id": d.get("id", ""),
                        "kakao_category": d.get("category_name", "")}
        return {}


# ----------------------------------------------------------------- 구글

class GoogleProvider:
    """Places API — 평점과 리뷰 수를 준다. 과금 계정이 필요하다."""

    name = "google"
    URL = "https://places.googleapis.com/v1/places:searchText"

    def __init__(self, session: requests.Session, key: str, timeout: int = 12):
        self.s, self.key, self.timeout = session, key, timeout

    def lookup(self, title: str, lat: float, lon: float) -> dict:
        try:
            r = self.s.post(self.URL, timeout=self.timeout, headers={
                "X-Goog-Api-Key": self.key,
                "X-Goog-FieldMask": "places.displayName,places.rating,"
                                    "places.userRatingCount",
                "Content-Type": "application/json",
            }, json={
                "textQuery": normalize(title),
                "languageCode": "ko",
                "locationBias": {"circle": {
                    "center": {"latitude": lat, "longitude": lon},
                    "radius": 500.0}},
                "maxResultCount": 3,
            })
            r.raise_for_status()
            places = r.json().get("places", [])
        except Exception:
            return {}
        for p in places:
            name = (p.get("displayName") or {}).get("text", "")
            if similar(title, name) >= MATCH_MIN:
                return {"google_rating": float(p.get("rating", 0) or 0),
                        "google_reviews": int(p.get("userRatingCount", 0) or 0)}
        return {}


# ------------------------------------------------------- 위키 매칭 검증

# 위키 문서 좌표가 장소에서 이만큼 이상 떨어져 있으면 다른 대상이다.
GEO_MAX_KM = 3.0
# 좌표가 없는 문서(인물·기념일·개념)는 이름이 거의 같을 때만 인정한다.
NAME_STRICT = 0.9
# 이 분류의 장소가 '좌표 없는 문서'에 걸리면 대개 이름만 같은 다른 것이다.
# 무궁화(꽃)·아리랑(민요)·나마스테(인사말)·중국(나라)에 식당 이름이 걸린다.
# 식당·가게가 정말로 위키백과에 실렸다면 그건 장소 문서이므로 좌표가 있다.
NEEDS_GEO = {"음식", "쇼핑", "숙박"}


def judge_match(title: str, wiki_title: str, category: str,
                km: float | None) -> tuple[bool, str]:
    """이 위키 문서를 이 장소의 것으로 인정할까. (인정, 이유)

    km은 문서 좌표와 장소의 거리. 어느 한쪽에 좌표가 없으면 None.
    """
    name_ok = similar(title, wiki_title) >= NAME_STRICT
    # 식당·가게·숙소와 '○○점' 지점은 이름이 겹치기 쉽다. 여기만 잣대를 높인다.
    strict = category in NEEDS_GEO or bool(_BRANCH.search(title.strip()))

    if km is not None:
        if km > GEO_MAX_KM:
            return False, f"{km:,.0f}km 떨어진 문서"
        if strict and not name_ok:
            # 광화문 옆 '광화문집'은 광화문 문서와 가깝지만 같은 대상이 아니다.
            return False, "가깝지만 이름이 다른 문서"
        return True, ""
    if strict:
        return False, "지점·가게 · 좌표로 확인 안 됨"
    return name_ok, "" if name_ok else "좌표 없는 문서 · 이름 불일치"


def _coords_batch(session: requests.Session, titles: list[str],
                  timeout: int = 15) -> dict:
    """문서 제목 → (위도, 경도) 또는 None. 50개씩 한 번에 묻는다."""
    try:
        r = session.get(WIKI_API, timeout=timeout, params={
            "action": "query", "prop": "coordinates", "format": "json",
            "titles": "|".join(titles), "redirects": 1, "coprimary": "primary",
            # colimit 기본값은 10이다. 50개를 물어도 앞 10개만 좌표가 온다.
            "colimit": "max",
        })
        r.raise_for_status()
        data = r.json().get("query", {}) or {}
    except Exception:
        return {t: None for t in titles}

    # 요청한 제목이 정규화·넘겨주기를 거쳐 다른 문서가 되는 경우를 따라간다
    alias = {}
    for kind in ("normalized", "redirects"):
        for m in data.get(kind) or []:
            alias[m["from"]] = m["to"]

    def final(t: str) -> str:
        seen = set()
        while t in alias and t not in seen:
            seen.add(t)
            t = alias[t]
        return t

    by_title = {}
    for pg in (data.get("pages") or {}).values():
        c = (pg.get("coordinates") or [None])[0]
        by_title[pg.get("title")] = (c["lat"], c["lon"]) if c else None
    return {t: by_title.get(final(t)) for t in titles}


def verify(delay: float = 0.1, verbose: bool = True) -> Path:
    """이미 만든 캐시의 위키 매칭을 좌표로 검증한다.

    이름만 보면 '히말라야 종각점'(카레집)이 히말라야산맥 문서에,
    '버뮤다삼각지'(술집)가 버뮤다 삼각지대 문서에 붙는다. 그러면 인기 점수가
    엉뚱한 곳을 밀어 올려 '관광지 중의 관광지'를 고르는 일 자체가 무너진다.

    문서에 좌표가 있으면 장소에서 얼마나 떨어졌는지로 판정하고,
    좌표가 없는 문서(인물·기념일·개념)는 이름이 거의 같을 때만 남긴다.
    """
    from .index import build_index
    from .report import load
    from .routing import haversine_m

    session = requests.Session()
    session.headers.update(UA)
    rows = {cid: Popularity(**v) for cid, v in load_cache().items()}
    if not rows:
        raise SystemExit("캐시가 없습니다. python -m weatherfit.popularity build")

    idx = build_index(load())
    where = {p.cid: (p.lat, p.lon) for p in idx.places if p.lat and p.lon}
    kind = {p.cid: p.content.category for p in idx.places}

    titled = [r for r in rows.values() if r.wiki_title]
    coords: dict = {}
    uniq = sorted({r.wiki_title for r in titled})
    for i in range(0, len(uniq), 50):
        coords.update(_coords_batch(session, uniq[i:i + 50]))
        time.sleep(delay)

    dropped = []
    for r in titled:
        art = coords.get(r.wiki_title)
        pos = where.get(r.cid)
        km = (haversine_m(pos[0], pos[1], art[0], art[1]) / 1000.0
              if art and pos else None)
        r.geo_km = round(km, 1) if km is not None else -1.0
        r.geo_ok, why = judge_match(r.title, r.wiki_title,
                                    kind.get(r.cid, ""), km)
        if not r.geo_ok:
            # 지우지 않고 표시만 한다. 지워 버리면 검증을 다시 돌릴 수도,
            # 왜 뺐는지 확인할 수도 없다. 점수 계산이 geo_ok를 본다.
            dropped.append((r.title, r.wiki_title, r.wiki_views, why))

    all_rows = list(rows.values())
    normalize_scores(all_rows)
    save_cache({r.cid: r for r in all_rows})

    if verbose:
        kept = sum(1 for r in all_rows if r.wiki_title and r.geo_ok)
        print(f"검증 {len(titled)}건 · 유지 {kept}건 · 제외 {len(dropped)}건")
        for t, w, v, why in sorted(dropped, key=lambda x: -x[2])[:15]:
            print(f"  ✗ {t[:24]:24} → {w[:16]:16} 조회 {v:>7,}  {why}")
    return CACHE


# ----------------------------------------------------------------- 점수

def normalize_scores(rows: list[Popularity]) -> None:
    """수집한 원값들을 0~1로 정규화해 score에 채운다.

    조회수는 편차가 크다(63 ~ 14,191). 로그를 씌워야 상위 몇 곳이
    나머지를 전부 눌러 버리지 않는다.
    """
    import math

    max_log = max((math.log1p(r.wiki_views) for r in rows if r.geo_ok),
                  default=0.0) or 1.0
    max_rev = max((math.log1p(r.google_reviews) for r in rows), default=0.0) or 1.0

    for r in rows:
        parts, weights = [], []
        if r.wiki_views and r.geo_ok:
            parts.append(math.log1p(r.wiki_views) / max_log)
            weights.append(0.55)
        elif r.wiki_title and r.geo_ok:
            parts.append(0.25)             # 문서는 있으나 조회가 적은 곳
            weights.append(0.25)
        if r.google_rating:
            parts.append(min(r.google_rating / 5.0, 1.0))
            weights.append(0.25)
            parts.append(math.log1p(r.google_reviews) / max_rev)
            weights.append(0.15)
        if r.naver_trend:
            parts.append(min(r.naver_trend / 100.0, 1.0))
            weights.append(0.20)
        if r.kakao_id:
            parts.append(0.5)              # 등재돼 있다는 사실 자체
            weights.append(0.05)

        r.score = round(sum(p * w for p, w in zip(parts, weights)) / sum(weights), 4) \
            if weights else 0.0


# ----------------------------------------------------------------- 수집

def build(limit: int | None = None, delay: float = 0.12,
          only_missing: bool = True) -> Path:
    from .index import build_index
    from .quality import is_touristic
    from .report import load

    cache = load_cache()
    session = requests.Session()
    session.headers.update(UA)
    wiki = WikiProvider(session)

    kakao_key = os.environ.get("KAKAO_REST_KEY", "")
    google_key = os.environ.get("GOOGLE_PLACES_KEY", "")
    kakao = KakaoProvider(session, kakao_key) if kakao_key else None
    google = GoogleProvider(session, google_key) if google_key else None

    idx = build_index(load())
    targets = [p for p in idx.places if is_touristic(p)]
    if only_missing:
        targets = [p for p in targets if p.cid not in cache]
    if limit:
        targets = targets[:limit]

    print(f"대상 {len(targets)}건 "
          f"(위키 기본{', 카카오' if kakao else ''}{', 구글' if google else ''})",
          flush=True)

    rows = {cid: Popularity(**v) for cid, v in cache.items()}
    for n, p in enumerate(targets, 1):
        row = Popularity(cid=p.cid, title=p.content.title)
        title, score = wiki.resolve(p.content.title)
        row.wiki_title, row.match = title, round(score, 3)
        if title:
            row.wiki_views = wiki.views(title)
            row.sources.append("wikipedia")
        if kakao and p.lat:
            got = kakao.lookup(p.content.title, p.lat, p.lon)
            if got:
                row.__dict__.update(got)
                row.sources.append("kakao")
        if google and p.lat:
            got = google.lookup(p.content.title, p.lat, p.lon)
            if got:
                row.__dict__.update(got)
                row.sources.append("google")
        rows[p.cid] = row
        time.sleep(delay)
        if n % 50 == 0:
            print(f"  {n}/{len(targets)}", flush=True)
            save_cache(rows)

    all_rows = list(rows.values())
    normalize_scores(all_rows)
    save_cache({r.cid: r for r in all_rows})

    hit = sum(1 for r in all_rows if r.wiki_title)
    print(f"완료 {len(all_rows)}건 · 위키 매칭 {hit}건 "
          f"({hit / max(len(all_rows), 1) * 100:.1f}%) → {CACHE}")
    return CACHE


def save_cache(rows: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    data = {cid: (r.to_dict() if isinstance(r, Popularity) else r)
            for cid, r in rows.items()}
    CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_cache() -> dict:
    if not CACHE.exists():
        return {}
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_scores: dict[str, float] | None = None
_notes: dict[str, str] | None = None


def scores() -> dict[str, float]:
    """cid → 0~1 인기도. 캐시가 없으면 빈 사전(전부 0으로 동작)."""
    global _scores
    if _scores is None:
        _scores = {cid: float(v.get("score", 0.0))
                   for cid, v in load_cache().items()}
    return _scores


def notes() -> dict[str, str]:
    """cid → 왜 '알려진 곳'인지 한 줄.

    적재 때 한 번만 만든다. 근거 한 줄을 위해 1MB짜리 캐시를 매번 다시
    읽으면 일정 한 번에 서너 번씩 파싱하게 된다.
    """
    global _notes
    if _notes is None:
        out = {}
        for cid, v in load_cache().items():
            if not v.get("geo_ok", True) or not v.get("wiki_title"):
                continue
            views = int(v.get("wiki_views") or 0)
            out[cid] = (f"위키백과 3개월 {views:,}회 조회" if views
                        else "위키백과에 문서가 있음")
        _notes = out
    return _notes


def reset() -> None:
    global _scores, _notes
    _scores = _notes = None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    ap = argparse.ArgumentParser(description="인기도 캐시 생성")
    ap.add_argument("action", choices=["build", "verify", "stats"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--all", action="store_true", help="이미 받은 것도 다시")
    args = ap.parse_args()

    if args.action == "build":
        build(limit=args.limit, only_missing=not args.all)
    elif args.action == "verify":
        verify()
    else:
        c = load_cache()
        got = [v for v in c.values() if v.get("wiki_title")]
        ok = [v for v in got if v.get("geo_ok", True)]
        geo = [v for v in ok if v.get("geo_km", -1) >= 0]
        print(f"캐시 {len(c)}건 · 위키 매칭 {len(got)}건 → 검증 통과 {len(ok)}건 "
              f"(그중 좌표로 확인 {len(geo)}건)")
        top = sorted(c.values(), key=lambda v: -v.get("score", 0))[:15]
        for v in top:
            print(f"  {v['score']:.3f}  {v['title'][:28]:28} "
                  f"조회 {v.get('wiki_views', 0):,}")


if __name__ == "__main__":
    main()
