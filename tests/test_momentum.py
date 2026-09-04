"""트렌드 모멘텀 테스트.

여기서 틀리면 '뜨는 곳'이 사실은 늘 1등이던 곳이 된다. 순위표에는
정보가 없다 — 경복궁은 언제나 1위라서 아무것도 말해 주지 않는다.
"""
import pytest

from weatherfit.momentum import MIN_MONTHS, MIN_VIEWS, axes, classify, score


def flat(n=24, v=1000):
    return [v] * n


def rising(n=24, base=1000, per=0.04):
    return [round(base * (1 + per) ** i) for i in range(n)]


class TestAxes:
    def test_자료가_짧으면_None(self):
        """0으로 채우면 '안 뜬다'는 거짓말이 된다."""
        assert axes(flat(MIN_MONTHS - 1)) is None

    def test_조회가_너무_적으면_None(self):
        """월 20회짜리 문서의 ±50%는 잡음이지 트렌드가 아니다."""
        assert axes([5] * 24) is None
        assert sum([5] * 12) < MIN_VIEWS

    def test_평평하면_전년비가_0(self):
        a = axes(flat())
        assert a is not None
        assert abs(a["yoy"]) < 0.01

    def test_늘면_전년비가_양수(self):
        assert axes(rising())["yoy"] > 0.3

    def test_줄면_전년비가_음수(self):
        assert axes(list(reversed(rising())))["yoy"] < -0.2


class TestSeasonality:
    def test_계절성만_있으면_트렌드로_잡지_않는다(self):
        """관광은 계절성이 지배적이다. 전월과 비교하면 '여름에 한강이
        뜬다'가 트렌드가 된다. 같은 달끼리 나눠야 계절이 약분된다."""
        season = [1000, 900, 1200, 1600, 1800, 2200,
                  2600, 2500, 1900, 1500, 1100, 950] * 2
        a = axes(season)
        assert abs(a["yoy"]) < 0.01
        assert abs(a["surge"]) < 0.5


class TestSurge:
    def test_최근에만_튀면_급등으로_잡는다(self):
        """뉴스 스파이크. 성수동이 2025년 2월에 7배로 튀었다가 내려앉았다."""
        v = flat(21) + [7000, 6000, 5000]
        assert axes(v)["surge"] > 1.2
        assert classify(axes(v)) == "spike"

    def test_꾸준히_오르면_뜨는_중(self):
        a = axes(rising(24, 1000, 0.05))
        assert classify(a) == "rising"

    def test_평평하면_꾸준함(self):
        assert classify(axes(flat())) == "steady"

    def test_줄면_식는_중(self):
        assert classify(axes(list(reversed(rising())))) == "fading"

    def test_자료가_없으면_모른다고_한다(self):
        assert classify(None) == "unknown"


class TestScore:
    def test_세_축을_합치지_않는다(self):
        """'수준'과 '변화'는 뜻이 다른 값이다. 하나로 뭉치면 늘 1등이던
        곳이 다시 1등이 된다."""
        s = score(axes(rising()), max_log=12.0)
        assert set(s) == {"level", "momentum", "surge"}

    def test_모두_0에서_1_사이(self):
        for v in (flat(), rising(), list(reversed(rising()))):
            s = score(axes(v), max_log=12.0)
            assert all(0.0 <= x <= 1.0 for x in s.values())

    def test_늘어난_쪽이_모멘텀이_높다(self):
        up = score(axes(rising()), 12.0)["momentum"]
        down = score(axes(list(reversed(rising()))), 12.0)["momentum"]
        assert up > down


class TestLookup:
    def test_계산_안_된_곳은_None(self):
        from weatherfit import momentum
        momentum._table = {"meta": {}, "place": {"KOX": {"trend": "unknown"}}}
        assert momentum.of("KOX") is None
        assert momentum.of("없는cid") is None

    def test_계산된_곳은_돌려준다(self):
        from weatherfit import momentum
        momentum._table = {"meta": {}, "place": {
            "KOY": {"trend": "rising", "yoy": 0.3,
                    "score": {"level": .5, "momentum": .8, "surge": .6}}}}
        got = momentum.of("KOY")
        assert got and got["trend"] == "rising"


class TestGeoExtent:
    """넓게 퍼진 지형은 문서 좌표와 입구 좌표가 원래 어긋난다."""

    def test_하천과_산은_넓게_본다(self):
        from weatherfit.momentum import GEO_MAX_KM, GEO_WIDE_KM, _max_km
        for wide in ("청계천", "관악산", "한강", "올림픽공원", "석촌호수", "밤섬"):
            assert _max_km(wide) == GEO_WIDE_KM
        for point in ("호림박물관 (신사분관)", "덕수궁", "리움 미술관"):
            assert _max_km(point) == GEO_MAX_KM

    def test_점_명소는_좁게_봐야_오매칭이_걸린다(self):
        """호림박물관 신사분관은 신림 본관 문서와 11.4km 떨어져 있다.
        넓게 잡으면 다른 건물의 조회수를 이 장소의 인기로 읽는다."""
        from weatherfit.momentum import _max_km
        assert 11.4 > _max_km("호림박물관 (신사분관)")


class TestPeaked:
    def test_올랐지만_최근에_꺾이면_뜨는_중이_아니다(self):
        """청와대는 전년비 +419%로 1위인데 급등이 -1.15다. 오른 건
        작년 일이고 최근 석 달은 제 평균보다 낮다."""
        v = [100] * 6 + [900] * 12 + [500] * 6
        a = axes(v)
        assert a["yoy"] > 0.15 and a["surge"] < -0.5
        assert classify(a) == "peaked"
