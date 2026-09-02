"""[C] 코스 구성.

유효성 판정을 통과한 후보에서 반나절 코스를 만든다.

    오늘의 행사 1곳  +  도보권 로컬 음식 1~2곳  +  날씨 급변 대비 실내 대안 1곳

'가까운 순'이 아니라 '오늘 놓치면 사라지는 것 우선'으로 고른다. 종료가 임박한
행사가 코스의 앵커가 되고, 나머지는 그 주변 도보권에서 붙인다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime

from .models import Content
from .routing import router
from .validate import Weather, evaluate

WALK_M_PER_MIN = 67.0          # 도보 4km/h


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk_minutes(meters: float) -> int:
    return max(1, round(meters / WALK_M_PER_MIN))


@dataclass
class Step:
    item: Content
    role: str                   # anchor | food | shelter
    environment: str
    reason: str                 # 판정 근거
    line: str = ""              # 사용자에게 보여줄 '왜 지금인지'
    distance_m: float | None = None
    walk_min: int | None = None
    ends_today: bool = False
    travel: dict | None = None      # 앞 장소에서 여기까지: 도보/대중교통

    def to_dict(self) -> dict:
        i = self.item
        return {
            "cid": i.cid, "title": i.title, "category": i.category,
            "category_path": i.category_path, "summary": i.summary,
            "address": i.address, "lat": i.lat, "lon": i.lon,
            "subway": i.subway_raw, "use_time": i.use_time_raw,
            "closed_days": i.closed_days_raw, "tags": i.tags,
            "schedule_start": i.schedule_start, "schedule_end": i.schedule_end,
            "accessibility": i.accessibility, "homepage": i.homepage,
            "role": self.role, "environment": self.environment,
            "verdict_reason": self.reason, "line": self.line,
            "distance_m": round(self.distance_m) if self.distance_m else None,
            "walk_min": self.walk_min, "ends_today": self.ends_today,
            "travel": self.travel,
        }


@dataclass
class Course:
    steps: list[Step] = field(default_factory=list)
    weather: Weather | None = None
    when: datetime | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        total_walk = sum(s.walk_min or 0 for s in self.steps)
        return {
            "steps": [s.to_dict() for s in self.steps],
            "total_walk_min": total_walk,
            "weather": {
                "desc": self.weather.describe() if self.weather else "",
                "source": getattr(self.weather, "source", ""),
                "note": getattr(self.weather, "note", ""),
                "outdoor_ok": self.weather.outdoor_ok if self.weather else True,
                "temp_c": self.weather.temp_c if self.weather else None,
                "pty": self.weather.pty if self.weather else "없음",
                "sky": self.weather.sky if self.weather else "맑음",
            },
            "when": self.when.isoformat() if self.when else "",
            "notes": self.notes,
        }


def _days_left(item: Content, today: date) -> int | None:
    from .validate import parse_ymd  # 날짜 파서 공유
    end = parse_ymd(item.schedule_end) or parse_ymd(item.schedule_start)
    return (end - today).days if end else None


def _prefer(cands, interests: list[str] | None):
    """관심사로 말한 분류를 앞으로 당긴다. 없으면 순서를 건드리지 않는다."""
    if not interests:
        return cands
    want = tuple(interests)
    hit = [t for t in cands if any(w in (t[0].category_path or t[0].category)
                                   for w in want)]
    rest = [t for t in cands if t not in hit]
    return hit + rest


def passing(items: list[Content], when: datetime, weather: Weather):
    """유효성 판정을 통과한 후보만. (Content, environment, reason) 생성기."""
    for it in items:
        verdict, detail = evaluate(it, when, weather)
        if verdict.ok is True:
            yield it, detail["environment"], verdict.reason


def build_course(items: list[Content], when: datetime, weather: Weather,
                 origin: tuple[float, float] | None = None,
                 max_walk_min: int = 25, want_food: int = 2,
                 area_radius_m: float = 4000.0,
                 interests: list[str] | None = None) -> Course:
    """반나절 코스 하나를 만든다.

    `origin`이 주어지면 그 반경(`area_radius_m`) 안에서 앵커를 고른다.
    "강남에서 3시간"이라고 했는데 성수 행사를 앵커로 잡으면 안 되기 때문이다.
    반경 안에 아무것도 없으면 반경을 풀고 서울 전역에서 다시 찾는다.
    """
    course = Course(weather=weather, when=when)
    today = when.date()

    pool = list(passing(items, when, weather))
    if not pool:
        course.notes.append("지금 조건에 맞는 장소를 찾지 못했습니다.")
        return course

    def near_origin(cands):
        if not origin:
            return cands
        return [t for t in cands
                if t[0].lat and t[0].lon
                and haversine_m(*origin, t[0].lat, t[0].lon) <= area_radius_m]

    all_events = [(i, e, r) for i, e, r in pool if i.is_short_event and i.lat and i.lon]
    foods = [(i, e, r) for i, e, r in pool if "음식" in (i.category_path or i.category)]
    indoors = [(i, e, r) for i, e, r in pool if e == "indoor" and i.lat and i.lon]

    # ---- 앵커 고르기 ----
    # 순서가 중요하다. "홍대에서 3시간"이라고 했는데 반경 밖 행사를 앵커로 잡으면
    # 이동에만 40분을 쓴다. 근처 행사 → 근처 상시 콘텐츠 → 그래도 없으면 전역 순.
    anchor = None
    near_events = near_origin(all_events)
    if near_events:
        near_events.sort(key=lambda t: _days_left(t[0], today) or 999)
        item, env, reason = near_events[0]
        left = _days_left(item, today)
        anchor = Step(item, "anchor", env, reason, ends_today=(left == 0))
    else:
        near_any = near_origin([t for t in pool if t[0].lat and t[0].lon])
        near_any = _prefer(near_any, interests)
        if near_any:
            if origin:
                near_any.sort(key=lambda t: haversine_m(*origin, t[0].lat, t[0].lon))
            item, env, reason = near_any[0]
            anchor = Step(item, "anchor", env, reason)
            course.notes.append(
                "근처에 지금 열린 행사가 없어 상시 콘텐츠로 구성했습니다."
                if weather.outdoor_ok else
                f"{weather.describe()}로 야외 행사가 빠져, 근처 실내 콘텐츠로 구성했습니다.")
        elif all_events:
            all_events.sort(key=lambda t: _days_left(t[0], today) or 999)
            item, env, reason = all_events[0]
            left = _days_left(item, today)
            anchor = Step(item, "anchor", env, reason, ends_today=(left == 0))
            course.notes.append(
                f"근처 {int(area_radius_m / 1000)}km 안에 조건에 맞는 곳이 없어 "
                "서울 전역에서 찾았습니다. 이동 시간을 확인해 주세요.")

    if anchor is None:
        course.notes.append("좌표가 있는 후보가 없어 지도에 표시할 수 없습니다.")
        return course
    course.steps.append(anchor)

    base = (anchor.item.lat, anchor.item.lon)
    if origin:
        anchor.distance_m = haversine_m(*origin, *base)
        anchor.walk_min = walk_minutes(anchor.distance_m)

    used = {anchor.item.cid}

    def nearby(cands, limit):
        out = []
        scored = []
        for it, env, reason in cands:
            if it.cid in used or not (it.lat and it.lon):
                continue
            d = haversine_m(*base, it.lat, it.lon)
            if walk_minutes(d) <= max_walk_min:
                scored.append((d, it, env, reason))
        scored.sort(key=lambda t: t[0])
        for d, it, env, reason in scored[:limit]:
            used.add(it.cid)
            out.append((d, it, env, reason))
        return out

    # ---- 도보권 음식 ----
    if interests and "음식" in interests:
        want_food = max(want_food, 3)        # 먹으러 간다고 했으면 더 붙인다
    for d, it, env, reason in nearby(foods, want_food):
        course.steps.append(Step(it, "food", env, reason,
                                 distance_m=d, walk_min=walk_minutes(d)))

    # ---- 날씨 급변 대비 실내 대안 ----
    # 이미 음식을 붙였으므로 대안은 식당 말고 다른 실내 콘텐츠를 먼저 찾는다
    non_food_indoor = [t for t in indoors
                       if "음식" not in (t[0].category_path or t[0].category)]
    shelter = nearby(non_food_indoor, 1) or nearby(indoors, 1)
    if shelter:
        d, it, env, reason = shelter[0]
        course.steps.append(Step(it, "shelter", env, reason,
                                 distance_m=d, walk_min=walk_minutes(d)))
    elif anchor.environment == "outdoor":
        course.notes.append(
            "도보권에 실내 대안을 찾지 못했습니다. 날씨가 바뀌면 이동이 필요합니다.")

    if not weather.outdoor_ok:
        course.notes.append(
            f"{weather.describe()} — 실외 장소를 후보에서 제외했습니다.")

    _measure_travel(course, origin)

    for s in course.steps:
        if not s.line:
            s.line = _default_line(s, weather, today)
    return course


def _measure_travel(course: Course, origin: tuple[float, float] | None) -> None:
    """구간마다 실제 도보·대중교통 소요시간을 채운다.

    앵커는 출발지에서, 나머지는 바로 앞 장소에서 잰다. 경로 API 키가 없으면
    추정값이 들어가되 provider가 estimate로 표시된다.
    """
    rt = router()
    prev = origin
    for step in course.steps:
        if not (step.item.lat and step.item.lon):
            prev = None
            continue
        here = (step.item.lat, step.item.lon)
        if prev:
            step.travel = rt.best(prev, here)
            walk = step.travel["walk"]
            step.walk_min = walk["minutes"]
            step.distance_m = walk["distance_m"]
        prev = here


def _default_line(step: Step, weather: Weather, today: date) -> str:
    if step.ends_today:
        return "오늘이 마지막 날입니다."
    if step.role == "anchor":
        left = _days_left(step.item, today)
        if left is not None and step.item.is_short_event:
            if left <= 3:
                return f"{left}일 뒤 끝납니다. 지금 아니면 놓칩니다."
            return f"{step.item.schedule_end}까지 열립니다."
        if step.environment == "indoor":
            return "실내라 날씨의 영향을 받지 않습니다."
        if step.environment == "outdoor" and weather.outdoor_ok:
            return f"{weather.describe()} — 지금 야외 활동에 무리가 없습니다."
        return "지금 문을 열었습니다."
    if step.role == "shelter":
        return "날씨가 바뀌면 여기로 피할 수 있습니다."
    if step.role == "food" and step.walk_min:
        return f"앞 장소에서 도보 {step.walk_min}분입니다."
    if step.environment == "indoor":
        return "실내라 날씨의 영향을 받지 않습니다."
    if step.environment == "outdoor" and weather.outdoor_ok:
        return f"{weather.describe()} — 지금 야외 활동에 무리가 없습니다."
    return step.reason
