"""트렌드 태깅 테스트.

VITALITY 8축을 장소에 매기려다 발견한 것들을 그대로 박아 둔다.
여기가 틀리면 '로컬 감성 코스'를 부탁했는데 전통공예명품전이 나온다.
"""
import pytest

from weatherfit.index import Place
from weatherfit.models import Content
from weatherfit.normalize import parse_hours, tag_environment
from weatherfit.trend import (PLACE_AXES, SERVICE_AXES, UNMEASURABLE,
                              tag_place, service_axes)


def place(title="아무개", category="문화관광", path="", tags=None,
          access=None, subway="", dong="", cid="KO1", desc="",
          start="", end=""):
    c = Content(cid=cid, title=title, category=category,
                category_path=path or category, tags=tags or [],
                accessibility=access or [], subway_raw=subway,
                description=desc, schedule_start=start, schedule_end=end,
                lat=37.5665, lon=126.9780)
    env, why = tag_environment(c.category, c.title, c.description, c.tags,
                               c.category_path)
    p = Place(content=c, hours=parse_hours(""), environment=env, env_reason=why)
    p.dong = dong
    return p


class TestAxes:
    def test_장소에_매길_수_있는_축만_남긴다(self):
        """'초개인화'와 '나만의 서울'은 장소의 성질이 아니다.
        경복궁이 얼마나 개인화됐냐고 물으면 답이 없다."""
        assert "tailored_smart" not in PLACE_AXES
        assert "your_seoul" not in PLACE_AXES
        assert set(SERVICE_AXES) == {"tailored_smart", "your_seoul"}

    def test_못_재는_축은_아예_빼_둔다(self):
        """'열린 선택지'는 가격대·수용인원이 있어야 재는데 API에 없다.
        행정동 다양성으로 대신했더니 '도심인가'를 재고 있었다."""
        assert "inclusive_choice" in UNMEASURABLE
        assert "inclusive_choice" not in PLACE_AXES

    def test_모든_축에_근거가_붙는다(self):
        v = tag_place(place("아무개", path="문화관광 > 전시시설"))
        for a in PLACE_AXES:
            assert a in v.basis and v.basis[a]


class TestTagMatching:
    def test_태그는_정확히_일치할_때만_센다(self):
        """'데이트코스'가 '데이트'에 부분 일치해 전통공예명품전이
        '감정 체류 0.85'가 됐다. 태그는 낱말이지 문장이 아니다."""
        v = tag_place(place("제46회 전통공예명품전", path="문화관광 > 전시시설",
                            tags=["전통공예", "데이트코스", "서울전시"]))
        assert v.axes["living_emotion"] < 0.5

    def test_진짜_감성_태그는_잡는다(self):
        v = tag_place(place("63 스카이피크닉", path="문화관광 > 전망대",
                            tags=["야경", "루프탑"]))
        assert v.axes["living_emotion"] >= 0.8


class TestVibrant:
    def test_전시시설은_콘텐츠_실감이_높다(self):
        v = tag_place(place("아무개전", path="문화관광 > 전시시설"))
        assert v.axes["vibrant_content"] >= 0.85

    def test_상시_콘텐츠는_낮다(self):
        v = tag_place(place("아무개 편의점", category="쇼핑", path="쇼핑 > 편의점"))
        assert v.axes["vibrant_content"] <= 0.2


class TestLocal:
    def test_오래가게_지정이_가장_강한_신호다(self):
        """서울시가 직접 지정한 노포다. 우리가 추측할 필요가 없다."""
        v = tag_place(place("거안", tags=["오래가게"]))
        assert v.axes["immersive_local"] >= 0.9
        assert "오래가게" in v.basis["immersive_local"]

    def test_로컬_핫스폿_동네를_안다(self):
        v = tag_place(place("아무개 가게", category="쇼핑", dong="성수1가제1동"))
        assert v.axes["immersive_local"] >= 0.6


class TestWellness:
    def test_위성_식생지수를_쓴다(self):
        """'힐링'이라는 낱말이 설명에 있느냐보다 정확하다."""
        p = place("아무개 공원", category="자연관광", path="자연관광 > 도시공원")
        green = tag_place(p, ndvi=0.53)
        bare = tag_place(p, ndvi=0.02)
        assert green.axes["ambient_wellness"] > bare.axes["ambient_wellness"]
        assert "식생지수" in green.basis["ambient_wellness"]

    def test_같은_녹지면_실외가_더_높다(self):
        out = tag_place(place("공원", category="자연관광",
                              path="자연관광 > 도시공원"), ndvi=0.5)
        ind = tag_place(place("박물관", path="문화관광 > 전시시설 > 박물관"),
                        ndvi=0.5)
        assert out.axes["ambient_wellness"] > ind.axes["ambient_wellness"]

    def test_녹지_자료가_없으면_모른다고_한다(self):
        """0으로 채우면 '녹지가 없다'는 거짓말이 된다."""
        v = tag_place(place("아무개 가게", category="쇼핑", path="쇼핑 > 편의점"))
        assert v.axes["ambient_wellness"] is None


class TestTrusted:
    def test_무장애_정보가_주축이다(self):
        """지하철 안내는 93.6%, 2개 어권은 89.5%가 갖고 있어 변별력이 없다."""
        many = tag_place(place("A", access=["엘리베이터", "장애인화장실",
                                            "전용주차", "접근가능", "안내"]))
        none = tag_place(place("B", subway="2호선"))
        assert many.axes["trusted_global"] > 0.7
        assert none.axes["trusted_global"] < 0.3

    def test_무슬림_친화_지정을_그대로_쓴다(self):
        """서울시가 지정한 '살람서울'이다. 추측할 필요가 없다."""
        v = tag_place(place("아무개 식당", category="음식", tags=["살람서울"]))
        assert v.axes["trusted_global"] >= 0.25
        assert "무슬림" in v.basis["trusted_global"]


class TestInterests:
    def test_음식은_미식_축이_높다(self):
        v = tag_place(place("아무개 식당", category="음식", path="음식 > 한식"))
        assert v.interests["k_food"] >= 0.8

    def test_로컬_라이프는_로컬_몰입을_따라간다(self):
        v = tag_place(place("거안", tags=["오래가게"]))
        assert v.interests["local_life"] == v.axes["immersive_local"]


class TestServiceAxes:
    def test_빈_일정이면_0(self):
        got = service_axes({"steps": []})
        assert set(got) == set(SERVICE_AXES)
        assert all(v == 0.0 for v in got.values())

    def test_흔한_곳만_돌면_나만의_서울이_낮다(self):
        from weatherfit import popularity
        popularity._scores = {"A": 0.9, "B": 0.95}
        got = service_axes({"steps": [{"cid": "A"}, {"cid": "B"}]})
        assert got["your_seoul"] <= 0.2

    def test_덜_알려진_곳이_섞이면_올라간다(self):
        from weatherfit import popularity
        popularity._scores = {"A": 0.9, "B": 0.1}
        got = service_axes({"steps": [{"cid": "A"}, {"cid": "B"}]})
        assert got["your_seoul"] >= 0.4


class TestTrendFit:
    def test_스타일이_없으면_None(self):
        from weatherfit.trend import TrendProfile, trend_fit
        v = tag_place(place("아무개"))
        assert trend_fit(TrendProfile(), v) is None

    def test_강하게_맞는_곳이_더_높다(self):
        from weatherfit.trend import TrendProfile, trend_fit
        prof = TrendProfile.from_styles(["local"])
        strong = tag_place(place("거안", tags=["오래가게"]))
        weak = tag_place(place("아무개 편의점", category="쇼핑",
                               path="쇼핑 > 편의점"))
        assert trend_fit(prof, strong) > trend_fit(prof, weak)

    def test_코사인이_아니라_가중평균이다(self):
        """코사인은 축 하나만 고른 사용자에게 '조금 가진' 곳과 '많이 가진'
        곳을 똑같이 1.0으로 준다. 그러면 순위가 서지 않는다."""
        from weatherfit.trend import TrendProfile, trend_fit
        prof = TrendProfile.from_styles(["local"])
        a = tag_place(place("노포", tags=["오래가게"]))
        b = tag_place(place("동네 식당", category="음식", path="음식 > 한식"))
        assert trend_fit(prof, a) > trend_fit(prof, b) > 0

    def test_스타일을_말로_설명한다(self):
        from weatherfit.trend import TrendProfile
        assert "동네" in TrendProfile.from_styles(["local"]).describe()
