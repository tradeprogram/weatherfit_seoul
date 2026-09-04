"""트렌드 엔진 테스트.

지켜야 하는 성질은 셋이다.

  엔진은 숫자가 무엇인지 모른다   — 알면 소스를 갈아 끼울 수 없다
  계절성은 반드시 지워진다        — 안 지우면 '여름에 한강이 뜬다'가 트렌드다
  모르면 모른다고 한다           — 0으로 채우면 '안 뜬다'는 거짓말이 된다

여기서 틀리면 '뜨는 곳'이 사실은 늘 1등이던 곳이 된다. 순위표에는
정보가 없다 — 경복궁은 언제나 1위라서 아무것도 말해 주지 않는다.
"""
import pytest

from weatherfit.momentum import (LABEL, PERIOD, Source, axes, classify,
                                 divergence, score)


def flat(n=24, v=1000.0):
    return [v] * n


def rising(n=24, base=1000.0, per=0.04):
    return [base * (1 + per) ** i for i in range(n)]


SEASON = [1000, 900, 1200, 1600, 1800, 2200,
          2600, 2500, 1900, 1500, 1100, 950]


class TestEngineIsBlind:
    """엔진이 숫자의 뜻을 알면 소스를 꽂았다 뺐다 할 수 없다."""

    def test_같은_수는_무엇을_재든_같은_답을_낸다(self):
        """조회수 1,000과 소비 비율 1,000은 엔진에게 같은 수다.
        해석은 소스가 하고, 엔진은 움직임만 잰다."""
        assert axes(rising()) == axes(rising())

    def test_단위가_달라도_모멘텀은_비교된다(self):
        """모멘텀은 비율이라 단위가 없다. 그래서 조회수와 소비액을
        가로질러 비교할 수 있다 — 이게 소스를 늘릴 수 있는 이유다."""
        views = axes(rising(24, 50000, 0.03))
        spend = axes(rising(24, 12.5, 0.03))
        assert views["yoy"] == pytest.approx(spend["yoy"], abs=1e-6)

    def test_수준은_단위에_매이므로_소스_안에서만_정규화한다(self):
        """조회수 5만과 비율 44%를 한 자로 재면 아무 뜻도 없다."""
        a = axes(flat(24, 50000.0))
        big = score(a, max_log=math_log(a["level"]))
        small = score(a, max_log=math_log(a["level"]) * 2)
        assert big["level"] > small["level"]
        assert big["momentum"] == small["momentum"]   # 단위 없는 축은 그대로


def math_log(x):
    import math
    return math.log1p(x)


class TestSeasonality:
    def test_계절성만_있으면_트렌드로_잡지_않는다(self):
        a = axes(SEASON * 2)
        assert abs(a["yoy"]) < 0.01
        assert abs(a["surge"]) < 0.5

    def test_주기와_다른_간격으로_비교하지_않는다(self):
        """20개월이 있다고 10개월 전과 비교하면 4월을 그해 6월과 재는
        셈이다. 계절성을 없애려던 방법이 계절성을 재게 된다. 그래서
        주기의 두 배가 안 되면 계산하지 않는다."""
        assert axes(flat(2 * PERIOD - 1)) is None
        assert axes(flat(2 * PERIOD)) is not None

    def test_주기가_다른_자료도_같은_엔진으로_잰다(self):
        """분기 자료는 주기가 4다. 엔진은 그대로고 숫자만 바뀐다."""
        assert axes(flat(8), period=4) is not None
        assert axes(flat(7), period=4) is None


class TestRefusal:
    def test_자료가_짧으면_None(self):
        assert axes(flat(12)) is None

    def test_잡음_문턱_아래는_None(self):
        """월 20회짜리 문서의 ±50%는 잡음이지 트렌드가 아니다."""
        assert axes(flat(24, 5.0), min_total=240) is None
        assert axes(flat(24, 5.0)) is not None      # 문턱은 소스가 정한다

    def test_직전_구간이_0이면_None(self):
        """0으로 나눌 수 없다. 무한대 성장이라고 적으면 안 된다."""
        assert axes([0.0] * 12 + [100.0] * 12) is None


class TestClassify:
    def test_최근에만_튀면_급등으로_잡는다(self):
        """뉴스 스파이크. 성수동이 2025년 2월에 7배로 튀었다 내려앉았다."""
        v = flat(21) + [7000.0, 6000.0, 5000.0]
        assert axes(v)["surge"] > 1.2
        assert classify(axes(v)) == "spike"

    def test_꾸준히_오르면_뜨는_중(self):
        """매기간 같은 비율로 늘면 비율의 분산이 0이라 급등도 0이다.
        가장 뚜렷한 상승인데, 급등을 함께 요구하면 걸러진다."""
        a = axes(rising(24, 1000, 0.05))
        assert a["surge"] == pytest.approx(0.0, abs=0.01)
        assert classify(a) == "rising"

    def test_올랐지만_최근에_꺾이면_뜨는_중이_아니다(self):
        """청와대는 전년비 +419%로 1위인데 급등이 -1.15다. 오른 건
        작년 일이고 최근 석 달은 제 평균보다 낮다."""
        a = axes([100.0] * 6 + [900.0] * 12 + [500.0] * 6)
        assert a["yoy"] > 0.15 and a["surge"] < -0.5
        assert classify(a) == "peaked"

    def test_평평하면_꾸준함(self):
        assert classify(axes(flat())) == "steady"

    def test_줄면_식는_중(self):
        assert classify(axes(list(reversed(rising())))) == "fading"

    def test_모르면_모른다고_한다(self):
        assert classify(None) == "unknown"

    def test_모든_라벨에_사람_말이_있다(self):
        for v in (flat(), rising(), list(reversed(rising())), None):
            assert classify(axes(v) if v else None) in LABEL


class TestScore:
    def test_세_축을_합치지_않는다(self):
        """'수준'과 '변화'는 뜻이 다른 값이다. 하나로 뭉치면 늘 1등이던
        곳이 다시 1등이 된다."""
        assert set(score(axes(rising()), 12.0)) == {"level", "momentum", "surge"}

    def test_모두_0에서_1_사이(self):
        for v in (flat(), rising(), list(reversed(rising())), flat(24, 1e9)):
            s = score(axes(v), 12.0)
            assert all(0.0 <= x <= 1.0 for x in s.values()), s

    def test_늘어난_쪽이_모멘텀이_높다(self):
        assert (score(axes(rising()), 12.0)["momentum"] >
                score(axes(list(reversed(rising()))), 12.0)["momentum"])


class TestDivergence:
    def test_두_소스가_같은_말을_하면_볼_것이_없다(self):
        a = b = axes(rising())
        assert divergence(a, b)["notable"] is False

    def test_관심은_주는데_방문이_늘면_잡아낸다(self):
        """북촌한옥마을이다. 조회수 -40.8%인데 실측 방문은 늘었다.
        조회수만 보면 추천에서 빼게 되는데, 사실은 그 반대다."""
        attention = {"yoy": -0.408}
        visits = {"yoy": 0.105}
        d = divergence(attention, visits)
        assert d["notable"] and d["lead"] == "b"

    def test_어느_쪽이_앞서는지_밝힌다(self):
        assert divergence({"yoy": 0.9}, {"yoy": 0.1})["lead"] == "a"


class TestSourceRegistry:
    def test_소스를_붙이는_데_엔진을_건드리지_않는다(self):
        """새 데이터를 붙이는 비용이 함수 하나여야 트렌드가 바뀌어도
        모듈이 산다."""
        from weatherfit import momentum as m

        before = dict(m.SOURCES)
        try:
            @m.source(name="_시험", kind="visits", unit="명", entity="place")
            def _f(verbose=True, **kw):
                return {"KO1": {"label": "어딘가", "values": flat()}}

            assert m.SOURCES["_시험"].kind == "visits"
            assert m.SOURCES["_시험"].period == PERIOD
        finally:
            m.SOURCES.clear()
            m.SOURCES.update(before)

    def test_이미_꽂힌_소스가_스스로를_설명한다(self):
        from weatherfit.momentum import SOURCES

        for s in SOURCES.values():
            assert s.kind in {"attention", "visits", "spend", "search"}
            assert s.entity in {"place", "category"}
            assert s.unit and s.note      # 무엇을 재는지 말할 수 있어야 한다


class TestEmptyIsExplained:
    """0건이 나왔을 때 자료가 짧아서인지 코드가 틀려서인지 달라야 한다."""

    def test_계열이_짧으면_얼마나_모자란지_말한다(self):
        from weatherfit.momentum import _why_empty

        got = _why_empty({"source": "datalab_spend", "rows": 18,
                          "with_series": 18, "longest": 12, "needs": 24})
        assert "12기간" in got and "24기간" in got

    def test_계열을_못_받았으면_그렇게_말한다(self):
        from weatherfit.momentum import _why_empty

        assert "연결" in _why_empty({"source": "x", "rows": 5,
                                    "with_series": 0, "longest": 0,
                                    "needs": 24})


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


class TestLookup:
    def test_계산_안_된_것은_None(self):
        from weatherfit import momentum as m

        m._cache["시험"] = {"meta": {}, "series": {"KOX": {"trend": "unknown"}}}
        assert m.of("KOX", "시험") is None
        assert m.of("없는cid", "시험") is None
        m._cache.pop("시험")

    def test_계산된_것은_돌려준다(self):
        from weatherfit import momentum as m

        m._cache["시험"] = {"meta": {}, "series": {"KOY": {
            "trend": "rising", "axes": {"yoy": 0.3},
            "score": {"level": .5, "momentum": .8, "surge": .6}}}}
        assert m.of("KOY", "시험")["trend"] == "rising"
        m._cache.pop("시험")


class TestDataCanChange:
    """핵심 주장: **데이터가 변해도 모듈은 트렌드를 뽑아낸다.**

    주장으로 두면 언젠가 거짓이 된다. 누군가 엔진 안에 '조회수'를 가정한
    한 줄을 넣는 순간 조용히 깨지고, 그때는 이미 소스를 못 갈아 끼운다.
    그래서 성격이 전혀 다른 자료를 같은 엔진에 통과시켜 둔다.
    """

    SHAPES = {
        "조회수": 52000.0,      # 정수, 수만 단위
        "소비비율": 7.23,        # 퍼센트, 한 자리
        "평점": 4.31,           # 1~5로 갇힌 값
        "방문객": 3202.0,        # 수천 단위
        "소비액": 1.7e8,        # 억 단위
    }

    def test_단위가_무엇이든_같은_모양이면_같은_판정(self):
        got = {k: classify(axes(rising(24, base, 0.04)))
               for k, base in self.SHAPES.items()}
        assert set(got.values()) == {"rising"}, got

    def test_단위가_무엇이든_같은_모양이면_같은_전년비(self):
        ys = [axes(rising(24, base, 0.04))["yoy"]
              for base in self.SHAPES.values()]
        assert max(ys) - min(ys) < 1e-6

    def test_하락도_급등도_단위를_타지_않는다(self):
        news = flat(21, 1.0) + [7.0, 6.0, 5.0]     # 퍼센트 자료의 뉴스 스파이크
        assert classify(axes(news)) == "spike"
        assert classify(axes(list(reversed(rising(24, 4.5, 0.03))))) == "fading"

    def test_새_소스를_붙여도_엔진_코드는_그대로다(self, tmp_path, monkeypatch):
        """수집부터 저장까지 통째로 돌린다. 새 데이터를 붙이는 데 필요한
        것이 함수 하나뿐임을 실제로 확인한다 — 엔진은 손대지 않는다."""
        from weatherfit import momentum as m

        before = dict(m.SOURCES)
        monkeypatch.setattr(m, "TREND_DIR", tmp_path)
        try:
            @m.source(name="_가상방문", kind="visits", unit="일평균 방문객",
                      entity="place", note="아직 존재하지 않는 자료")
            def _f(verbose=True, **kw):
                return {"KO뜸": {"label": "뜨는곳", "values": rising()},
                        "KO식": {"label": "식는곳",
                                 "values": list(reversed(rising()))},
                        "KO짧": {"label": "자료부족", "values": flat(6)}}

            m.build("_가상방문", verbose=False)
            m.reset()
            saved = m.table("_가상방문")

            assert saved["meta"]["kind"] == "visits"
            assert saved["meta"]["computed"] == 2
            assert saved["series"]["KO뜸"]["trend"] == "rising"
            assert saved["series"]["KO식"]["trend"] == "fading"
            assert saved["series"]["KO짧"]["trend"] == "unknown"
            assert m.of("KO짧", "_가상방문") is None
        finally:
            m.SOURCES.clear()
            m.SOURCES.update(before)
            m.reset()

    def test_주기가_다른_소스도_같은_엔진이_받는다(self, tmp_path, monkeypatch):
        """분기 자료는 계절 주기가 4다. 엔진은 주기를 인자로 받을 뿐,
        자료가 월간인지 분기인지 알지 못한다."""
        from weatherfit import momentum as m

        before = dict(m.SOURCES)
        monkeypatch.setattr(m, "TREND_DIR", tmp_path)
        try:
            @m.source(name="_분기", kind="spend", unit="분기 소비액",
                      entity="category", period=4, note="분기 자료")
            def _f(verbose=True, **kw):
                return {"C1": {"label": "어떤업종", "values": rising(8, 100, 0.1)}}

            m.build("_분기", verbose=False)
            m.reset()
            assert m.table("_분기")["series"]["C1"]["trend"] == "rising"
        finally:
            m.SOURCES.clear()
            m.SOURCES.update(before)
            m.reset()


class TestMarketEffect:
    """플랫폼이 통째로 움직인 몫을 장소의 사정으로 읽으면 안 된다."""

    def test_전체가_함께_빠지면_개별_하락으로_읽지_않는다(self):
        """한국어 위키백과가 그랬다 — 106곳 중 94곳 하락, 중앙값 -41.0%.
        서울의 명소가 일제히 식었을 리 없고, 한국어권에서 위키백과를
        찾는 일 자체가 준 것이다."""
        from weatherfit.momentum import excess, market_adjust

        crowd = [{"yoy": -0.41 + i * 0.001} for i in range(20)]
        m = market_adjust(crowd)
        assert m == pytest.approx(-0.4, abs=0.02)
        for a in crowd:
            a["rel"] = a["yoy"] - m
        assert all(classify({**a, "surge": 0.0}) == "steady" for a in crowd)

    def test_시장보다_잘한_곳은_남는다(self):
        from weatherfit.momentum import market_adjust

        crowd = [{"yoy": -0.41} for _ in range(20)] + [{"yoy": 0.10}]
        m = market_adjust(crowd)
        star = {"yoy": 0.10, "rel": 0.10 - m, "surge": 0.0}
        assert classify(star) == "rising"

    def test_평균이_아니라_중앙값을_쓴다(self):
        """청와대 하나가 +419%다. 평균을 쓰면 그 한 곳이 시장 전체를
        끌어올려 나머지가 전부 '식는 중'이 된다."""
        from weatherfit.momentum import market_adjust

        crowd = [{"yoy": 0.0} for _ in range(20)] + [{"yoy": 4.19}]
        assert abs(market_adjust(crowd)) < 0.01

    def test_개체가_적으면_시장을_빼지_않는다(self):
        """하나뿐이면 중앙값이 곧 그 자신이라 무엇이 오르든 0이 된다."""
        from weatherfit.momentum import MARKET_MIN, market_adjust

        assert market_adjust([{"yoy": 0.8}]) == 0.0
        assert market_adjust([{"yoy": 0.8}] * (MARKET_MIN - 1)) == 0.0
        assert market_adjust([{"yoy": 0.8}] * MARKET_MIN) == 0.8

    def test_조정값이_없으면_원값을_쓴다(self):
        from weatherfit.momentum import excess

        assert excess({"yoy": 0.3}) == 0.3
        assert excess({"yoy": 0.3, "rel": -0.1}) == -0.1
        assert excess(None) == 0.0


class TestSuspect:
    """자료가 바뀐 자국을 트렌드로 읽으면 안 된다."""

    def test_한_해에_몇_배씩_뛰면_자료를_의심한다(self):
        """남산서울타워 한국어 문서가 전년비 +2,625%로 1위였다. 27배다.
        서울에서 두 번째로 유명한 탑이 한 해 만에 27배로 알려졌을 리 없고,
        문서 제목이 바뀌며 조회수가 옛 제목과 갈린 것이다."""
        from weatherfit.momentum import suspect

        assert suspect({"yoy": 26.25})
        assert classify({"yoy": 26.25, "surge": 0.0}) == "suspect"

    def test_실제_뉴스로_다섯_배쯤_뛴_것은_남긴다(self):
        """청와대 +419%(5.2배)는 실제로 일어난 일이다. 두 소스 218곳에서
        27.3배가 홀로 떨어져 있고 그다음이 5.2배라, 사이에서 끊는다."""
        from weatherfit.momentum import suspect

        assert not suspect({"yoy": 4.191})

    def test_의심스러운_값은_시장_계산에서도_뺀다(self):
        """분모가 무너진 값 하나가 시장 중앙값까지 흔들면 안 된다."""
        from weatherfit.momentum import market_adjust

        clean = [{"yoy": 0.0} for _ in range(10)]
        assert market_adjust(clean + [{"yoy": 99.0}]) == 0.0

    def test_소스가_아니라_엔진이_잡는다(self):
        """위키백과만의 문제가 아니다. 어떤 소스든 집계 방식이 바뀌면
        분모가 무너지고 비율이 폭발한다."""
        from weatherfit.momentum import suspect

        for unit_base in (52000.0, 7.23, 4.31, 1.7e8):
            v = flat(12, unit_base * 0.01) + flat(12, unit_base)
            assert suspect(axes(v))


class TestRankingValue:
    """순위에 쓰는 값과 화면에 적는 값은 다르다."""

    def test_작은_기준선의_큰_비율은_깎인다(self):
        """떡박물관은 한 해 800회에 +98%다. 늘어난 절대량은 400회다.
        같은 표의 광화문광장은 42,975회에 +186%다. 둘을 같은 자로 재면
        잡음이 신호를 이긴다."""
        from weatherfit.momentum import shrink

        typical = 4312.0
        small = shrink(0.982, 800.0, typical)
        big = shrink(1.859, 42975.0, typical)
        assert small < 0.20              # +98% → +15%쯤
        assert big > 1.60                # +186% → +169%쯤

    def test_중앙값짜리는_절반으로_당겨진다(self):
        from weatherfit.momentum import shrink

        assert shrink(1.0, 4312.0, 4312.0) == pytest.approx(0.5)

    def test_상수를_손으로_고르지_않는다(self):
        """기준은 그 소스의 중앙값이다. 조회수든 소비액이든 같은 코드가
        돈다 — 이게 소스를 갈아 끼울 수 있는 조건이다."""
        from weatherfit.momentum import shrink

        views = shrink(0.5, 40000.0, 4312.0)
        spend = shrink(0.5, 40000.0 * 1e-4, 4312.0 * 1e-4)
        assert views == pytest.approx(spend)

    def test_하락은_절반만_반영한다(self):
        """관심이 늘었다는 건 새로 알아보는 사람이 늘었다는 뜻 하나지만,
        줄었다는 건 시들었거나 너무 유명해졌거나 둘 중 하나다. 북촌한옥마을은
        조회수 -40.8%인데 실측 외국인 방문은 +10.5%였다."""
        from weatherfit.momentum import _damped

        assert _damped({"adj": 0.4}) == pytest.approx(0.4)
        assert _damped({"adj": -0.4}) == pytest.approx(-0.2)

    def test_깎아도_판정_라벨은_사실대로_남는다(self):
        """순위만 덜 움직이고, 화면에는 '식는 중'이라고 그대로 적는다."""
        a = {"yoy": -0.44, "rel": -0.44, "adj": -0.40, "surge": 0.0,
             "level": 39059.0}
        assert classify(a) == "fading"
        assert score(a, 12.0)["momentum"] > 0.25   # 0.06이 아니다


class TestRankingWiring:
    def test_가중치의_합이_1이다(self):
        from weatherfit import quality as q

        total = (q.W_NEAR + q.W_QUALITY + q.W_POPULAR + q.W_MOMENTUM
                 + q.W_TASTE + q.W_STYLE)
        assert total == pytest.approx(1.0)

    def test_자료가_없으면_중립이다(self):
        """위키 문서가 없는 곳이 대부분이다. 0을 주면 '안 뜬다'고 말하는
        셈이고, 그건 모르는 것을 아는 척하는 일이다."""
        from weatherfit import quality as q

        class P:
            cid = "없는cid"

        value, note = q._momentum_of(P())
        assert value == q.MOMENTUM_UNKNOWN == 0.5
        assert "없음" in note

    def test_인기와_모멘텀은_따로_센다(self):
        """하나로 합치면 경복궁이 늘 이긴다 — 크기가 압도적이라 변화가
        묻힌다. 그런데 크기는 다들 아는 사실이라 정보가 없다."""
        from weatherfit import quality as q

        assert q.W_POPULAR > 0 and q.W_MOMENTUM > 0


class TestBadge:
    """카드에 붙는 한 줄. 화면에 나가는 값이라 조용히 틀리면 안 된다."""

    def setup_method(self):
        from weatherfit import momentum as m
        m._cache["시험"] = {"meta": {}, "series": {
            "오름": {"label": "광화문광장", "trend": "rising",
                    "axes": {"yoy": 1.86, "rel": 1.859, "level": 42975.0},
                    "score": {"momentum": 1.0}},
            "내림": {"label": "북촌한옥마을", "trend": "fading",
                    "axes": {"yoy": -0.44, "rel": -0.441, "level": 39059.0},
                    "score": {"momentum": 0.28}},
            "평": {"label": "경복궁", "trend": "steady",
                  "axes": {"yoy": -0.01, "rel": -0.014, "level": 208507.0},
                  "score": {"momentum": 0.49}},
            "흔들": {"label": "남산서울타워", "trend": "suspect",
                    "axes": {"yoy": 26.25, "level": 7876.0},
                    "score": {"momentum": 1.0}},
        }}

    def teardown_method(self):
        from weatherfit import momentum as m
        m._cache.pop("시험", None)

    def test_오르는_곳에_붙는다(self):
        from weatherfit.momentum import badge

        b = badge("오름", "시험")
        assert b["kind"] == "rising" and b["label"] == "뜨는 중"
        assert b["yoy"] == pytest.approx(1.859)

    def test_식는_곳도_숨기지_않는다(self):
        """추천에서 뺀 것이 아니라 순위에 덜 반영했을 뿐이다. 화면에
        안 적으면 '뜨는 곳만 있다'는 인상을 준다."""
        from weatherfit.momentum import badge

        assert badge("내림", "시험")["label"] == "식는 중"

    def test_꾸준한_곳에는_안_붙인다(self):
        """경복궁이 '꾸준함'인 건 맞지만 알려 줄 것이 없다."""
        from weatherfit.momentum import badge

        assert badge("평", "시험") is None

    def test_자료_사정은_사용자에게_안_보인다(self):
        """'기준 흔들림'은 우리 쪽 문제지 그 장소의 성질이 아니다."""
        from weatherfit.momentum import badge

        assert badge("흔들", "시험") is None

    def test_모르는_곳은_None(self):
        from weatherfit.momentum import badge

        assert badge("없는cid", "시험") is None

    def test_시장을_뺀_값을_내보낸다(self):
        """화면에 원값을 적으면 판정과 숫자가 어긋나 보인다 — 운현궁이
        '-22.8%인데 뜨는 중'으로 나오는 식이다."""
        from weatherfit.momentum import badge

        assert badge("내림", "시험")["yoy"] != -0.44
