"""품질·다양성·인기·취향 테스트.

이 모듈들이 틀리면 약국이 코스에 들어오고 식당만 네 곳 나온다.
실제로 그랬던 회귀를 그대로 테스트로 박아 둔다.
"""
import pytest

from weatherfit.index import Place
from weatherfit.models import Content
from weatherfit.normalize import parse_hours, tag_environment
from weatherfit.quality import (Diversity, is_touristic, quality, radius_for,
                                rank, subcategory)
from weatherfit.taste import Taste, tokens_of


def place(title="아무개", category="문화관광", path="", desc="", tags=None,
          homepage="", phone="", access=None, subway="", use_time="",
          lat=37.5665, lon=126.9780, cid=None, start="", end=""):
    c = Content(
        cid=cid or f"KO{abs(hash(title)) % 10**7}",
        title=title, category=category, category_path=path or category,
        description=desc, tags=tags or [], homepage=homepage, phone=phone,
        accessibility=access or [], subway_raw=subway, use_time_raw=use_time,
        lat=lat, lon=lon, schedule_start=start, schedule_end=end,
    )
    env, why = tag_environment(c.category, c.title, c.description, c.tags,
                               c.category_path)
    return Place(content=c, hours=parse_hours(use_time), environment=env,
                 env_reason=why)


class TestTouristic:
    @pytest.mark.parametrize("title", [
        "메디킹덤 약국", "GS25 DXLAB점", "백양세탁", "송파관광정보센터",
        "광화문 관광안내소", "코엑스 물품보관소", "다이소 이스턴스퀘어점",
    ])
    def test_관광지가_아닌_것은_일정에서_뺀다(self, title):
        assert is_touristic(place(title)) is False

    @pytest.mark.parametrize("title", [
        "국립중앙박물관", "북촌한옥마을", "성수연방", "광장시장",
    ])
    def test_진짜_관광지는_남긴다(self, title):
        assert is_touristic(place(title)) is True

    def test_주변_목록에는_남아야_한다(self):
        """약국을 일정에 넣지 않는 것과 목록에서 지우는 것은 다르다.

        is_touristic은 일정 구성에서만 쓰고, /api/candidates는 거르지 않는다.
        약이 필요한 순간도 있다.
        """
        from weatherfit import server
        src = server.__file__
        with open(src, encoding="utf-8") as f:
            body = f.read()
        candidates_body = body.split("def candidates(")[1].split("def ")[0]
        assert "is_touristic" not in candidates_body


class TestQuality:
    def test_충실한_콘텐츠가_높다(self):
        rich = place("풍성한 곳", desc="설명" * 200, tags=["a", "b", "c", "d", "e", "f"],
                     homepage="http://x", phone="02-1", access=["엘리베이터"],
                     subway="1호선", use_time="매일 09:00~18:00")
        bare = place("빈약한 곳")
        assert quality(rich) > quality(bare)

    def test_분류_가중이_적용된다(self):
        """같은 충실도면 축제가 쇼핑보다 관광 일정의 뼈대에 가깝다."""
        common = dict(desc="설명" * 100, tags=["a", "b", "c"])
        festival = place("축제", category="축제/공연/행사", **common)
        shop = place("가게", category="쇼핑", **common)
        assert quality(festival) > quality(shop)

    def test_0에서_1_사이(self):
        p = place("x", desc="설명" * 500, tags=list("abcdefgh"),
                  homepage="h", phone="p", access=["a"], subway="s",
                  use_time="매일 09:00~18:00")
        assert 0.0 <= quality(p) <= 1.0


class TestDiversity:
    def test_음식은_두곳까지(self):
        d = Diversity()
        a = place("식당1", category="음식", path="음식 > 한식")
        b = place("식당2", category="음식", path="음식 > 카페/찻집")
        c = place("식당3", category="음식", path="음식 > 주점")
        assert d.allows(a); d.add(a)
        assert d.allows(b); d.add(b)
        assert d.allows(c) is False          # 세 번째는 막힌다

    def test_같은_소분류는_한곳까지(self):
        d = Diversity()
        a = place("한식1", category="음식", path="음식 > 한식")
        b = place("한식2", category="음식", path="음식 > 한식")
        d.add(a)
        assert d.allows(b) is False

    def test_소분류는_두단계까지_본다(self):
        p = place("x", path="음식 > 외국식 > 중식")
        assert subcategory(p) == "음식 > 외국식"


class TestRank:
    def test_가까워도_품질이_낮으면_밀린다(self):
        near_poor = place("가깝고 빈약", lat=37.5665, lon=126.9780)
        far_rich = place("멀지만 충실", lat=37.5700, lon=126.9820,
                         desc="설명" * 200, tags=list("abcdef"),
                         homepage="h", access=["엘리베이터"], subway="1호선",
                         use_time="매일 09:00~18:00")
        ordered = rank([(near_poor, ""), (far_rich, "")],
                       (37.5665, 126.9780), pop={})
        assert ordered[0][0].title == "멀지만 충실"

    def test_취향이_반영된다(self):
        art = place("미술관", category="문화관광", tags=["전시", "미술"])
        food = place("식당", category="음식", path="음식 > 한식", tags=["맛집"])
        t = Taste()
        t.declare(["음식"], weight=3.0)
        ordered = rank([(art, ""), (food, "")], (37.5665, 126.9780),
                       taste=t, pop={})
        assert ordered[0][0].content.category == "음식"

    def test_싫다고_한_것은_감점된다(self):
        a = place("싫은 곳", cid="KO1")
        b = place("보통 곳", cid="KO2")
        t = Taste(disliked=["KO1"])
        ordered = rank([(a, ""), (b, "")], (37.5665, 126.9780), taste=t, pop={})
        assert ordered[-1][0].cid == "KO1"


class TestRadius:
    def test_시간이_길수록_넓어진다(self):
        assert radius_for(2) < radius_for(4) < radius_for(6)

    def test_두시간짜리에_사킬로는_과하다(self):
        """이동에만 절반을 쓰게 된다."""
        assert radius_for(2) <= 1500


class TestTaste:
    def test_태그가_없으면_제목에서_뽑는다(self):
        p = place("북촌 한옥마을 야경 투어", tags=[])
        toks = tokens_of(p)
        assert any("한옥" in t or "북촌" in t for t in toks)

    def test_좋아요가_친화도를_올린다(self):
        liked = place("미술관 A", category="문화관광", tags=["전시", "현대미술"])
        similar = place("미술관 B", category="문화관광", tags=["전시", "회화"])
        t = Taste()
        before = t.affinity(similar)
        t.like(liked)
        assert t.affinity(similar) > before

    def test_싫어요는_음수(self):
        p = place("싫은 곳", cid="KOX")
        t = Taste()
        t.dislike(p)
        assert t.affinity(p) == -1.0

    def test_빈_프로필은_영향을_주지_않는다(self):
        assert Taste().affinity(place("아무거나")) == 0.0

    def test_직렬화_왕복(self):
        t = Taste()
        t.declare(["음식"])
        t.like(place("맛집", category="음식", tags=["한식"]))
        again = Taste.from_dict(t.to_dict())
        assert again.categories == t.categories
        assert again.liked == t.liked

    def test_태그가_무한정_늘지_않는다(self):
        t = Taste()
        for i in range(60):
            t.like(place(f"장소{i}", tags=[f"태그{i}{j}" for j in range(10)]))
        assert len(t.tags) <= 120

    def test_학습_내용을_설명할_수_있다(self):
        t = Taste()
        t.declare(["문화관광"], weight=2.0)
        assert "문화관광" in t.describe()


class TestPopularNote:
    def test_숫자로_말한다(self):
        """막대 길이만 보여 주면 근거가 아니라 장식이다."""
        from weatherfit import popularity
        from weatherfit.quality import popular_note
        popularity._notes = {"KOX": "위키백과 3개월 9,352회 조회"}
        p = place("경복궁", cid="KOX")
        assert "9,352" in popular_note(p)

    def test_모르면_모른다고_한다(self):
        from weatherfit import popularity
        from weatherfit.quality import popular_note
        popularity._notes = {}
        assert "자료 없음" in popular_note(place("무명", cid="KOY"))


class TestRatingFloor:
    """평점은 합성값이다. 그래서 두 가지를 동시에 지켜야 한다 —
    품질 하한은 실제로 작동하고, 그 값은 화면으로 새 나가지 않는다."""

    def test_합성임을_숨기지_않는다(self):
        """제안서가 '모르는 것을 아는 척하지 않는다'를 차별점으로 내세운다.
        지어낸 값을 실측처럼 흘리면 그 원칙이 가장 아픈 자리에서 무너진다."""
        from weatherfit import ratings

        assert ratings.is_real() is False
        assert ratings.table()["meta"]["synthetic"] is True

    def test_같은_가게는_늘_같은_값이다(self):
        """돌릴 때마다 바뀌면 시연 중에 같은 화면이 두 번 안 나온다."""
        from weatherfit.ratings import synth

        assert synth("KO시험") == synth("KO시험")
        assert synth("KO시험") != synth("KO다른곳")

    def test_그럴듯한_범위에_있다(self):
        """5.0이 흔하면 가짜 티가 나고 3.0이 흔해도 그렇다."""
        from weatherfit.ratings import HI, LO, synth

        vals = [synth(f"KO{i}")["rating"] for i in range(400)]
        assert all(LO <= v <= HI for v in vals)
        assert 4.0 <= sum(vals) / len(vals) <= 4.4

    def test_하한을_넘는지_판별한다(self):
        from weatherfit import ratings

        got = [c for c in ratings.table()["place"] if not ratings.passes(c)]
        assert got, "하한에 걸리는 곳이 하나도 없으면 문턱이 무의미하다"

    def test_자료가_없으면_막지_않는다(self):
        """모르는 것을 '나쁘다'로 처리하면 멀쩡한 곳이 사라진다."""
        from weatherfit.ratings import passes

        assert passes("자료없는cid") is True

    def test_평점이_응답으로_새지_않는다(self):
        """화면에 띄우지 않기로 했다. 카드와 일정 어디에도 없어야 한다."""
        import inspect

        from weatherfit import course, server

        for mod in (server, course):
            src = inspect.getsource(mod)
            assert "rating" not in src, f"{mod.__name__}에 평점이 새어 있다"
