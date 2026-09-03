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
from .remote import heat_of
from .quality import Diversity, explain, is_touristic, radius_for, rank
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
    why: dict | None = None         # 왜 이 장소가 뽑혔는지 항목별 근거

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
            "why": self.why,
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


def passing(places: list[Place], when: datetime, weather: Weather,
            heat_of=None):
    """일정에 올릴 수 있는 후보. (Place, reason)

    '판정 불가'를 일률적으로 버리지 않는다. 어느 단계에서 몰랐는지가 다르다.

      운영 불가  운영시간 정보가 없을 뿐이다. 일반 영업시간대(ASSUMED_OPEN)를
                 가정하고 넣되 '시간 미상'으로 표시한다. 여기를 버리면
                 803건(21.2%)이 통째로 사라진다.
      날씨 불가  실내외를 모르는데 비가 온다. 이건 넣으면 안 된다 —
                 야외였다면 헛걸음이 되기 때문이다.
    """
    for p in places:
        # 폭염일 때만 위성 열지도를 본다. 비는 동네를 가리지 않는다.
        h = heat_of(p.adm_cd) if (heat_of and not weather.outdoor_ok
                                  and not weather.is_raining) else None
        v, _ = evaluate_place(p, when, weather, h)
        if v.ok is True:
            yield p, v.reason
        elif v.ok is None and v.stage == "운영":
            yield p, v.reason


# 운영정보가 없는 곳에 적용하는 가정 시간대.
# 후보 목록에서는 '판정 불가'를 그대로 두지만, 일정에 넣을 때는 다르다.
# 새벽 4시에 "정보가 없으니 열려 있을지도 모른다"며 넣으면 그건 추천이 아니다.
ASSUMED_OPEN = (10, 20)


MIN_USEFUL_DWELL = 20          # 이보다 짧게 머물 바엔 다른 곳을 간다

# 이 안쪽으로 끝나는 행사만 '지금 아니면 놓친다'로 본다.
# 축제·행사의 99.2%가 이미 끝난 데이터라, 살아 있는 행사는 그 자체로 귀하다.
URGENT_DAYS = 7


def _closing_at(place: Place, when: datetime) -> datetime | None:
    """그날 문 닫는 시각. 여러 구간이면 도착 이후 가장 가까운 마감."""
    wd = when.weekday()
    best = None
    for rule in place.hours.rules:
        if wd not in rule.days:
            continue
        for _, end in rule.ranges:
            try:
                h, m = map(int, end.split(":"))
            except ValueError:
                continue
            close = when.replace(hour=h % 24, minute=m, second=0, microsecond=0)
            if h >= 24 or close <= when:
                continue               # 자정 넘김은 그날 마감으로 보지 않는다
            if best is None or close < best:
                best = close
    return best


def _fit_visit(place: Place, arrive: datetime,
               dwell: int) -> tuple[bool, int, bool]:
    """도착해서 실제로 머물 수 있는가.

    도착 시각만 보면 12:20에 문을 연 곳이 12:30에 닫아도 통과한다.
    머무는 동안 닫히면 체류를 줄이고, 그래도 너무 짧으면 넣지 않는다.

    반환: (넣을까, 조정된 체류시간, 가정을 적용했는지)
    """
    state = check_hours(place.hours, arrive).ok
    if state is False:
        return False, dwell, False

    assumed = False
    if state is None:                  # 운영 정보가 없다 — 일반 시간대로 가정
        lo, hi = ASSUMED_OPEN
        if not (lo <= arrive.hour < hi):
            return False, dwell, True
        assumed = True
        return True, dwell, assumed

    close = _closing_at(place, arrive)
    if close is not None:
        left = int((close - arrive).total_seconds() // 60)
        if left < MIN_USEFUL_DWELL:
            return False, dwell, assumed
        dwell = min(dwell, left)
    return True, dwell, assumed


def build_course(places: list[Place], when: datetime, weather: Weather,
                 origin: tuple[float, float] | None = None,
                 budget_min: int = 240, area_radius_m: float = 4000.0,
                 interests: list[str] | None = None,
                 max_stops: int = 5,
                 taste: Taste | None = None,
                 exclude: set[str] | None = None,
                 avoid: tuple[str, ...] = ()) -> Course:
    """출발 시각과 남은 시간으로 실제 일정을 짠다."""
    course = Course(weather=weather, start=when, budget_min=budget_min)
    today = when.date()
    rt = router()

    # 취향으로 걸러낸 것(영구)과 이번 일정에서만 뺀 것(일회성)을 함께 제외한다
    skip = set(taste.disliked) if taste else set()
    skip |= (exclude or set())
    def build_pool(src):
        """판정을 통과하고 일정에 올릴 수 있는 후보.

        머물 시간이 없는 곳은 일정이 아니다. 숙박은 반나절 코스의 목적지가
        아니라 자는 곳이라 체류시간이 0인데, 그대로 두면 "16:16 도착 16:16
        출발"짜리 항목이 붙는다. 주변 목록에서는 그대로 보인다.
        """
        return [(p, r) for p, r in passing(src, when, weather, heat_of)
                if p.lat and p.lon and is_touristic(p) and p.cid not in skip
                and dwell_minutes(p) >= MIN_USEFUL_DWELL
                and not any(a in (p.content.category_path or p.content.category)
                            for a in avoid)]

    # 반경 밖은 판정할 이유가 없다. 거리로 먼저 자르면 3,788건 중 수백 건만
    # 운영시간·날씨 판정을 거친다 — 요청당 40ms가 여기서 빠진다.
    # 근처가 통째로 비었을 때만 서울 전역을 다시 본다.
    wide = False
    if origin:
        pool = build_pool([p for p in places if p.lat and p.lon
                           and haversine_m(*origin, p.lat, p.lon)
                           <= area_radius_m])
        if not pool:
            pool, wide = build_pool(places), True
    else:
        pool = build_pool(places)

    if not pool:
        course.notes = _diagnose(when, budget_min, weather, 0, origin)
        return course

    # 후보를 직선거리로 고르면 한강 건너편이 '가까운 곳'으로 올라온다.
    # 지도상 276m인데 걸어서 684m인 구간이 서울 도심에 흔하다. 그렇다고
    # 후보마다 경로를 물으면 수백 번을 부르게 되니, 직선거리로 먼저 추린
    # 상위 후보만 한 번의 매트릭스 요청으로 실제 보행 거리를 받는다.
    dist = _walk_distances(pool, origin, rt) if origin else {}

    def near(cands, radius=area_radius_m):
        if not origin or not wide:
            return cands
        return [t for t in cands
                if haversine_m(*origin, t[0].lat, t[0].lon) <= radius]

    events = [t for t in pool if t[0].content.is_short_event]
    foods = [t for t in pool if "음식" in (t[0].content.category_path
                                           or t[0].content.category)]
    indoors = [t for t in pool if t[0].environment == "indoor"]

    # ---------- 앵커 ----------
    # 첫 장소가 나머지 일정의 위치를 결정한다. 그래서 여기만은 순위를
    # 제대로 세운다. "홍대에서 3시간"인데 반경 밖을 앵커로 잡으면
    # 이동에만 40분을 쓰므로, 근처를 먼저 보고 없을 때만 서울 전역으로 넓힌다.
    anchor_pick = None
    near_pool = [] if wide else pool
    near_events = [] if wide else events
    if near_pool:
        # 순위(거리·품질·인기·취향)를 먼저 세우고, 곧 끝나는 행사만 앞으로 당긴다.
        # 행사를 무조건 앵커로 두면 두 달 남은 전시가 '역사관광을 보고 싶다'는
        # 사람의 덕수궁을 밀어낸다. '지금 아니면 놓친다'가 사실일 때만 앞선다.
        ordered = _urgent_first(
            _prefer(rank(near_pool, origin, taste, dist=dist), interests), today)
        anchor_pick = ordered[0]
        personalized = bool(interests) or (taste is not None and not taste.is_empty)
        if not anchor_pick[0].content.is_short_event:
            course.notes.append(
                "관심사에 맞는 곳을 근처 행사보다 먼저 넣었습니다."
                if near_events and personalized else
                "근처 행사보다 점수가 높은 곳으로 시작합니다."
                if near_events else
                "근처에 지금 열린 행사가 없어 상시 콘텐츠로 시작합니다."
                if weather.outdoor_ok else
                f"{weather.describe()}로 야외 행사가 빠져, 근처 실내 콘텐츠로 시작합니다.")
    else:
        pick_all = _urgent_first(
            _prefer(rank(pool, origin, taste, dist=dist), interests), today)
        if pick_all:
            anchor_pick = pick_all[0]
            course.notes.append(
                f"근처 {int(area_radius_m / 1000)}km 안에 조건에 맞는 곳이 없어 "
                "서울 전역에서 찾았습니다. 이동 시간을 확인해 주세요.")

    if anchor_pick is None:
        course.notes = _diagnose(when, budget_min, weather, len(pool), origin)
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
        travel = rt.best(here, dest, measure=False) if here else None
        move = 0
        if travel:
            rec = travel["recommended"]
            move = (travel[rec] or {}).get("minutes", 0)

        arrive = cursor + timedelta(minutes=move)
        dwell = dwell_minutes(place)
        if arrive + timedelta(minutes=dwell) > deadline:
            return False
        ok, dwell, assumed = _fit_visit(place, arrive, dwell)
        if not ok or arrive + timedelta(minutes=dwell) > deadline:
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
        step.why = explain(place, origin, taste, dist=dist)
        course.steps.append(step)
        used.add(place.cid)
        diversity.add(place)
        cursor = step.depart
        here = dest
        return True

    if not add(anchor_pick, "anchor"):
        course.notes = _diagnose(when, budget_min, weather, len(pool), origin)
        return course

    def pick_from(cands, radius=1400.0):
        """현재 위치 반경 안에서 품질×거리 순. 다양성 상한을 넘는 건 뺀다."""
        base = here
        near_by = [(p, r) for p, r in cands
                   if p.cid not in used
                   and haversine_m(*base, p.lat, p.lon) <= radius
                   and diversity.allows(p)]
        return rank(near_by, base, taste, dist=dist if base == origin else None)

    # 식사 시간대면 음식을 먼저, 아니면 관심사를 먼저 붙인다.
    # 다만 앵커가 이미 식당이면 밥 먹고 나와 또 밥집으로 가게 된다.
    # 강남처럼 식당이 압도적으로 많은 동네에서 실제로 그렇게 나왔다.
    anchor_is_food = "음식" in (anchor_pick[0].content.category_path
                                or anchor_pick[0].content.category)
    want_food = (not anchor_is_food
                 and (_is_meal_time(cursor) or (interests and "음식" in interests)))
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
        travel = rt.best(here, (p.lat, p.lon), measure=False) if here else None
        course.backup = Step(place=p, role="shelter", reason=r, travel=travel,
                             dwell_min=dwell_minutes(p),
                             line="날씨가 바뀌면 여기로 피할 수 있습니다.")
    elif not weather.outdoor_ok:
        course.notes.append("도보권에 실내 대안을 찾지 못했습니다.")

    _measure_legs(course, rt, origin, deadline)

    if not weather.outdoor_ok:
        course.notes.append(f"{weather.describe()} — 실외 장소를 후보에서 제외했습니다.")

    # 일정이 비었으면 앞서 붙인 안내는 사실과 다르다. 진단으로 갈아 끼운다.
    if not course.steps:
        course.notes = _diagnose(when, budget_min, weather, len(pool), origin)
        return course

    if assumed_count[0]:
        course.notes.append(
            f"{assumed_count[0]}곳은 운영시간 정보가 없어 일반적인 영업시간"
            f"({ASSUMED_OPEN[0]}~{ASSUMED_OPEN[1]}시)으로 가정했습니다. "
            "방문 전 확인해 주세요.")

    for s in course.steps:
        if not s.line:
            s.line = _default_line(s, weather, today)
    return course


def _diagnose(when: datetime, budget_min: int, weather: Weather,
              pool_size: int, origin) -> list[str]:
    """왜 일정을 못 만들었는지 정확히 말한다.

    "조건에 맞는 곳이 없다"는 답은 사용자가 뭘 바꿔야 할지 알려 주지 않는다.
    """
    notes = []
    # 숙박은 0이라 그냥 min을 쓰면 이 분기가 영영 걸리지 않는다
    shortest = min([v for v in DWELL.values() if v > 0] or [40])
    if budget_min < shortest:
        notes.append(
            f"{budget_min}분으로는 한 곳도 담기 어렵습니다. "
            f"한 장소에 보통 {shortest}분 이상 머뭅니다.")
    elif not (ASSUMED_OPEN[0] <= when.hour < ASSUMED_OPEN[1]) and when.hour < 9:
        notes.append(
            f"{when:%H시}에는 문을 연 곳이 거의 없습니다. "
            "오전 10시 이후로 잡아 보세요.")
    elif when.hour >= 21:
        notes.append(
            f"{when:%H시}에는 대부분 영업이 끝났습니다. "
            "내일 오전으로 잡아 보세요.")
    elif pool_size == 0:
        if not weather.outdoor_ok:
            notes.append(
                f"{weather.describe()}에 갈 만한 실내 장소를 근처에서 "
                "찾지 못했습니다. 위치를 도심 쪽으로 옮겨 보세요.")
        else:
            notes.append("이 시각에 문을 연 곳을 찾지 못했습니다. "
                         "시간대를 바꿔 보세요.")
    else:
        notes.append("근처에서 일정을 만들지 못했습니다. "
                     "시간을 늘리거나 위치를 옮겨 보세요.")
    return notes


# 우회율을 재기 위해 실제로 물어보는 표본 수.
# 공개 OSRM은 목적지가 20개를 넘으면 한 요청에 10초를 물린다(스로틀).
# 10개까지는 0.9초다. 그래서 조금 물어보고 나머지는 그 비율로 환산한다.
SAMPLE_N = 10
# 직선거리 구간. 짧은 구간일수록 우회율이 크다 — 도심 276m가 걸어서 684m다.
BANDS = (300.0, 700.0, 1500.0, 3000.0, float("inf"))


def _walk_distances(pool, origin, rt) -> dict:
    """후보별 보행 거리(cid → m).

    후보마다 경로를 물으면 수백 번을 부르게 되고, 매트릭스로 한 번에
    물으면 공개 서버가 10초를 물린다. 그래서 **거리 구간마다 표본을 실측해
    국소 우회율을 재고, 같은 구간의 나머지에 그 비율을 적용한다.**

    직선 × 1.3이라는 고정 우회율보다 훨씬 낫다. 실제로 도심 276m 구간의
    우회율은 2.48배이고 12km 구간은 1.17배로, 거리에 따라 크게 다르다.
    최종 일정에 오른 서넛은 뒤에서 다시 정확히 잰다(_measure_legs).
    """
    if not pool or origin is None:
        return {}
    straight = {t[0].cid: haversine_m(*origin, t[0].lat, t[0].lon) for t in pool}

    # 구간마다 하나씩, 남는 자리는 가까운 쪽부터 — 가까운 곳이 일정에 오른다
    by_band: dict[int, list] = {}
    for t in pool:
        d = straight[t[0].cid]
        by_band.setdefault(next(i for i, b in enumerate(BANDS) if d <= b),
                           []).append(t[0])
    sample = []
    for band in sorted(by_band):
        got = sorted(by_band[band], key=lambda p: straight[p.cid])
        sample += got[:1] + got[len(got) // 2: len(got) // 2 + 1]
    rest = sorted((t[0] for t in pool), key=lambda p: straight[p.cid])
    for p in rest:
        if len(sample) >= SAMPLE_N:
            break
        if p not in sample:
            sample.append(p)
    sample = sample[:SAMPLE_N]

    measured = rt.walk_matrix(origin, [(p.lat, p.lon) for p in sample])
    ratios: dict[int, list[float]] = {}
    out: dict[str, float] = {}
    for p, m in zip(sample, measured):
        if m is None or straight[p.cid] < 30:
            continue
        out[p.cid] = m
        band = next(i for i, b in enumerate(BANDS) if straight[p.cid] <= b)
        ratios.setdefault(band, []).append(m / straight[p.cid])
    if not out:
        return {}                       # 하나도 못 쟀으면 직선으로 돈다

    allr = [r for v in ratios.values() for r in v]
    fallback = sorted(allr)[len(allr) // 2]
    for t in pool:
        p = t[0]
        if p.cid in out:
            continue
        band = next(i for i, b in enumerate(BANDS) if straight[p.cid] <= b)
        got = sorted(ratios.get(band, []))
        r = got[len(got) // 2] if got else fallback
        out[p.cid] = straight[p.cid] * r
    return out


def _measure_legs(course: Course, rt, origin, deadline: datetime) -> None:
    """확정된 구간만 실제 경로로 다시 재고, 바뀐 시각으로 일정을 다시 맞춘다.

    후보를 고르는 동안에는 직선 추정을 쓴다 — 한 자리에 여덟 곳을 시도하므로
    시도마다 경로 API를 부르면 일정 하나에 수십 번을 묻게 된다. 대신 다 정해진
    뒤에 실제로 쓰는 서넛만 동시에 물어 실측으로 바꾼다.

    실측이 추정보다 길게 나오면 뒤 일정이 밀린다. 밀린 시각으로 운영시간을
    다시 보고, 더 이상 들어가지 않는 뒤쪽은 잘라 낸다. 재지 않고 두면
    '도착 시각에 열려 있는가'라는 이 앱의 전제가 그 순간 거짓이 된다.
    """
    if not course.steps or origin is None:
        return

    pairs, here = [], origin
    for st in course.steps:
        pairs.append((here, (st.place.lat, st.place.lon)))
        here = (st.place.lat, st.place.lon)
    measured = rt.measure_many(pairs)

    kept: list[Step] = []
    cursor = course.start
    for st, travel in zip(course.steps, measured):
        rec = travel["recommended"]
        move = (travel[rec] or {}).get("minutes", 0)
        arrive = cursor + timedelta(minutes=move)
        ok, dwell, assumed = _fit_visit(st.place, arrive, dwell_minutes(st.place))
        if not ok or arrive + timedelta(minutes=dwell) > deadline:
            break                      # 여기부터는 실제 이동시간으로는 못 간다
        st.travel = travel
        st.arrive, st.depart = arrive, arrive + timedelta(minutes=dwell)
        st.dwell_min, st.hours_assumed = dwell, assumed
        cursor = st.depart
        kept.append(st)

    if len(kept) < len(course.steps):
        course.notes.append(
            f"실제 이동시간으로 다시 계산해 뒤쪽 {len(course.steps) - len(kept)}곳을 "
            "뺐습니다. 추정보다 오래 걸리는 구간이 있습니다.")
    course.steps = kept


def _is_meal_time(t: datetime) -> bool:
    return 11 <= t.hour < 14 or 17 <= t.hour < 21


def _urgent_first(cands, today: date):
    """곧 끝나는 행사만 순위를 뛰어넘어 앞으로 온다.

    끝이 임박했다는 것은 취향보다 우선하는 정보다 — 취향에 덜 맞아도
    다시 올 기회가 없기 때문이다. 다만 그 특권은 URGENT_DAYS 안쪽에만 준다.
    """
    def urgency(t):
        if not t[0].content.is_short_event:
            return 0
        left = _days_left(t[0], today)
        if left is None or left < 0 or left > URGENT_DAYS:
            return 0
        return URGENT_DAYS - left + 1

    return [t for _, t in sorted(enumerate(cands),
                                 key=lambda it: (-urgency(it[1]), it[0]))]


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
