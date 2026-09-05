"""웨더핏 서울 백엔드.

    python -m weatherfit.server            # http://127.0.0.1:8020

키가 없어도 전부 동작한다. 기상청 키가 없으면 기본 날씨로, 경로 키가 없으면
직선거리 추정으로, LLM 키가 없으면 규칙 기반으로 떨어진다. 어느 쪽이 쓰였는지는
응답의 source/provider/engine 필드에 항상 실린다.

정규화와 행정동 매칭은 적재 시점에 한 번만 한다(index.build_index).
요청 경로에서 3,788건을 다시 파싱하면 판정 한 번에 120ms가 들기 때문이다.
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .chat import LANDMARKS, Intent, compose_reply, parse_intent
from .i18n import deep_en, localize, to_en
from .momentum import badge as trend_badge
from .popularity import scores as popularity_scores
from .taste import PARTY_AVOID, PARTY_TAGS, Taste, mood_interests
from .agent import compose, run_agent
from .trend import STYLES, TrendProfile, service_axes
from .course import build_course
from .index import Index, build_index
from .llm import LLM
from .models import LANG_LABEL, LANGS
from .report import load as load_items
from .routing import haversine_m, router
from .validate import Weather, check_period, evaluate_place
from .weather import SEOUL_CITY_HALL, get_weather

# 이 비율을 넘겨야 그 어권을 '지원한다'고 말한다
LANG_MIN_COVERAGE = 0.5

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# 서울 대략 범위. 밖이면 사용자에게 알려 준다.
SEOUL_BOUNDS = (37.41, 126.73, 37.72, 127.19)   # s, w, n, e

app = FastAPI(title="웨더핏 서울", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

STATE: dict[str, Any] = {"index": None, "dong": None, "stats_cache": {}}


def dong_gdf():
    """행정동 경계. 없으면 None. (GeoDataFrame은 진릿값이 모호해 `or`를 못 쓴다)"""
    if STATE["dong"] is None:
        try:
            from .geo import load_dong_index
            STATE["dong"] = load_dong_index()
        except Exception:
            STATE["dong"] = False          # 다시 시도하지 않는다는 표시
    return None if STATE["dong"] is False else STATE["dong"]


def index() -> Index:
    if STATE["index"] is None:
        # 한국어를 기준으로 두고 다른 어권은 텍스트만 덮어씌운다.
        # 좌표·운영시간·기간은 언어와 무관하므로 한 번만 정규화하면 된다.
        translations = {}
        for lang in LANGS:
            if lang == "ko":
                continue
            rows = load_items(lang)
            if rows:
                translations[lang] = rows
        STATE["index"] = build_index(load_items(), dong_gdf(), translations)
    return STATE["index"]


def resolve_weather(mode: str, lat: float, lon: float, when: datetime) -> Weather:
    """mode: auto(기상청) | clear | rain | heat"""
    presets = {
        "rain": (19.0, 4.0, "흐림", "비", "우천 시나리오(수동 지정)"),
        "heat": (35.0, 0.0, "맑음", "없음", "폭염 시나리오(수동 지정)"),
        "clear": (22.0, 0.0, "맑음", "없음", "맑음 시나리오(수동 지정)"),
    }
    if mode in presets:
        t, p, sky, pty, note = presets[mode]
        w = Weather(temp_c=t, precip_mm=p, sky=sky, pty=pty)
        w.source, w.note = "manual", note
        return w
    return get_weather(lat, lon, when)


def parse_when(at: str | None) -> datetime:
    if not at:
        return datetime.now()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(at, fmt)
        except ValueError:
            continue
    return datetime.now()


def in_seoul(lat: float, lon: float) -> bool:
    s, w, n, e = SEOUL_BOUNDS
    return s <= lat <= n and w <= lon <= e


# ----------------------------------------------------------------- 상태

@app.get("/api/health")
def health():
    idx = index()
    llm = LLM()
    return {
        "ok": True,
        "items": len(idx),
        "located": idx.located,
        "dong_matched": idx.dong_matched,
        "built_at": idx.built_at,
        "build_ms": idx.build_ms,
        # 절반도 못 채운 어권은 켜지 않는다. 전환해 봐야 대부분 한국어가
        # 그대로 나오면 "지원한다"는 말이 거짓말이 된다.
        "languages": ["ko"] + sorted(
            l for l, n in idx.translated.items()
            if n >= len(idx.places) * LANG_MIN_COVERAGE),
        "language_coverage": {l: round(n / max(len(idx.places), 1), 3)
                              for l, n in sorted(idx.translated.items())},
        "translated": idx.translated,
        "llm_provider": LLM().provider,
        "keys": {
            "visitseoul_api": bool(os.environ.get("VISITSEOUL_API_KEY")),
            "kma": bool(os.environ.get("KMA_API_KEY")),
            "seoul_rtd": bool(os.environ.get("SEOUL_RTD_KEY")),
            "llm": llm.available,
        },
        "routing": router().providers,
    }


@app.post("/api/reload")
def reload_data():
    STATE["index"] = None
    STATE["stats_cache"] = {}
    idx = index()
    return {"ok": True, "items": len(idx), "built_at": idx.built_at}


# ----------------------------------------------------------------- 위치

@app.get("/api/where")
def where(lat: float, lon: float):
    """좌표 → 행정동. 우리가 가진 경계 데이터로 직접 판정한다.

    외부 역지오코딩 API가 필요 없다. 서울 행정동 경계를 이미 들고 있고,
    사용자에게 필요한 건 '지금 어느 동인가'뿐이기 때문이다.
    """
    inside = in_seoul(lat, lon)
    result = {
        "lat": lat, "lon": lon, "in_seoul": inside,
        "gu": None, "dong": None, "adm_cd": None, "label": None, "nearby": 0,
    }

    gdf = dong_gdf()
    if gdf is not None and inside:
        try:
            from shapely.geometry import Point
            hit = gdf[gdf.contains(Point(lon, lat))]
            if not hit.empty:
                row = hit.iloc[0]
                result["gu"], result["dong"] = row["gu"], row["dong"]
                result["adm_cd"] = str(row["adm_cd"])
                result["label"] = f"{row['gu']} {row['dong']}"
        except Exception:
            pass

    if not result["label"]:
        result["label"] = "서울" if inside else "서울 밖"

    # 반경 1.5km 안에 콘텐츠가 몇 개나 있는지 — 여기서 코스가 나올 수 있는지의 신호
    result["nearby"] = sum(
        1 for p in index().places
        if p.lat and p.lon and haversine_m(lat, lon, p.lat, p.lon) <= 1500
    )
    return result


# ----------------------------------------------------------------- 후보

def _row(p, verdict, detail, lat, lon, lang: str = "ko") -> dict:
    c = p.content
    t = p.text(lang)
    d = haversine_m(lat, lon, p.lat, p.lon)
    return {
        "cid": c.cid, "title": t["title"], "category": c.category,
        "category_path": t["category_path"], "summary": (t["summary"] or "")[:120],
        "lat": p.lat, "lon": p.lon, "address": t["address"],
        "gu": p.gu, "dong": p.dong, "adm_cd": getattr(p, "adm_cd", ""),
        "crowd": _crowd_of(p),
        "tags": (t["tags"] or [])[:4], "subway": t["subway"],
        "use_time": t["use_time"], "closed_days": t["closed_days"],
        "text_lang": t["lang"],
        "schedule_start": c.schedule_start, "schedule_end": c.schedule_end,
        "accessibility": c.accessibility, "homepage": c.homepage,
        "phone": c.phone,
        "verdict": verdict.label, "stage": verdict.stage, "reason": verdict.reason,
        "environment": detail["environment"],
        "hours_confidence": detail["hours_confidence"],
        "popularity": round(popularity_scores().get(c.cid, 0.0), 3),
        "trend": trend_badge(c.cid),
        "distance_m": round(d), "walk_min": max(1, round(d * 1.3 / 67)),
    }


@app.get("/api/candidates")
def candidates(lat: float = SEOUL_CITY_HALL[0], lon: float = SEOUL_CITY_HALL[1],
               mode: str = "auto", at: str | None = None,
               limit: int = Query(200, le=1000),
               radius_m: int = Query(0, ge=0, le=20000),
               category: str = "", lang: str = "ko"):
    when = parse_when(at)
    w = resolve_weather(mode, lat, lon, when)

    rows = []
    for p in index().places:
        if not (p.lat and p.lon):
            continue
        if category and p.content.category != category:
            continue
        if radius_m and haversine_m(lat, lon, p.lat, p.lon) > radius_m:
            continue
        v, detail = evaluate_place(p, when, w)
        if v.ok is not True:
            continue
        rows.append(localize(_row(p, v, detail, lat, lon, lang), lang))

    rows.sort(key=lambda r: r["distance_m"])
    return {"weather": localize(weather_now(lat, lon, mode, at), lang),
            "count": len(rows), "items": rows[:limit]}


@app.get("/api/weather")
def weather_now(lat: float = SEOUL_CITY_HALL[0], lon: float = SEOUL_CITY_HALL[1],
                mode: str = "auto", at: str | None = None):
    when = parse_when(at)
    w = resolve_weather(mode, lat, lon, when)
    return {
        "temp_c": w.temp_c, "precip_mm": w.precip_mm, "sky": w.sky, "pty": w.pty,
        "desc": w.describe(), "outdoor_ok": w.outdoor_ok,
        "source": w.source, "note": w.note, "when": when.isoformat(),
    }


# ----------------------------------------------------------------- 코스

def make_course(lat: float, lon: float, mode: str = "auto",
                at: str | None = None, hours: float = 4.0,
                radius_m: int = 4000, interests: str = "",
                explain: bool = False, taste: Taste | None = None,
                lang: str = "ko", exclude: list[str] | None = None,
                avoid: tuple[str, ...] = (), meals: tuple[str, ...] = (),
                profile=None) -> dict:
    """코스 생성 본체.

    엔드포인트를 파이썬 함수로 직접 부르면 FastAPI의 Query 기본값이
    그대로 넘어와 숫자 대신 Query 객체가 들어온다. 그래서 로직을 분리한다.
    """
    from .quality import radius_for
    when = parse_when(at)

    # 서울 밖에서 열면 근처에 아무것도 없다. 도심 기준으로 돌리되 그 사실을 알린다.
    moved = False
    if not in_seoul(lat, lon):
        lat, lon = SEOUL_CITY_HALL
        moved = True

    w = resolve_weather(mode, lat, lon, when)
    c = build_course(
        index().places, when, w, origin=(lat, lon),
        budget_min=int(hours * 60),
        area_radius_m=float(radius_m) if radius_m != 4000 else radius_for(hours),
        interests=[x for x in interests.split(",") if x],
        taste=taste, exclude=set(exclude or []), avoid=avoid,
        profile=profile, meals=tuple(meals or ()),
    )
    out = localize(c.to_dict(lang), lang)
    out["engine"] = "rules"
    out["lang"] = lang
    out["origin"] = {"lat": lat, "lon": lon, "moved_to_seoul": moved}
    if moved:
        out["notes"] = ["서울 밖에서 접속하셨습니다. 서울 도심(시청) 기준으로 "
                        "안내합니다."] + out["notes"]
    if taste is not None and not taste.is_empty:
        out["taste"] = taste.describe()
    if profile is not None and not profile.is_empty:
        out["style"] = profile.describe()
        out["service_axes"] = service_axes(out, taste)

    if explain and c.steps:
        llm = LLM()
        res = llm.explain_course(out["steps"], w.describe(),
                                 when.strftime("%Y-%m-%d %H:%M"))
        for step, line in zip(out["steps"], res.data.get("lines") or []):
            if line:
                step["line"] = line
        out["engine"] = res.engine
    return out


@app.get("/api/course")
def course(lat: float = SEOUL_CITY_HALL[0], lon: float = SEOUL_CITY_HALL[1],
           mode: str = "auto", at: str | None = None,
           hours: float = Query(4.0, ge=0.5, le=12),
           radius_m: int = Query(4000, ge=500, le=20000),
           interests: str = "", explain: bool = False, lang: str = "ko"):
    return make_course(lat, lon, mode, at, hours, radius_m, interests, explain,
                       lang=lang)


class PlanIn(BaseModel):
    lat: float = SEOUL_CITY_HALL[0]
    lon: float = SEOUL_CITY_HALL[1]
    mode: str = "auto"
    at: str | None = None
    hours: float = 4.0
    interests: list[str] = []
    taste: dict | None = None          # 화면이 들고 다니는 취향 프로필
    styles: list[str] = []             # 여행 스타일 (VITALITY)
    lang: str = "ko"
    exclude: list[str] = []            # 이번 일정에서만 빼 달라고 한 곳
    meals: list[str] = []              # 끼니를 챙길 시간대 (breakfast/lunch/dinner)


@app.post("/api/plan")
def plan(body: PlanIn):
    """취향을 반영한 일정.

    프로필은 서버에 저장하지 않는다. 화면이 들고 있다가 매 요청에 실어 보내고
    서버는 그 요청에만 쓴다. 개인화를 하면서 아무것도 쌓아 두지 않는 방법이다.
    """
    taste = Taste.from_dict(body.taste)
    if body.interests:
        taste.declare(body.interests, weight=1.5)
    out = make_course(body.lat, body.lon, body.mode, body.at, body.hours,
                      profile=TrendProfile.from_styles(body.styles),
                      radius_m=4000, interests=",".join(body.interests),
                      taste=taste, lang=body.lang, exclude=body.exclude,
                      meals=tuple(body.meals or ()))
    out["taste_applied"] = not taste.is_empty
    return out


@app.get("/api/styles")
def styles():
    """고를 수 있는 여행 스타일. VITALITY 축을 사람 말로 묶은 것이다."""
    return {"styles": [{"key": k, "label": v["label"], "emoji": v["emoji"]}
                       for k, v in STYLES.items()]}


@app.get("/api/thermal")
def thermal_map():
    """행정동별 위성 지표면온도. 지도의 열지도 모드가 쓴다."""
    from .remote import table
    t = table()
    return {"meta": t.get("meta", {}), "dong": t.get("dong", {})}


def _crowd_of(p) -> dict | None:
    """이 장소의 지금 혼잡. 관측 지역이 800m 안에 없으면 None이다.

    남의 동네 혼잡을 이 자리의 혼잡이라고 말하면 안 된다. 121곳이
    서울 전역을 덮지 않는다는 사실을 감추지 않는다.
    """
    from .crowd import at, is_crowded, relief

    if not (p.lat and p.lon):
        return None
    got = at(p.lat, p.lon)
    if not got:
        return None
    ease = relief(got)
    return {"level": got["level"], "message": got["message"],
            "min": got["min"], "max": got["max"],
            "visitor_rate": got["visitor_rate"], "at": got["at"],
            "crowded": is_crowded(got),
            "relief_at": (ease or {}).get("at", ""),
            "relief_level": (ease or {}).get("level", "")}


@app.get("/api/crowd")
def crowd_now():
    """관측 121곳의 지금 혼잡. 지도의 혼잡 표시가 쓴다."""
    from .crowd import CROWDED, areas, live

    out = []
    for a in areas():
        got = live(a["name"])
        out.append({**a,
                    "level": (got or {}).get("level", ""),
                    "crowded": bool(got and got["level"] in CROWDED),
                    "visitor_rate": (got or {}).get("visitor_rate", 0.0),
                    "at": (got or {}).get("at", "")})
    live_n = sum(1 for x in out if x["level"])
    return {"meta": {"areas": len(out), "live": live_n,
                     "key": bool(os.environ.get("SEOUL_RTD_KEY")),
                     "source": "서울시 실시간 도시데이터 · 5~10분 갱신"},
            "areas": out}


@app.get("/api/search")
def search_place(q: str = "", limit: int = Query(8, le=20)):
    """지명·주소로 좌표를 찾는다. 내 위치가 아닌 곳도 볼 수 있게.

    바깥 지오코더를 붙이지 않는다. 우리가 이미 서울의 장소 3,788건과
    행정동 424개를 이름으로 들고 있어서, 관광 목적의 검색은 그 안에서
    거의 다 풀린다 — '성수동', '경복궁', '홍대'가 전부 여기 있다. 키도
    쿼터도 없고 오프라인에서 돈다.

    못 찾으면 빈 목록을 준다. 엉뚱한 좌표로 보내는 것보다 낫다.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return {"query": q, "items": []}

    from .chat import LANDMARKS, LANDMARK_EN

    idx = index()
    out, seen = [], set()

    def push(kind, name, sub, lat, lon, score, en=""):
        key = (round(lat, 4), round(lon, 4))
        if key in seen:
            return
        seen.add(key)
        out.append({"kind": kind, "name": name, "en": en, "sub": sub,
                    "lat": lat, "lon": lon, "score": score})

    low = q.lower()
    # 널리 쓰는 동네 이름이 먼저다. '홍대'는 장소명이 아니라 지역이다.
    for nm, (la, lo) in LANDMARKS.items():
        en = LANDMARK_EN.get(nm, "")
        hit = (low in nm.lower() or nm.lower() in low
               # 로마자로도 찾게 한다. 'Seongsu'도 'seongsu-dong'도 걸린다.
               or (en and (low in en.lower() or en.lower().startswith(low))))
        if hit:
            push("area", nm, "지역", la, lo, 100 - abs(len(nm) - len(q)), en)

    # 행정동
    for f in _dong_features():
        nm = f"{f['gu']} {f['dong']}"
        if low in f["dong"].lower() or low in nm.lower():
            push("dong", nm, "행정동", f["lat"], f["lon"], 80)

    # 장소 이름
    for p in idx.places:
        if not (p.lat and p.lon):
            continue
        t = p.content.title or ""
        if low in t.lower():
            push("place", t, p.content.category, p.lat, p.lon,
                 70 - min(len(t) - len(q), 40))

    out.sort(key=lambda r: -r["score"])
    return {"query": q, "items": out[:limit]}


@lru_cache(maxsize=1)
def _dong_features() -> list:
    """행정동 이름과 중심점. 경계 파일에서 한 번만 만든다."""
    import json as _json
    p = Path(__file__).resolve().parent.parent / "web" / "data" / "seoul_dong.geojson"
    try:
        gj = _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for f in gj.get("features", []):
        g, xs, ys, n = f.get("geometry") or {}, 0.0, 0.0, 0
        rings = (g.get("coordinates") or [[]])[0] if g.get("type") == "Polygon"             else [r[0] for r in (g.get("coordinates") or [])]
        pts = rings if g.get("type") == "Polygon" else [p for r in rings for p in r]
        for pt in pts:
            xs += pt[0]; ys += pt[1]; n += 1
        if n:
            pr = f["properties"]
            out.append({"gu": pr.get("gu", ""), "dong": pr.get("dong", ""),
                        "lat": ys / n, "lon": xs / n})
    return out


@app.get("/api/area")
def area_trend():
    """행정동별 방문 모멘텀. 지도의 '동네' 모드와 '조용한 곳' 필터가 쓴다.

    유동인구는 행안부 행정동코드(11110515)를 쓰고 지도 경계는 서울시
    체계(11010530)를 쓴다. 두 체계를 잇는 표를 만들 것도 없이 구·동
    이름이 97.7% 맞으므로 이름으로 붙이고, 화면이 쓰는 쪽 코드로 내보낸다.
    """
    from .momentum import LABEL, excess, table

    t = table("footfall")
    rows = t.get("series") or {}
    if not rows:
        return {"meta": {}, "dong": {}, "quiet": []}

    web = _dong_name_to_code()
    lv = sorted(r["axes"]["level"] for r in rows.values() if r.get("axes"))
    median = lv[len(lv) // 2] if lv else 0.0

    out, quiet = {}, []
    for r in rows.values():
        a = r.get("axes")
        code = web.get(r.get("label", ""))
        if not a or not code:
            continue
        m = excess(a)
        # '아직 조용한 곳' — 뜨는데 아직 안 붐빈다. 이 사분면이 제품이다.
        is_quiet = m >= QUIET_RISE and a["level"] <= median
        out[code] = {"name": r["label"], "momentum": round(m, 4),
                     "level": a["level"], "trend": r["trend"],
                     "label": LABEL.get(r["trend"], ""), "quiet": is_quiet}
        if is_quiet:
            quiet.append(code)
    return {"meta": {"dong": len(out), "median_level": median,
                     "quiet_rise": QUIET_RISE, "quiet": len(quiet),
                     "months": (t.get("meta") or {}).get("rows"),
                     "source": "서울 열린데이터광장 단기체류 외국인 생활인구"},
            "dong": out, "quiet": quiet}


QUIET_RISE = 0.15          # 이만큼 늘면 '뜨는 중'으로 본다


@lru_cache(maxsize=1)
def _dong_name_to_code() -> dict:
    """'구 동' → 지도 경계의 adm_cd."""
    import json as _json
    p = Path(__file__).resolve().parent.parent / "web" / "data" / "seoul_dong.geojson"
    try:
        gj = _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {f"{f['properties']['gu']} {f['properties']['dong']}":
            f["properties"]["adm_cd"] for f in gj.get("features", [])}


class ReplanIn(BaseModel):
    lat: float = SEOUL_CITY_HALL[0]
    lon: float = SEOUL_CITY_HALL[1]
    at: str | None = None
    hours: float = 4.0
    mode: str = "rain"                 # 바뀐 날씨
    done_until: str | None = None      # 이 시각까지는 이미 다녀왔다
    interests: list[str] = []
    styles: list[str] = []
    taste: dict | None = None
    lang: str = "ko"


@app.post("/api/replan")
def replan_course(body: ReplanIn):
    """날씨가 바뀌었을 때 원래 하려던 경험을 지키며 남은 일정을 고친다.

    "비 오면 실내로" 가 아니라 "비 오는데, 원래 하려던 게 로컬 감성이었으니
    실내인데 로컬 감성인 곳으로". 얼마나 지켰는지는 숫자로 함께 돌려준다.
    """
    from .course import build_course, experience_kept, replan

    when = parse_when(body.at)
    taste = Taste.from_dict(body.taste)
    if body.interests:
        taste.declare(body.interests, weight=1.5)
    profile = TrendProfile.from_styles(body.styles)
    idx = index()

    before = build_course(
        idx.places, when, resolve_weather("clear", body.lat, body.lon, when),
        origin=(body.lat, body.lon), budget_min=int(body.hours * 60),
        interests=body.interests, taste=taste, profile=profile)

    after = replan(
        before, idx.places, when,
        resolve_weather(body.mode, body.lat, body.lon, when),
        origin=(body.lat, body.lon), budget_min=int(body.hours * 60),
        taste=taste, profile=profile,
        keep_before=parse_when(body.done_until) if body.done_until else None)

    return {
        "before": before.to_dict(body.lang),
        "after": after.to_dict(body.lang),
        "experience_kept": experience_kept(before, after),
        "style": profile.describe(),
    }


@app.get("/api/routing")
def routing_info(from_lat: float, from_lon: float, to_lat: float, to_lon: float):
    return router().best((from_lat, from_lon), (to_lat, to_lon))


# ----------------------------------------------------------------- 근거 수치

@app.get("/api/stats")
def stats(mode: str = "auto", at: str | None = None):
    when = parse_when(at)
    key = (mode, when.strftime("%Y-%m-%d-%H"))
    if key in STATE["stats_cache"]:
        return STATE["stats_cache"][key]

    w = resolve_weather(mode, *SEOUL_CITY_HALL, when)
    idx = index()

    conf, env, stages = Counter(), Counter(), Counter()
    passed = 0
    dated = ended = 0
    for p in idx.places:
        conf[p.hours.confidence] += 1
        env[p.environment] += 1
        v, _ = evaluate_place(p, when, w)
        if v.ok is True:
            passed += 1
        else:
            stages[f"{v.label}·{v.stage}"] += 1
        if p.content.is_dated_event:
            dated += 1
            if check_period(p.content, when.date()).reason.endswith("종료"):
                ended += 1

    out = {
        "total": len(idx),
        "by_category": dict(Counter(p.content.category for p in idx.places)),
        "hours_confidence": dict(conf),
        "environment": dict(env),
        "dated": {"total": dated, "ended": ended},
        "funnel": {"passed": passed, "dropped": dict(stages)},
        "weather": weather_now(*SEOUL_CITY_HALL, mode, at),
        "distribution": dict(
            Counter(p.gu for p in idx.places if p.gu).most_common()),
    }
    STATE["stats_cache"][key] = out
    return out


# ----------------------------------------------------------------- 챗봇

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    messages: list[ChatMessage]
    lat: float | None = None
    lon: float | None = None
    at: str | None = None
    intent: dict | None = None
    taste: dict | None = None
    lang: str = "ko"


def _shift_start(at: str | None, hour: int | None) -> str | None:
    """대화에서 말한 시작 시각을 반영한다. "오후 3시부터"를 흘리면
    3시 일정을 달라고 했는데 지금 시각으로 짜 주게 된다."""
    if hour is None:
        return at
    base = parse_when(at)
    return base.replace(hour=hour, minute=0).strftime("%Y-%m-%dT%H:%M")


# ----------------------------------------------------------------- 에이전트

def _agent_replan(prior_cids, lat, lon, at, hours, mode, taste,
                  styles, done_until=None, lang="ko") -> dict | None:
    """에이전트용 재편성 도구.

    화면이 들고 있던 일정을 cid로 되살려 '원래 하려던 것'으로 삼는다.
    그래야 "비 온대요"에 처음부터 다시 짜지 않고 그 일정을 고칠 수 있다.
    """
    from .course import (Course, Step, experience_kept, replan)

    idx = index()
    olds = [idx.by_cid[c] for c in prior_cids if c in idx.by_cid]
    if not olds:
        return None
    when = parse_when(at)
    before = Course(start=when, budget_min=int(hours * 60))
    before.steps = [Step(place=p, role="spot", reason="") for p in olds]

    after = replan(
        before, idx.places, when,
        resolve_weather(mode, lat, lon, when),
        origin=(lat, lon), budget_min=int(hours * 60),
        taste=taste, profile=TrendProfile.from_styles(styles),
        keep_before=parse_when(done_until) if done_until else None)
    return {"after": after.to_dict(lang),
            "experience_kept": experience_kept(before, after)}


def _agent_deps() -> dict:
    """에이전트가 쓸 도구 묶음. 서버가 가진 것을 넘겨준다."""
    from .remote import heat_of
    return {
        "llm": LLM(),
        "where": lambda lat, lon: where(lat, lon),
        "gu_center": _gu_center,
        "shift_start": _shift_start,
        "make_course": make_course,
        "thermal": heat_of,
        "replan": _agent_replan,
    }


class AgentIn(BaseModel):
    message: str = ""
    messages: list[ChatMessage] = []   # 예전 /api/chat 형식도 받는다
    lat: float | None = None
    lon: float | None = None
    at: str | None = None
    intent: dict | None = None
    taste: dict | None = None
    styles: list[str] = []
    course: dict | None = None         # 화면이 들고 있는 현재 일정
    done_until: str | None = None      # 이 시각까지는 이미 다녀왔다
    lang: str = "ko"


@app.post("/api/agent")
def agent_turn(body: AgentIn):
    """에이전트 한 턴. 도구를 돌리고, 무엇을 했는지 함께 돌려준다."""
    msg = body.message.strip()
    if not msg and body.messages:
        users = [m for m in body.messages if m.role == "user"]
        msg = users[-1].content.strip() if users else ""
    if not msg:
        blank = ("Tell me where you are and how long you have, and I will "
                 "pick only places you can actually visit now."
                 if body.lang == "en" else
                 "어디서, 얼마나 시간이 있으신지 알려주시면 "
                 "지금 갈 수 있는 곳만 골라 드릴게요.")
        return {"answer": blank, "course": None, "intent": None,
                "tool_trace": [], "evidence": [], "actions": [],
                "engine": "rules"}

    payload = {"message": msg, "lat": body.lat, "lon": body.lon,
               "at": body.at, "intent": body.intent, "taste": body.taste,
               "styles": body.styles, "course": body.course,
               "done_until": body.done_until, "lang": body.lang}
    deps = _agent_deps()
    got = run_agent(payload, deps)
    answer, engine = compose(payload, got, deps["llm"])

    course = got["course"]
    course["engine"] = engine
    lang = body.lang
    return {
        "answer": answer, "reply": answer,        # 예전 이름도 함께
        "course": localize(course, lang),
        "intent": got["intent"].to_dict(),
        "taste": got["taste"].to_dict(),
        "taste_summary": to_en(got["taste"].describe())
        if lang == "en" else got["taste"].describe(),
        "where": localize(got["where"], lang),
        "heat": localize(got["heat"], lang),
        # 도구 실행 기록과 근거도 화면에 그대로 나간다
        "tool_trace": deep_en(got["tool_trace"], lang),
        "evidence": deep_en(got["evidence"], lang),
        "actions": deep_en(got["actions"], lang),
        "origin": got["origin"],
        "engine": engine, "llm_available": deps["llm"].available,
    }


@app.post("/api/chat")
def chat(body: ChatIn):
    """대화 한 턴. 의도를 뽑고, 판정 엔진이 일정을 만들고, 말로 옮긴다."""
    user_msgs = [m for m in body.messages if m.role == "user"]
    if not user_msgs:
        return {"reply": "어디서, 얼마나 시간이 있으신지 알려주세요.",
                "course": None, "intent": None, "engine": "rules"}
    message = user_msgs[-1].content.strip()

    prev = None
    if body.intent:
        prev = Intent()
        for k, v in body.intent.items():
            if hasattr(prev, k):
                setattr(prev, k, v)
        if prev.area in LANDMARKS:
            prev.lat, prev.lon = LANDMARKS[prev.area]

    llm = LLM()
    intent = parse_intent(message, prev, llm)

    # 사용자가 지역을 말하지 않았으면 화면이 알려 준 현재 위치를 쓴다
    if not intent.area and body.lat and body.lon:
        intent.lat, intent.lon = body.lat, body.lon
    elif intent.area and intent.area not in LANDMARKS:
        if (center := _gu_center(intent.area.replace("구", ""))):
            intent.lat, intent.lon = center

    taste = Taste.from_dict(body.taste)
    if intent.interests:
        taste.declare(intent.interests, weight=1.5)
    for tag in mood_interests(message):          # "조용한 데", "이색적인 곳"
        taste.tags[tag] = taste.tags.get(tag, 0.0) + 0.8
    for tag in PARTY_TAGS.get(intent.party or "", ()):
        taste.tags[tag] = taste.tags.get(tag, 0.0) + 0.6

    at = _shift_start(body.at, intent.start_hour)
    result = make_course(lat=intent.lat, lon=intent.lon, mode=intent.weather_mode,
                         at=at, hours=float(intent.hours or 4.0),
                         radius_m=(int(intent.max_walk_min * 67)
                                   if intent.walk_limited else 4000),
                         interests=",".join(intent.interests), taste=taste,
                         avoid=PARTY_AVOID.get(intent.party or "", ()),
                         lang=intent.language if intent.language in ("ko", "en",
                              "ja", "zh-CN", "zh-TW", "ru", "ms") else body.lang)

    reply, engine = compose_reply(message, intent, result, llm)
    result["engine"] = engine
    return {"reply": reply, "course": result, "intent": intent.to_dict(),
            "taste": taste.to_dict(), "taste_summary": taste.describe(),
            "engine": engine, "llm_available": llm.available}


def _gu_center(gu_short: str) -> tuple[float, float] | None:
    gdf = dong_gdf()
    if gdf is None:
        return None
    try:
        name = gu_short if gu_short.endswith("구") else gu_short + "구"
        sub = gdf[gdf["gu"] == name]
        if sub.empty:
            return None
        c = sub.geometry.union_all().centroid
        return c.y, c.x
    except Exception:
        return None


# ----------------------------------------------------------------- 정적 파일

@app.middleware("http")
async def no_cache_html(request, call_next):
    """화면을 이루는 파일은 캐시하지 않는다.

    index.html이 캐시되면 스크립트 경로를 바꿔도 브라우저가 옛 파일을 계속
    불러온다. 파일명에 ?v= 를 붙여도 그 참조를 담은 HTML이 낡으면 소용없다.
    CSS·JS도 같다 — style.css를 고쳐도 화면이 그대로여서 "안 고쳐졌다"고
    한참 엉뚱한 데를 뒤지게 된다. 무거운 것(경계 GeoJSON·지도 라이브러리)은
    그대로 캐시한다.
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/sw.js":
        # 서비스 워커 스크립트에 no-store가 붙으면 크롬이 등록을 거부한다.
        # revalidate만 시키면 배포한 새 워커는 그대로 잡힌다.
        response.headers["Cache-Control"] = "no-cache"
        return response
    if (path.endswith((".html", ".css", ".js", "/")) or path == "")             and not path.startswith("/vendor/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


if WEB.exists():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")


def _dual_stack_socket(port: int):
    """IPv4와 IPv6를 함께 받는 소켓.

    IPv4에만 바인드하면 브라우저가 `localhost`를 ::1로 먼저 풀 때 폴백에
    2초가 걸린다(실측 2,038ms). 반대로 IPv6에만 바인드하면 127.0.0.1이
    끊긴다. IPV6_V6ONLY를 끈 소켓 하나로 둘 다 받는다.
    """
    import socket
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("::", port))
        sock.listen(128)
        return sock
    except OSError:
        return None                    # IPv6가 없는 환경 — 호출부가 폴백한다


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", 8020))
    host = os.environ.get("HOST")
    if host:                           # 명시했으면 그대로 따른다
        uvicorn.run(app, host=host, port=port)
        return

    sock = _dual_stack_socket(port)
    if sock is None:
        uvicorn.run(app, host="0.0.0.0", port=port)
        return
    server = uvicorn.Server(uvicorn.Config(app, log_level="info"))
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
