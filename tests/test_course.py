"""일정 구성 테스트.

목록이 아니라 일정이라는 점이 이 모듈의 전부다. 시각이 맞아야 하고,
남은 시간을 넘지 않아야 하고, **도착할 시각에** 열려 있어야 한다.
"""
from datetime import datetime

import pytest

from weatherfit.course import (ASSUMED_OPEN, DWELL, MEALS, MIN_USEFUL_DWELL,
                               build_course, dwell_minutes, meal_at,
                               _is_meal_time)
from weatherfit.index import Place
from weatherfit.models import Content
from weatherfit.normalize import parse_hours, tag_environment
from weatherfit.validate import Weather

CLEAR = Weather(temp_c=22.0)
RAIN = Weather(temp_c=19.0, precip_mm=4.0, pty="비", sky="흐림")
NOON = datetime(2026, 9, 3, 12, 0)


def place(title, category="문화관광", path="", use_time="매일 09:00~20:00",
          lat=37.5665, lon=126.9780, desc="설명" * 60, tags=None,
          start="", end="", cid=None):
    c = Content(
        cid=cid or f"KO{abs(hash(title)) % 10**7}",
        title=title, category=category, category_path=path or category,
        use_time_raw=use_time, lat=lat, lon=lon, description=desc,
        tags=tags or ["a", "b", "c"], schedule_start=start, schedule_end=end,
    )
    env, why = tag_environment(c.category, c.title, c.description, c.tags,
                               c.category_path)
    return Place(content=c, hours=parse_hours(use_time), environment=env,
                 env_reason=why)


def near(i):
    """서로 200m쯤 떨어진 좌표를 만든다."""
    return 37.5665 + i * 0.0018, 126.9780 + i * 0.0018


class TestDwell:
    def test_분류별_체류시간(self):
        assert dwell_minutes(place("축제", category="축제/공연/행사")) == DWELL["축제/공연/행사"]
        assert dwell_minutes(place("식당", category="음식", path="음식 > 한식")) == DWELL["음식"]

    def test_카페는_식사보다_짧다(self):
        cafe = place("카페", category="음식", path="음식 > 카페/찻집")
        meal = place("식당", category="음식", path="음식 > 한식")
        assert dwell_minutes(cafe) < dwell_minutes(meal)


class TestTimeline:
    def test_시각이_이어진다(self):
        # 분류를 섞는다. 같은 분류만 주면 다양성 상한에 걸려 두 곳에서 멈춘다.
        cats = [("문화관광", "문화관광 > 전시시설"), ("음식", "음식 > 한식"),
                ("쇼핑", "쇼핑 > 쇼핑몰"), ("체험관광", "체험관광 > 공예체험"),
                ("음식", "음식 > 카페/찻집")]
        places = [place(f"장소{i}", category=c, path=p2,
                        lat=near(i)[0], lon=near(i)[1])
                  for i, (c, p2) in enumerate(cats)]
        c = build_course(places, NOON, CLEAR, origin=(37.5665, 126.9780),
                         budget_min=300)
        assert len(c.steps) >= 2
        for a, b in zip(c.steps, c.steps[1:]):
            assert a.depart <= b.arrive        # 앞 장소를 떠난 뒤 도착한다

    def test_체류시간이_반영된다(self):
        c = build_course([place("한 곳")], NOON, CLEAR,
                         origin=(37.5665, 126.9780), budget_min=300)
        s = c.steps[0]
        assert (s.depart - s.arrive).total_seconds() / 60 == s.dwell_min

    def test_남은_시간을_넘지_않는다(self):
        places = [place(f"장소{i}", lat=near(i)[0], lon=near(i)[1]) for i in range(8)]
        c = build_course(places, NOON, CLEAR, origin=(37.5665, 126.9780),
                         budget_min=120)
        assert c.end <= NOON.replace(hour=14)

    def test_시간이_아주_짧으면_빈_일정과_안내(self):
        far = place("먼 곳", lat=37.7000, lon=127.1500)
        c = build_course([far], NOON, CLEAR, origin=(37.5665, 126.9780),
                         budget_min=30)
        assert c.steps == []
        assert c.notes


class TestArrivalHours:
    def test_도착_시각에_닫혀_있으면_넣지_않는다(self):
        """'지금 열려 있다'는 40분 뒤 도착할 곳에는 해당하지 않는다."""
        closed = place("이미 닫은 곳", use_time="매일 09:00~11:00")
        c = build_course([closed], datetime(2026, 9, 3, 12, 20), CLEAR,
                         origin=(37.5665, 126.9780), budget_min=240)
        assert all(s.place.cid != closed.cid for s in c.steps)

    def test_머무는_동안_닫히면_체류를_줄인다(self):
        """12:20에 열려 있어도 12:30에 닫으면 70분을 머물 수 없다."""
        soon = place("곧 닫는 곳", use_time="매일 09:00~13:00")
        c = build_course([soon], datetime(2026, 9, 3, 12, 20), CLEAR,
                         origin=(37.5665, 126.9780), budget_min=240)
        assert c.steps
        s = c.steps[0]
        assert s.depart.hour <= 13 and s.dwell_min <= 40

    def test_너무_짧게_남았으면_넣지_않는다(self):
        soon = place("곧 닫는 곳", use_time="매일 09:00~12:30")
        c = build_course([soon], datetime(2026, 9, 3, 12, 20), CLEAR,
                         origin=(37.5665, 126.9780), budget_min=240)
        assert c.steps == []

    def test_운영정보가_없으면_일반_영업시간으로_가정한다(self):
        blank = place("정보 없음", use_time="")
        lo, hi = ASSUMED_OPEN
        day = build_course([blank], datetime(2026, 9, 3, lo + 1, 0), CLEAR,
                           origin=(37.5665, 126.9780), budget_min=240)
        night = build_course([blank], datetime(2026, 9, 3, 4, 0), CLEAR,
                             origin=(37.5665, 126.9780), budget_min=240)
        assert day.steps and not night.steps

    def test_가정을_적용하면_알려준다(self):
        blank = place("정보 없음", use_time="")
        c = build_course([blank], datetime(2026, 9, 3, 12, 0), CLEAR,
                         origin=(37.5665, 126.9780), budget_min=240)
        assert c.steps[0].hours_assumed is True
        assert any("가정" in n for n in c.notes)


class TestWeather:
    def test_비오면_실외가_빠진다(self):
        park = place("공원", category="문화관광", path="문화관광 > 도시공원")
        museum = place("박물관", category="문화관광", path="문화관광 > 전시시설",
                       lat=near(1)[0], lon=near(1)[1])
        c = build_course([park, museum], NOON, RAIN,
                         origin=(37.5665, 126.9780), budget_min=240)
        assert all(s.place.environment != "outdoor" for s in c.steps)

    def test_비오면_이유를_남긴다(self):
        c = build_course([place("실내", path="문화관광 > 전시시설")], NOON, RAIN,
                         origin=(37.5665, 126.9780), budget_min=240)
        assert any("실외" in n for n in c.notes)


class TestDiversityInCourse:
    def test_식당만_나오지_않는다(self):
        foods = [place(f"식당{i}", category="음식", path="음식 > 한식",
                       lat=near(i)[0], lon=near(i)[1]) for i in range(6)]
        c = build_course(foods, NOON, CLEAR, origin=(37.5665, 126.9780),
                         budget_min=360)
        assert sum(1 for s in c.steps if s.place.content.category == "음식") <= 2

    def test_관광지가_아닌_곳은_안_들어온다(self):
        c = build_course([place("메디킹덤 약국", category="쇼핑")], NOON, CLEAR,
                         origin=(37.5665, 126.9780), budget_min=240)
        assert c.steps == []


class TestExclude:
    def test_교체하면_다른_곳이_나온다(self):
        places = [place(f"장소{i}", lat=near(i)[0], lon=near(i)[1], cid=f"KO{i}")
                  for i in range(4)]
        first = build_course(places, NOON, CLEAR, origin=(37.5665, 126.9780),
                             budget_min=300)
        top = first.steps[0].place.cid
        again = build_course(places, NOON, CLEAR, origin=(37.5665, 126.9780),
                             budget_min=300, exclude={top})
        assert all(s.place.cid != top for s in again.steps)


class TestMealTime:
    @pytest.mark.parametrize("hour,expected", [
        (12, True), (18, True), (15, False), (9, False), (23, False),
    ])
    def test_식사_시간대(self, hour, expected):
        assert _is_meal_time(datetime(2026, 9, 3, hour, 0)) is expected


class TestMealsRequested:
    """끼니를 고르면 그 시간대에 식당이 하나 들어간다.

    '식사 시간이면 음식을 먼저 본다' 정도의 선호로는 밥때가 그냥 지나간다.
    일정이 촘촘하면 관광지가 먼저 붙어 12시가 이동 중에 지나가 버린다.
    """

    # 다양성 상한이 소분류당 1곳이라, 소분류를 갈라 두지 않으면 두 번째
    # 식당이 끼니가 아니라 상한에 막혀 빠진다.
    FOOD = ["한식", "카페", "양식", "분식"]
    SPOT = ["고궁", "박물관", "공원", "전망대"]

    def spread(self, n=8):
        out = []
        for i in range(n):
            lat, lon = near(i)
            food = bool(i % 2)
            kind = "음식" if food else "문화관광"
            sub = (self.FOOD if food else self.SPOT)[(i // 2) % 4]
            out.append(place(f"{kind}{i}", category=kind,
                             path=f"{kind} > {sub}", lat=lat, lon=lon,
                             use_time="매일 07:00~22:00"))
        return out

    def meal_kinds(self, course):
        return {meal_at(s.arrive) for s in course.steps
                if "음식" in (s.place.content.category_path
                             or s.place.content.category)}

    def test_점심을_고르면_점심때_식당이_들어간다(self):
        c = build_course(self.spread(), datetime(2026, 9, 3, 11, 0), CLEAR,
                         origin=(37.5665, 126.9780), budget_min=300,
                         meals=("lunch",))
        assert "lunch" in self.meal_kinds(c)

    def test_아침과_점심을_같이_고를_수_있다(self):
        c = build_course(self.spread(), datetime(2026, 9, 3, 9, 0), CLEAR,
                         origin=(37.5665, 126.9780), budget_min=420,
                         meals=("breakfast", "lunch"))
        assert {"breakfast", "lunch"} <= self.meal_kinds(c)

    def test_안_고르면_못_채웠다고_하지_않는다(self):
        """끼니는 요청일 때만 강제다. 요청도 안 한 밥을 못 찾았다고
        말하면, 정작 요청한 끼니를 놓쳤을 때의 경고가 묻힌다."""
        only = [place(f"문화{i}", path=f"문화관광 > {self.SPOT[i]}",
                      lat=near(i)[0], lon=near(i)[1]) for i in range(4)]
        c = build_course(only, datetime(2026, 9, 3, 11, 0), CLEAR,
                         origin=(37.5665, 126.9780), budget_min=300)
        assert not any("찾지 못했습니다" in n for n in c.notes)

    def test_못_채우면_조용히_넘어가지_않는다(self):
        """식당이 아예 없는 판. 빠뜨린 것을 말해야 왜 없는지 안다."""
        only = [place(f"문화{i}", path=f"문화관광 > {self.SPOT[i]}",
                      lat=near(i)[0], lon=near(i)[1]) for i in range(4)]
        c = build_course(only, datetime(2026, 9, 3, 11, 0), CLEAR,
                         origin=(37.5665, 126.9780), budget_min=300,
                         meals=("lunch",))
        assert any("점심" in n for n in c.notes)

    def test_모르는_끼니는_무시한다(self):
        """바깥에서 온 값이다. 오타 하나로 500을 내면 안 된다."""
        c = build_course(self.spread(), NOON, CLEAR,
                         origin=(37.5665, 126.9780), budget_min=300,
                         meals=("brunch", "lunch"))
        assert c.steps
        assert not any("brunch" in n for n in c.notes)

    def test_끼니_시간대는_겹치지_않는다(self):
        spans = sorted(MEALS.values())
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))


class TestSerialization:
    def test_출력에_시각과_이동이_담긴다(self):
        places = [place(f"장소{i}", lat=near(i)[0], lon=near(i)[1]) for i in range(3)]
        d = build_course(places, NOON, CLEAR, origin=(37.5665, 126.9780),
                         budget_min=300).to_dict()
        assert d["start"] and d["end"]
        assert d["total_min"] == d["travel_min"] + d["dwell_min"]
        for s in d["steps"]:
            assert s["arrive"] and s["depart"]


class TestUselessStops:
    def test_체류시간이_0인_곳은_넣지_않는다(self):
        """숙박은 반나절 코스의 목적지가 아니다. 그대로 두면
        '16:16 도착 16:16 출발'짜리 항목이 붙는다."""
        hotel = place("아무개 호텔", category="숙박")
        c = build_course([hotel], NOON, CLEAR, origin=(37.5665, 126.9780),
                         budget_min=300)
        assert c.steps == []

    def test_주변_목록에서는_지우지_않는다(self):
        """일정에 안 넣는 것과 목록에서 지우는 것은 다르다."""
        from weatherfit.quality import is_touristic
        assert is_touristic(place("아무개 호텔", category="숙박")) is True


class TestExperiencePreserving:
    """날씨 때문에 장소가 바뀌는 건 어쩔 수 없지만 경험까지 바뀔 이유는 없다."""

    def test_같은_경험은_비슷하게_나온다(self):
        from weatherfit.course import experience_similarity
        from weatherfit.trend import tag_place
        a = place("고궁A", category="역사관광", path="역사관광 > 고궁")
        b = place("고궁B", category="역사관광", path="역사관광 > 고궁")
        c = place("쇼핑몰", category="쇼핑", path="쇼핑 > 쇼핑몰")
        a.trend, b.trend, c.trend = (tag_place(a), tag_place(b), tag_place(c))
        assert experience_similarity(a, b) > experience_similarity(a, c)

    def test_트렌드를_모르면_0(self):
        from weatherfit.course import experience_similarity
        assert experience_similarity(place("A"), place("B")) == 0.0

    def test_한_곳이_여러_자리를_덮지_못한다(self):
        """'새 일정 아무 곳과의 최대'로 재면 무엇을 해도 90%가 넘는다.
        한 곳은 한 자리만 맡는 일대일이어야 한다."""
        from weatherfit.course import Course, Step, experience_kept
        from weatherfit.trend import tag_place
        ps = [place(f"고궁{i}", category="역사관광", path="역사관광 > 고궁",
                    cid=f"KO{i}") for i in range(3)]
        for p in ps:
            p.trend = tag_place(p)
        before = Course(start=NOON)
        before.steps = [Step(place=p, role="spot", reason="") for p in ps]
        after = Course(start=NOON)
        after.steps = [Step(place=ps[0], role="spot", reason="")]
        assert experience_kept(before, after) < 0.5

    def test_다녀온_일정은_건드리지_않는다(self):
        from datetime import timedelta
        from weatherfit.course import replan
        from weatherfit.trend import tag_place
        places = [place(f"장소{i}", lat=near(i)[0], lon=near(i)[1], cid=f"KO{i}",
                        path="문화관광 > 전시시설") for i in range(5)]
        for p in places:
            p.trend = tag_place(p)
        c = build_course(places, NOON, CLEAR, origin=(37.5665, 126.9780),
                         budget_min=300)
        assert c.steps
        done = c.steps[0].depart
        out = replan(c, places, NOON, RAIN, origin=(37.5665, 126.9780),
                     budget_min=300, keep_before=done)
        assert out.steps and out.steps[0].place.cid == c.steps[0].place.cid

    def test_무엇을_무엇으로_바꿨는지_적는다(self):
        from weatherfit.course import replan
        from weatherfit.trend import tag_place
        places = [place(f"장소{i}", lat=near(i)[0], lon=near(i)[1], cid=f"KO{i}",
                        category="문화관광",
                        path="문화관광 > 도시공원" if i < 2 else "문화관광 > 전시시설")
                  for i in range(6)]
        for p in places:
            p.trend = tag_place(p)
        c = build_course(places, NOON, CLEAR, origin=(37.5665, 126.9780),
                         budget_min=300)
        out = replan(c, places, NOON, RAIN, origin=(37.5665, 126.9780),
                     budget_min=300)
        assert any("다시 짰습니다" in n for n in out.notes)


class TestAnchorFallback:
    def test_1순위가_막혀도_포기하지_않는다(self):
        """19:50에 시청에서 456곳을 갈 수 있는데도 빈 일정이 나왔다.
        1순위 덕수궁이 20시에 닫힌다는 이유 하나로 나머지를 버린 것이다.
        문 닫은 곳 하나가 도시 전체를 닫지는 않는다."""
        closed = place("곧 닫는 명소", use_time="매일 09:00~20:00",
                       desc="설명" * 300, tags=list("abcdefgh"),
                       lat=near(0)[0], lon=near(0)[1], cid="KO0")
        open_ = place("열려 있는 곳", use_time="매일 09:00~23:00",
                      lat=near(1)[0], lon=near(1)[1], cid="KO1")
        c = build_course([closed, open_], datetime(2026, 9, 3, 19, 50), CLEAR,
                         origin=(37.5665, 126.9780), budget_min=240)
        assert c.steps
        assert c.steps[0].place.cid == "KO1"

    def test_정말_다_닫혔으면_이유를_말한다(self):
        allclosed = [place(f"닫은 곳{i}", use_time="매일 09:00~18:00",
                           lat=near(i)[0], lon=near(i)[1], cid=f"KO{i}")
                     for i in range(5)]
        c = build_course(allclosed, datetime(2026, 9, 3, 22, 0), CLEAR,
                         origin=(37.5665, 126.9780), budget_min=240)
        assert c.steps == []
        assert c.notes
