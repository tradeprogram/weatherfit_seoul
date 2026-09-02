"""웨더핏 서울 백엔드.

    python -m weatherfit.server            # http://127.0.0.1:8020

키가 없어도 전부 동작한다. 기상청 키가 없으면 기본 날씨로, LLM 키가 없으면
규칙 기반으로 떨어진다. 어느 쪽이 쓰였는지는 응답의 engine/source 필드에 항상 실린다.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .chat import Intent, LANDMARKS, compose_reply, parse_intent
from .course import build_course, haversine_m, passing, walk_minutes
from .llm import LLM
from .models import Content
from .normalize import parse_hours, tag_environment
from .report import load as load_items
from .routing import router
from .validate import Weather, check_period, evaluate
from .weather import SEOUL_CITY_HALL, get_weather

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

app = FastAPI(title="웨더핏 서울", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

STATE: dict[str, Any] = {"items": [], "dong": None, "loaded_at": None}


def items() -> list[Content]:
    if not STATE["items"]:
        STATE["items"] = load_items()
        STATE["loaded_at"] = datetime.now().isoformat()
    return STATE["items"]


def resolve_weather(mode: str, lat: float, lon: float,
                    when: datetime) -> Weather:
    """mode: auto(기상청) | clear | rain | heat"""
    if mode == "rain":
        w = Weather(temp_c=19.0, precip_mm=4.0, sky="흐림", pty="비")
        w.source, w.note = "manual", "우천 시나리오(수동 지정)"
        return w
    if mode == "heat":
        w = Weather(temp_c=35.0, precip_mm=0.0, sky="맑음", pty="없음")
        w.source, w.note = "manual", "폭염 시나리오(수동 지정)"
        return w
    if mode == "clear":
        w = Weather(temp_c=22.0, precip_mm=0.0, sky="맑음", pty="없음")
        w.source, w.note = "manual", "맑음 시나리오(수동 지정)"
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


# ----------------------------------------------------------------- 엔드포인트

@app.get("/api/health")
def health():
    llm = LLM()
    return {
        "ok": True,
        "items": len(items()),
        "loaded_at": STATE["loaded_at"],
        "keys": {
            "visitseoul_api": bool(os.environ.get("VISITSEOUL_API_KEY")),
            "kma": bool(os.environ.get("KMA_API_KEY")),
            "llm": llm.available,
        },
        "routing": router().providers,
    }


@app.post("/api/reload")
def reload_data():
    """수집을 더 돌린 뒤 서버를 재시작하지 않고 다시 읽는다."""
    STATE["items"] = []
    return {"ok": True, "items": len(items()), "loaded_at": STATE["loaded_at"]}


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


@app.get("/api/candidates")
def candidates(lat: float = SEOUL_CITY_HALL[0], lon: float = SEOUL_CITY_HALL[1],
               mode: str = "auto", at: str | None = None,
               limit: int = Query(300, le=2000),
               only_pass: bool = True):
    """판정 결과가 붙은 후보 목록. 지도 마커의 원천."""
    when = parse_when(at)
    w = resolve_weather(mode, lat, lon, when)

    rows = []
    for it in items():
        if not (it.lat and it.lon):
            continue
        verdict, detail = evaluate(it, when, w)
        if only_pass and verdict.ok is not True:
            continue
        d = haversine_m(lat, lon, it.lat, it.lon)
        rows.append({
            "cid": it.cid, "title": it.title, "category": it.category,
            "category_path": it.category_path, "summary": it.summary,
            "lat": it.lat, "lon": it.lon, "address": it.address,
            "tags": it.tags[:5], "subway": it.subway_raw,
            "use_time": it.use_time_raw, "closed_days": it.closed_days_raw,
            "schedule_start": it.schedule_start, "schedule_end": it.schedule_end,
            "accessibility": it.accessibility, "homepage": it.homepage,
            "verdict": verdict.label, "stage": verdict.stage,
            "reason": verdict.reason,
            "environment": detail["environment"],
            "hours_confidence": detail["hours_confidence"],
            "distance_m": round(d), "walk_min": walk_minutes(d),
        })
    rows.sort(key=lambda r: r["distance_m"])
    return {"weather": weather_now(lat, lon, mode, at), "count": len(rows),
            "items": rows[:limit]}


@app.get("/api/course")
def course(lat: float = SEOUL_CITY_HALL[0], lon: float = SEOUL_CITY_HALL[1],
           mode: str = "auto", at: str | None = None,
           max_walk_min: int = 25, food: int = 2, explain: bool = False,
           interests: str = ""):
    when = parse_when(at)
    w = resolve_weather(mode, lat, lon, when)
    c = build_course(items(), when, w, origin=(lat, lon),
                     max_walk_min=max_walk_min, want_food=food,
                     interests=[x for x in interests.split(",") if x])
    out = c.to_dict()
    out["engine"] = "rules"

    if explain and c.steps:
        llm = LLM()
        res = llm.explain_course(out["steps"], w.describe(),
                                 when.strftime("%Y-%m-%d %H:%M"))
        lines = res.data.get("lines") or []
        for step, line in zip(out["steps"], lines):
            if line:
                step["line"] = line
        out["engine"] = res.engine
        if res.error:
            out["engine_error"] = res.error
    return out


@app.get("/api/stats")
def stats(mode: str = "auto", at: str | None = None):
    """제안서 근거 수치 + 관광 분산 지표."""
    when = parse_when(at)
    w = resolve_weather(mode, *SEOUL_CITY_HALL, when)
    data = items()
    total = len(data)

    conf, env, stages = Counter(), Counter(), Counter()
    passed = 0
    for it in data:
        oh = parse_hours(it.use_time_raw, it.closed_days_raw)
        conf[oh.confidence] += 1
        label, _ = tag_environment(it.category, it.title, it.description, it.tags)
        env[label] += 1
        v, _ = evaluate(it, when, w)
        if v.ok is True:
            passed += 1
        else:
            stages[f"{v.label}·{v.stage}"] += 1

    dated = [i for i in data if i.is_dated_event]
    ended = sum(1 for i in dated
                if check_period(i, when.date()).reason.endswith("종료"))

    return {
        "total": total,
        "by_category": dict(Counter(i.category for i in data)),
        "hours_confidence": dict(conf),
        "environment": dict(env),
        "dated": {"total": len(dated), "ended": ended},
        "funnel": {"passed": passed, "dropped": dict(stages)},
        "weather": weather_now(*SEOUL_CITY_HALL, mode, at),
        "distribution": distribution(),
    }


def distribution() -> dict[str, int]:
    """콘텐츠가 어느 자치구에 있는지 — 관광 분산 지표의 기준선."""
    try:
        from .geo import assign_dong
        if STATE["dong"] is None:
            from .geo import load_dong_index
            STATE["dong"] = load_dong_index()
        mapping = assign_dong(items(), STATE["dong"])
    except Exception:
        return {}
    return dict(Counter(gu for gu, _ in mapping.values()).most_common())


# ----------------------------------------------------------------- 챗봇

class ChatMessage(BaseModel):
    role: str            # user | assistant
    content: str


class ChatIn(BaseModel):
    messages: list[ChatMessage]
    lat: float | None = None
    lon: float | None = None
    at: str | None = None
    intent: dict | None = None       # 앞 턴에서 돌려준 의도를 그대로 넘겨받는다


@app.post("/api/chat")
def chat(body: ChatIn):
    """대화 한 턴. 의도를 뽑고, 판정 엔진이 코스를 만들고, 말로 옮긴다.

    추천 대상은 언제나 판정을 통과한 실제 콘텐츠다. LLM은 알아듣고 설명할 뿐
    장소를 지어내지 않는다.
    """
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

    # 화면에서 현재 위치를 줬고 사용자가 지역을 말하지 않았으면 그 위치를 쓴다
    if not intent.area and body.lat and body.lon:
        intent.lat, intent.lon = body.lat, body.lon
    elif intent.area and intent.area not in LANDMARKS:
        if (center := _gu_center(intent.area.replace("구", ""))):
            intent.lat, intent.lon = center

    result = course(lat=intent.lat, lon=intent.lon, mode=intent.weather_mode,
                    at=body.at, max_walk_min=intent.max_walk_min, explain=False,
                    interests=",".join(intent.interests))

    reply, engine = compose_reply(message, intent, result, llm)
    result["engine"] = engine
    return {
        "reply": reply,
        "course": result,
        "intent": intent.to_dict(),
        "engine": engine,
        "llm_available": llm.available,
    }


def _gu_center(gu_short: str) -> tuple[float, float] | None:
    """자치구 이름 → 경계 중심 좌표."""
    try:
        if STATE["dong"] is None:
            from .geo import load_dong_index
            STATE["dong"] = load_dong_index()
        idx = STATE["dong"]
        name = gu_short if gu_short.endswith("구") else gu_short + "구"
        sub = idx[idx["gu"] == name]
        if sub.empty:
            return None
        c = sub.geometry.union_all().centroid
        return c.y, c.x
    except Exception:
        return None


@app.get("/api/routing")
def routing_info(from_lat: float, from_lon: float, to_lat: float, to_lon: float):
    """두 지점 사이의 도보·대중교통 소요시간."""
    return router().best((from_lat, from_lon), (to_lat, to_lon))


# ----------------------------------------------------------------- 정적 파일

@app.middleware("http")
async def no_cache_html(request, call_next):
    """HTML은 캐시하지 않는다.

    index.html이 캐시되면 스크립트 경로를 바꿔도 브라우저가 옛 파일을 계속
    불러온다. 파일명에 ?v= 를 붙여도 그 참조를 담은 HTML 자체가 낡으면 소용없다.
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
