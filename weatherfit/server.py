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
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .chat import LANDMARKS, Intent, compose_reply, parse_intent
from .popularity import scores as popularity_scores
from .taste import Taste, mood_interests
from .course import build_course
from .index import Index, build_index
from .llm import LLM
from .models import LANG_LABEL, LANGS
from .report import load as load_items
from .routing import haversine_m, router
from .validate import Weather, check_period, evaluate_place
from .weather import SEOUL_CITY_HALL, get_weather

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
        "languages": ["ko"] + sorted(idx.translated),
        "translated": idx.translated,
        "keys": {
            "visitseoul_api": bool(os.environ.get("VISITSEOUL_API_KEY")),
            "kma": bool(os.environ.get("KMA_API_KEY")),
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
        "gu": None, "dong": None, "label": None, "nearby": 0,
    }

    gdf = dong_gdf()
    if gdf is not None and inside:
        try:
            from shapely.geometry import Point
            hit = gdf[gdf.contains(Point(lon, lat))]
            if not hit.empty:
                row = hit.iloc[0]
                result["gu"], result["dong"] = row["gu"], row["dong"]
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
        "gu": p.gu, "dong": p.dong,
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
        rows.append(_row(p, v, detail, lat, lon, lang))

    rows.sort(key=lambda r: r["distance_m"])
    return {"weather": weather_now(lat, lon, mode, at),
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
                lang: str = "ko", exclude: list[str] | None = None) -> dict:
    """코스 생성 본체.

    엔드포인트를 파이썬 함수로 직접 부르면 FastAPI의 Query 기본값이
    그대로 넘어와 숫자 대신 Query 객체가 들어온다. 그래서 로직을 분리한다.
    """
    from .quality import radius_for
    when = parse_when(at)
    w = resolve_weather(mode, lat, lon, when)
    c = build_course(
        index().places, when, w, origin=(lat, lon),
        budget_min=int(hours * 60),
        area_radius_m=float(radius_m) if radius_m != 4000 else radius_for(hours),
        interests=[x for x in interests.split(",") if x],
        taste=taste, exclude=set(exclude or []),
    )
    out = c.to_dict(lang)
    out["engine"] = "rules"
    out["lang"] = lang
    if taste is not None and not taste.is_empty:
        out["taste"] = taste.describe()

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
    lang: str = "ko"
    exclude: list[str] = []            # 이번 일정에서만 빼 달라고 한 곳


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
                      radius_m=4000, interests=",".join(body.interests),
                      taste=taste, lang=body.lang, exclude=body.exclude)
    out["taste_applied"] = not taste.is_empty
    return out


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

    result = make_course(lat=intent.lat, lon=intent.lon, mode=intent.weather_mode,
                         at=body.at, hours=float(intent.hours or 4.0),
                         interests=",".join(intent.interests), taste=taste,
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
    """HTML은 캐시하지 않는다.

    index.html이 캐시되면 스크립트 경로를 바꿔도 브라우저가 옛 파일을 계속
    불러온다. 파일명에 ?v= 를 붙여도 그 참조를 담은 HTML이 낡으면 소용없다.
    """
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".html", "/")) or path == "":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


if WEB.exists():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8020)))


if __name__ == "__main__":
    main()
