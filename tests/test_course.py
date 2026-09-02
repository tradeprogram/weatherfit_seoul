"""일정 구성 테스트.

목록이 아니라 일정이라는 점이 이 모듈의 전부다. 시각이 맞아야 하고,
남은 시간을 넘지 않아야 하고, **도착할 시각에** 열려 있어야 한다.
"""
from datetime import datetime

import pytest

from weatherfit.course import (ASSUMED_OPEN, DWELL, MIN_USEFUL_DWELL,
                               build_course, dwell_minutes, _is_meal_time)
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
