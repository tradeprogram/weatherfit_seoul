"""경로 소요시간 테스트.

핵심은 두 가지다. **추정과 실측을 섞지 않는 것** — 화면에 "도보 6분"이라고
적을 때 그게 잰 값인지 가정한 값인지 사용자가 알아야 한다. 그리고 **확정된
구간만 실측하는 것** — 후보를 고르는 동안 시도마다 API를 부르면 일정 하나에
수십 번을 묻게 된다.
"""
from datetime import datetime

import pytest

from weatherfit.course import (Course, Step, _measure_legs, build_course,
                               dwell_minutes)
from weatherfit.routing import (WALKABLE_M, Leg, Routing, estimate_walk,
                                haversine_m)
from weatherfit.validate import Weather

from .test_course import NOON, near, place

CLEAR = Weather(temp_c=22.0)
CITY_HALL = (37.5665, 126.9780)


class TestHaversine:
    def test_같은_점은_0(self):
        assert haversine_m(*CITY_HALL, *CITY_HALL) == 0

    def test_시청에서_강남역은_약_7km(self):
        d = haversine_m(37.5665, 126.9780, 37.4979, 127.0276)
        assert 7000 < d < 9000


class TestEstimate:
    def test_직선보다_길게_잡는다(self):
        """실제 보행 경로는 직선보다 길다. 짧게 잡으면 늦는다."""
        o, d = (37.5665, 126.9780), (37.5700, 126.9820)
        leg = estimate_walk(o, d)
        assert leg.distance_m > haversine_m(*o, *d)

    def test_추정이라고_밝힌다(self):
        leg = estimate_walk((37.5665, 126.9780), (37.5700, 126.9820))
        assert leg.exact is False and leg.provider == "estimate"

    def test_최소_1분(self):
        assert estimate_walk(CITY_HALL, CITY_HALL).minutes >= 1


class TestOffline:
    """offline이면 네트워크를 타지 않는다."""

    def test_추정만_낸다(self):
        r = Routing(offline=True)
        got = r.best(CITY_HALL, (37.4979, 127.0276))
        assert got["walk"]["exact"] is False
        assert got["transit"]["exact"] is False

    def test_무엇을_쓰는지_밝힌다(self):
        assert Routing(offline=True).providers["walk"] == "estimate"

    def test_키가_없으면_도보는_osrm(self, monkeypatch):
        monkeypatch.delenv("TMAP_APP_KEY", raising=False)
        assert Routing().providers["walk"] == "osrm"


class TestBest:
    def test_가까우면_대중교통을_묻지_않는다(self):
        r = Routing(offline=True)
        got = r.best(CITY_HALL, (37.5675, 126.9790))
        assert got["recommended"] == "walk"
        assert got["transit"] is None
        assert got["walk"]["distance_m"] <= WALKABLE_M

    def test_멀면_둘_다_주고_빠른_쪽을_권한다(self):
        got = Routing(offline=True).best(CITY_HALL, (37.4979, 127.0276))
        assert got["transit"] is not None
        rec = got["recommended"]
        assert got[rec]["minutes"] <= got["walk" if rec == "transit"
                                          else "transit"]["minutes"]

    def test_후보_탐색에는_묻지_않는다(self, monkeypatch):
        """measure=False면 어떤 HTTP 호출도 나가면 안 된다."""
        import weatherfit.routing as rt

        def boom(*a, **k):
            raise AssertionError("후보를 고르는 중에 경로 API를 불렀다")

        monkeypatch.setattr(rt.requests, "get", boom)
        monkeypatch.setattr(rt.requests, "post", boom)
        monkeypatch.setenv("TMAP_APP_KEY", "있는-척")
        Routing().best(CITY_HALL, (37.4979, 127.0276), measure=False)


class FakeRouter:
    """실측을 흉내 내는 라우터. 추정보다 오래 걸린다고 답한다."""

    def __init__(self, minutes: int):
        self.minutes = minutes
        self.calls = 0

    def best(self, o, d, measure=True):
        return {"recommended": "walk", "transit": None,
                "walk": Leg(mode="walk", minutes=self.minutes, distance_m=500,
                            provider="fake", exact=True).to_dict()}

    def measure_many(self, pairs):
        self.calls += 1                      # 한 번에 몰아서 물어야 한다
        return [self.best(o, d) for o, d in pairs]


class TestMeasureLegs:
    def build(self, n=3):
        places = [place(f"장소{i}", lat=near(i)[0], lon=near(i)[1], cid=f"KO{i}")
                  for i in range(n)]
        return build_course(places, NOON, CLEAR, origin=CITY_HALL,
                            budget_min=300)

    def test_구간을_한_번에_묻는다(self):
        c = self.build()
        rt = FakeRouter(5)
        _measure_legs(c, rt, CITY_HALL, NOON.replace(hour=17))
        assert rt.calls == 1                 # 구간마다 따로 물으면 초가 쌓인다

    def test_실측값으로_시각을_다시_맞춘다(self):
        c = self.build()
        _measure_legs(c, FakeRouter(9), CITY_HALL, NOON.replace(hour=17))
        assert c.steps[0].arrive == NOON.replace(minute=9)
        for a, b in zip(c.steps, c.steps[1:]):
            assert b.arrive == a.depart + \
                __import__("datetime").timedelta(minutes=9)

    def test_실측이_예산을_넘기면_뒤를_잘라내고_알린다(self):
        """추정으로는 들어갔는데 실제로는 안 들어가는 구간이 있다.
        그대로 두면 '도착 시각에 열려 있는가'라는 전제가 거짓이 된다."""
        c = self.build()
        before = len(c.steps)
        _measure_legs(c, FakeRouter(90), CITY_HALL,
                      NOON.replace(hour=14))      # 2시간만 남았다
        assert len(c.steps) < before
        assert any("실제 이동시간" in n for n in c.notes)

    def test_빈_일정에는_묻지_않는다(self):
        rt = FakeRouter(5)
        _measure_legs(Course(start=NOON), rt, CITY_HALL, NOON)
        assert rt.calls == 0

    def test_실측_표시가_구간에_남는다(self):
        c = self.build()
        _measure_legs(c, FakeRouter(5), CITY_HALL, NOON.replace(hour=17))
        assert all(s.travel["walk"]["exact"] is True for s in c.steps)


class TestCache:
    def test_실측은_기억한다(self):
        r = Routing(offline=True)
        leg = estimate_walk(CITY_HALL, (37.5700, 126.9820))
        key = r._key("walk", CITY_HALL, (37.5700, 126.9820))
        r._remember(key, leg)
        assert r.walk(CITY_HALL, (37.5700, 126.9820)) is leg

    def test_추정은_기억하지_않는다(self):
        """추정은 실측이 오면 대체될 임시값이다. 캐시에 눌러앉으면
        나중에 실측을 부르려 해도 추정이 계속 돌아온다."""
        r = Routing(offline=True)
        r.walk(CITY_HALL, (37.5700, 126.9820))
        assert r._cache == {}

    def test_상한을_넘으면_오래된_것부터_버린다(self):
        from weatherfit.routing import CACHE_MAX
        r = Routing(offline=True)
        leg = estimate_walk(CITY_HALL, CITY_HALL)
        for i in range(CACHE_MAX + 10):
            r._remember(("walk", i, 0, 0, 0), leg)
        assert len(r._cache) < CACHE_MAX
