"""[C] 코스 구성 — 시간표가 있는 반나절 일정.

목록이 아니라 일정이다. 몇 시에 출발해 몇 시에 도착하고 얼마나 머무는지가
정해져야 실제로 쓸 수 있다. 그래서 두 가지를 바꿨다.

**도착 시각에 열려 있는가를 본다.** '지금 열려 있다'는 40분 뒤에 도착할
장소에는 해당하지 않는 이야기다. 이동 시간을 더한 시각으로 다시 판정한다.

**남은 시간을 지킨다.** "3시간"이라고 했으면 이동과 체류를 합쳐 3시간 안에
끝나야 한다. 넘치면 거기서 코스를 닫는다.

날씨 대비 실내 대안은 일정에 넣지 않고 따로 둔다. 그건 순서가 아니라
플랜 B이고, 시간을 소비하지 않기 때문이다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .index import Place
from .quality import Diversity, is_touristic, radius_for, rank
from .taste import Taste
from .routing import haversine_m, router
from .validate import Weather, check_hours, evaluate_place, parse_ymd

# 분류별 표준 체류시간(분). 실제 관광객의 평균 체류에 가깝게 잡았다.
DWELL = {
    "축제/공연/행사": 90,
    "문화관광": 70,
    "체험관광": 80,
    "역사관광": 60,
    "자연관광": 50,
    "음식": 60,
    "쇼핑": 45,
    "숙박": 0,
}
DWELL_SUB = {"카페/찻집": 40, "주점": 70, "전시시설": 70, "공연시설": 100}
DEFAULT_DWELL = 55


def dwell_minutes(place: Place) -> int:
    path = place.content.category_path or place.content.category
    for sub, m in DWELL_SUB.items():
        if sub in path:
            return m
    return DWELL.get(place.content.category, DEFAULT_DWELL)


@dataclass
class Step:
    place: Place
    role: str                       # anchor | food | spot
    reason: str
    line: str = ""
    arrive: datetime | None = None
    depart: datetime | None = None
    dwell_min: int = 0
    travel: dict | None = None      # 앞 장소에서 여기까지
    ends_today: bool = False
    hours_assumed: bool = False     # 운영정보가 없어 일반 시간대로 가정했는가

    def to_dict(self, lang: str = "ko") -> dict:
        i = self.place.content
        t = self.place.text(lang)
        return {
            "cid": i.cid, "title": t["title"], "category": i.category,
            "category_path": t["category_path"], "summary": t["summary"],
            "address": t["address"], "lat": i.lat, "lon": i.lon,
            "subway": t["subway"], "use_time": t["use_time"],
            "closed_days": t["closed_days"], "tags": t["tags"],
            "text_lang": t["lang"],
            "schedule_start": i.schedule_start, "schedule_end": i.schedule_end,
            "accessibility": i.accessibility, "homepage": t["homepage"],
            "phone": i.phone,
            "gu": self.place.gu, "dong": self.place.dong,
            "role": self.role, "environment": self.place.environment,
            "hours_confidence": self.place.hours.confidence,
            "verdict_reason": self.reason, "line": self.line,
            "arrive": self.arrive.strftime("%H:%M") if self.arrive else None,
            "depart": self.depart.strftime("%H:%M") if self.depart else None,
            "dwell_min": self.dwell_min,
            "travel": self.travel,
            "walk_min": (self.travel or {}).get("walk", {}).get("minutes"),
            "ends_today": self.ends_today,
            "hours_assumed": self.hours_assumed,
        }


@dataclass
class Course:
    steps: list[Step] = field(default_factory=list)
    backup: Step | None = None          # 날씨가 바뀌면 갈 실내 대안
    weather: Weather | None = None
    start: datetime | None = None
    budget_min: int = 240
    notes: list[str] = field(default_factory=list)

    @property
    def end(self) -> datetime | None:
        return self.steps[-1].depart if self.steps else self.start

    def to_dict(self, lang: str = "ko") -> dict:
        travel = sum(_leg_minutes(s) for s in self.steps)
        dwell = sum(s.dwell_min for s in self.steps)
        return {
            "steps": [s.to_dict(lang) for s in self.steps],
            "backup": self.backup.to_dict(lang) if self.backup else None,
            "start": self.start.strftime("%H:%M") if self.start else "",
            "end": self.end.strftime("%H:%M") if self.end else "",
            "total_min": travel + dwell,
            "travel_min": travel,
            "dwell_min": dwell,
            "budget_min": self.budget_min,
            "weather": {
                "desc": self.weather.describe() if self.weather else "",
                "source": getattr(self.weather, "source", ""),
                "note": getattr(self.weather, "note", ""),
                "outdoor_ok": self.weather.outdoor_ok if self.weather else True,
                "temp_c": self.weather.temp_c if self.weather else None,
                "pty": self.weather.pty if self.weather else "없음",
                "sky": self.weather.sky if self.weather else "맑음",
            },
            "when": self.start.isoformat() if self.start else "",
            "notes": self.notes,
        }


def _leg_minutes(step: Step) -> int:
    tv = step.travel or {}
    rec = tv.get("recommended")
    return (tv.get(rec) or {}).get("minutes", 0) if rec else 0


def _days_left(place: Place, today: date) -> int | None:
    c = place.content
    end = parse_ymd(c.schedule_end) or parse_ymd(c.schedule_start)
    return (end - today).days if end else None


def passing(places: list[Place], when: datetime, weather: Weather):
    """지금 판정을 통과한 후보. (Place, reason)"""
    for p in places:
        v, _ = evaluate_place(p, when, weather)
        if v.ok is True:
            yield p, v.reason


# 운영정보가 없는 곳에 적용하는 가정 시간대.
# 후보 목록에서는 '판정 불가'를 그대로 두지만, 일정에 넣을 때는 다르다.
# 새벽 4시에 "정보가 없으니 열려 있을지도 모른다"며 넣으면 그건 추천이 아니다.
ASSUMED_OPEN = (10, 20)


def _open_on_arrival(place: Place, arrive: datetime) -> tuple[bool, bool]:
    """도착 시각에 열려 있는가. (통과 여부, 가정을 적용했는지)"""
    state = check_hours(place.hours, arrive).ok
    if state is True:
        return True, False
    if state is False:
        return False, False
    # 판정 불가 — 일반적인 영업시간대로 가정한다
    lo, hi = ASSUMED_OPEN
    return lo <= arrive.hour < hi, True


def build_course(places: list[Place], when: datetime, weather: Weather,
                 origin: tuple[float, float] | None = None,
                 budget_min: int = 240, area_radius_m: float = 4000.0,
                 interests: list[str] | None = None,
                 max_stops: int = 5,
                 taste: Taste | None = None,
                 exclude: set[str] | None = None) -> Course:
    """출발 시각과 남은 시간으로 실제 일정을 짠다."""
    course = Course(weather=weather, start=when, budget_min=budget_min)
    today = when.date()
    rt = router()

    # 취향으로 걸러낸 것(영구)과 이번 일정에서만 뺀 것(일회성)을 함께 제외한다
    skip = set(taste.disliked) if taste else set()
    skip |= (exclude or set())
    pool = [(p, r) for p, r in passing(places, when, weather)
            if p.lat and p.lon and is_touristic(p) and p.cid not in skip]
    if not pool:
        course.notes.append("지금 조건에 맞는 장소를 찾지 못했습니다.")
        return course

    def near(cands, radius=area_radius_m):
        if not origin:
            return cands
        return [t for t in cands
                if haversine_m(*origin, t[0].lat, t[0].lon) <= radius]

    events = [t for t in pool if t[0].content.is_short_event]
    foods = [t for t in pool if "음식" in (t[0].content.category_path
                                           or t[0].content.category)]
    indoors = [t for t in pool if t[0].environment == "indoor"]

    # ---------- 앵커 ----------
    # "홍대에서 3시간"인데 반경 밖 행사를 앵커로 잡으면 이동에만 40분을 쓴다.
    # 근처 행사 → 근처 관심사 → 근처 아무거나 → 전역 행사 순.
    anchor_pick = None
    near_events = near(events)
    if near_events:
        # 끝나는 날이 임박한 것 우선, 같으면 품질 순
        near_events = rank(near_events, origin, taste)
        near_events.sort(key=lambda t: _days_left(t[0], today) or 999)
        anchor_pick = near_events[0]
    else:
        near_any = _prefer(rank(near(pool), origin, taste), interests)
        if near_any:
            anchor_pick = near_any[0]
            course.notes.append(
                "근처에 지금 열린 행사가 없어 상시 콘텐츠로 시작합니다."
                if weather.outdoor_ok else
                f"{weather.describe()}로 야외 행사가 빠져, 근처 실내 콘텐츠로 시작합니다.")
        elif events:
            events.sort(key=lambda t: _days_left(t[0], today) or 999)
            anchor_pick = events[0]
            course.notes.append(
                f"근처 {int(area_radius_m / 1000)}km 안에 조건에 맞는 곳이 없어 "
                "서울 전역에서 찾았습니다. 이동 시간을 확인해 주세요.")

    if anchor_pick is None:
        course.notes.append("조건에 맞는 후보를 찾지 못했습니다.")
        return course

    # ---------- 일정 쌓기 ----------
    deadline = when + timedelta(minutes=budget_min)
    assumed_count = [0]
    diversity = Diversity()
    used: set[str] = set()
    cursor = when
    here = origin

    def add(pick, role) -> bool:
        """도착 시각을 계산해 일정에 넣는다. 시간이 모자라면 False."""
        nonlocal cursor, here
        place, reason = pick
        dest = (place.lat, place.lon)
        travel = rt.best(here, dest) if here else None
        move = 0
        if travel:
            rec = travel["recommended"]
            move = (travel[rec] or {}).get("minutes", 0)

        arrive = cursor + timedelta(minutes=move)
        dwell = dwell_minutes(place)
        if arrive + timedelta(minutes=dwell) > deadline:
            return False
        ok, assumed = _open_on_arrival(place, arrive)
        if not ok:
            return False
        if assumed:
            assumed_count[0] += 1

        step = Step(
            place=place, role=role, reason=reason, travel=travel,
            arrive=arrive, depart=arrive + timedelta(minutes=dwell),
            dwell_min=dwell, hours_assumed=assumed,
            ends_today=(_days_left(place, today) == 0
                        and place.content.is_short_event),
        )
        course.steps.append(step)
        used.add(place.cid)
        diversity.add(place)
        cursor = step.depart
        here = dest
        return True

    if not add(anchor_pick, "anchor"):
        course.notes.append(
            "남은 시간이 짧아 일정을 만들지 못했습니다. 시간을 늘려 보세요.")
        return course

    def pick_from(cands, radius=1400.0):
        """현재 위치 반경 안에서 품질×거리 순. 다양성 상한을 넘는 건 뺀다."""
        base = here
        near_by = [(p, r) for p, r in cands
                   if p.cid not in used
                   and haversine_m(*base, p.lat, p.lon) <= radius
                   and diversity.allows(p)]
        return rank(near_by, base, taste)

    # 식사 시간대면 음식을 먼저, 아니면 관심사를 먼저 붙인다
    want_food = _is_meal_time(cursor) or (interests and "음식" in interests)
    order = ["food", "spot"] if want_food else ["spot", "food"]

    while len(course.steps) < max_stops and cursor < deadline:
        added = False
        for kind in order:
            cands = pick_from(foods if kind == "food"
                              else _prefer(pool, interests))
            for pick in cands[:8]:
                if add(pick, kind if kind == "food" else "spot"):
                    added = True
                    break
            if added:
                break
        if not added:
            break
        order = ["spot", "food"] if order[0] == "food" else ["food", "spot"]

    # ---------- 플랜 B: 날씨가 바뀌면 갈 실내 ----------
    shelter_pool = [t for t in indoors
                    if t[0].cid not in used
                    and "음식" not in (t[0].content.category_path
                                      or t[0].content.category)]
    shelter = pick_from(shelter_pool, 1600.0) or pick_from(
        [t for t in indoors if t[0].cid not in used], 1600.0)
    if shelter:
        p, r = shelter[0]
        travel = rt.best(here, (p.lat, p.lon)) if here else None
        course.backup = Step(place=p, role="shelter", reason=r, travel=travel,
                             dwell_min=dwell_minutes(p),
                             line="날씨가 바뀌면 여기로 피할 수 있습니다.")
    elif not weather.outdoor_ok:
        course.notes.append("도보권에 실내 대안을 찾지 못했습니다.")

    if not weather.outdoor_ok:
        course.notes.append(f"{weather.describe()} — 실외 장소를 후보에서 제외했습니다.")

    if assumed_count[0]:
        course.notes.append(
            f"{assumed_count[0]}곳은 운영시간 정보가 없어 일반적인 영업시간"
            f"({ASSUMED_OPEN[0]}~{ASSUMED_OPEN[1]}시)으로 가정했습니다. "
            "방문 전 확인해 주세요.")

    for s in course.steps:
        if not s.line:
            s.line = _default_line(s, weather, today)
    return course


def _is_meal_time(t: datetime) -> bool:
    return 11 <= t.hour < 14 or 17 <= t.hour < 21


def _prefer(cands, interests: list[str] | None):
    """관심사로 말한 분류를 앞으로 당긴다."""
    if not interests:
        return cands
    hit = [t for t in cands
           if any(w in (t[0].content.category_path or t[0].content.category)
                  for w in interests)]
    rest = [t for t in cands if t not in hit]
    return hit + rest


def _default_line(step: Step, weather: Weather, today: date) -> str:
    if step.ends_today:
        return "오늘이 마지막 날입니다."
    if step.role == "anchor":
        left = _days_left(step.place, today)
        if left is not None and step.place.content.is_short_event:
            if left <= 3:
                return f"{left}일 뒤 끝납니다. 지금 아니면 놓칩니다."
            return f"{step.place.content.schedule_end}까지 열립니다."
    if step.role == "food":
        if step.arrive and _is_meal_time(step.arrive):
            return f"{step.arrive:%H:%M} 도착이면 식사 시간에 맞습니다."
        return "일정 사이에 쉬어 가기 좋습니다."
    if step.place.hours.confidence == "high":
        close = _closing_soon(step)
        if close:
            return close
        return f"{step.arrive:%H:%M}에 문을 열어 두는 곳입니다."
    if step.place.environment == "indoor":
        return "실내라 날씨의 영향을 받지 않습니다."
    if step.place.environment == "outdoor" and weather.outdoor_ok:
        return f"{weather.describe()} — 야외 활동에 무리가 없습니다."
    return "이 시각에 이용할 수 있습니다."


def _closing_soon(step: Step) -> str | None:
    """마감이 체류 예정 시간과 겹치면 알려 준다."""
    if not step.depart:
        return None
    wd = step.depart.weekday()
    for rule in step.place.hours.rules:
        if wd not in rule.days:
            continue
        for _, end in rule.ranges:
            try:
                h, m = map(int, end.split(":"))
            except ValueError:
                continue
            closing = step.depart.replace(hour=h % 24, minute=m,
                                          second=0, microsecond=0)
            gap = (closing - step.depart).total_seconds() / 60
            if 0 <= gap <= 45:
                return f"{end}에 문을 닫습니다. 여유를 두고 움직이세요."
    return None


# 하위 호환 — 기존 호출부가 쓰던 이름
def walk_minutes(meters: float) -> int:
    return max(1, round(meters / 67.0))
