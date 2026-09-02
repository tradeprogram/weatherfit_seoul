"""웨더핏 서울 백엔드.

    python -m weatherfit.server            # http://127.0.0.1:8020

키가 없어도 전부 동작한다. 기상청 키가 없으면 기본 날씨로, LLM 키가 없으면
규칙 기반으로 떨어진다. 어느 쪽이 쓰였는지는 응답의 engine/source 필드에 항상 실린다.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .course import build_course, haversine_m, passing, walk_minutes
from .llm import LLM
from .models import Content
from .normalize import parse_hours, tag_environment
from .report import load as load_items
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
           max_walk_min: int = 25, food: int = 2, explain: bool = False):
    when = parse_when(at)
    w = resolve_weather(mode, lat, lon, when)
    c = build_course(items(), when, w, origin=(lat, lon),
                     max_walk_min=max_walk_min, want_food=food)
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

class ChatIn(BaseModel):
    message: str
    lat: float = SEOUL_CITY_HALL[0]
    lon: float = SEOUL_CITY_HALL[1]
    mode: str = "auto"
    at: str | None = None


_GU_RE = re.compile(r"(종로|중구|용산|성동|광진|동대문|중랑|성북|강북|도봉|노원|은평|"
                    r"서대문|마포|양천|강서|구로|금천|영등포|동작|관악|서초|강남|송파|강동)")

# 사람들은 자치구가 아니라 동네 이름으로 말한다
LANDMARKS = {
    "홍대": (37.5570, 126.9245), "합정": (37.5495, 126.9137),
    "연남": (37.5601, 126.9256), "성수": (37.5445, 127.0557),
    "건대": (37.5403, 127.0695), "명동": (37.5636, 126.9827),
    "인사동": (37.5735, 126.9860), "북촌": (37.5826, 126.9830),
    "익선동": (37.5732, 126.9905), "을지로": (37.5660, 126.9910),
    "이태원": (37.5346, 126.9946), "한남": (37.5343, 127.0016),
    "여의도": (37.5215, 126.9243), "잠실": (37.5133, 127.1000),
    "가로수길": (37.5205, 127.0230), "압구정": (37.5271, 127.0286),
    "청담": (37.5250, 127.0530), "삼청동": (37.5825, 126.9810),
    "동대문": (37.5654, 127.0090), "남산": (37.5512, 126.9882),
    "서울숲": (37.5443, 127.0374), "DDP": (37.5665, 127.0093),
    "경복궁": (37.5796, 126.9770), "광화문": (37.5720, 126.9769),
    "신촌": (37.5551, 126.9368), "강남역": (37.4979, 127.0276),
}
_LANDMARK_RE = re.compile("(" + "|".join(sorted(LANDMARKS, key=len, reverse=True)) + ")")

_HOUR_RE = re.compile(r"(\d+)\s*시간")
_RAIN_WORDS = ("비 ", "비가", "비오", "비 오", "우천", "소나기", "장마", "빗")
_HEAT_WORDS = ("더위", "더운", "덥", "폭염", "무더")
_COLD_WORDS = ("추위", "추운", "춥", "한파")


@app.post("/api/chat")
def chat(body: ChatIn):
    """자연어 한 줄 → 코스. 의도 파악은 규칙으로, 설명 문장만 LLM에 맡긴다."""
    msg = body.message.strip()
    mode = body.mode
    if any(k in msg for k in _RAIN_WORDS):
        mode = "rain"
    elif any(k in msg for k in _HEAT_WORDS) or any(k in msg for k in _COLD_WORDS):
        mode = "heat" if any(k in msg for k in _HEAT_WORDS) else "auto"

    max_walk = 25
    if (m := _HOUR_RE.search(msg)):
        # 남은 시간이 짧으면 도보 반경을 줄인다
        max_walk = max(10, min(40, int(m.group(1)) * 10))

    # 동네 이름을 먼저 본다. 자치구보다 좁고 사람들이 실제로 쓰는 말이다
    lat, lon, area = body.lat, body.lon, None
    if (m := _LANDMARK_RE.search(msg)):
        area = m.group(1)
        lat, lon = LANDMARKS[area]
    elif (m := _GU_RE.search(msg)):
        area = m.group(1) if m.group(1) == "중구" else m.group(1) + "구"
        if (center := _gu_center(m.group(1))):
            lat, lon = center

    result = course(lat=lat, lon=lon, mode=mode, at=body.at,
                    max_walk_min=max_walk, explain=True)
    result["understood"] = {
        "area": area, "weather_mode": mode, "max_walk_min": max_walk,
    }
    return result


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
