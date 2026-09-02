"""인기도 매칭 테스트.

위키백과를 인기 지표로 쓰면 곧바로 이름 충돌에 부딪힌다. 실제로 났던
오매칭을 그대로 박아 둔다 — 이걸 놓치면 종각의 카레집이 히말라야산맥의
조회수를 업고 '관광지 중의 관광지'로 올라온다.
"""
import pytest

from weatherfit.popularity import (Popularity, judge_match, normalize,
                                   normalize_scores, similar)


class TestNormalize:
    @pytest.mark.parametrize("raw,want", [
        ("스타벅스 이대점", "스타벅스"),
        ("강강술래(역삼점)", "강강술래"),
        ("런던 베이글 뮤지엄 도산점", "런던 베이글 뮤지엄"),
        ("《안식의 결》", "안식의 결"),
    ])
    def test_지점명과_기호를_걷어낸다(self, raw, want):
        assert normalize(raw) == want


class TestSimilar:
    def test_같은_이름은_1(self):
        assert similar("경복궁", "경복궁") == 1.0
        assert similar("노량진 수산시장", "노량진수산시장") == 1.0

    def test_포함은_1이_아니다(self):
        """'히말라야'가 '히말라야산맥'에 들어 있다고 같은 것은 아니다.

        여기서 1.0을 돌려주던 것이 오매칭 458건의 출발점이었다.
        """
        assert similar("히말라야", "히말라야산맥") < 1.0
        assert similar("스페인클럽", "스페인") < 0.9

    def test_길이가_비슷한_포함은_높게(self):
        assert similar("버뮤다삼각지", "버뮤다 삼각지대") > 0.8


class TestVerifyRules:
    """verify()가 쓰는 판정 규칙. 네트워크 없이 규칙만 확인한다."""

    def judge(self, title, wiki, category, km=None):
        return judge_match(title, wiki, category, km)[0]

    def test_먼_문서는_뺀다(self):
        assert self.judge("히말라야 종각점", "히말라야산맥", "음식", km=3862) is False
        assert self.judge("버뮤다삼각지", "버뮤다 삼각지대", "음식", km=12813) is False

    def test_가깝고_이름이_같으면_남긴다(self):
        assert self.judge("광장시장", "광장시장", "쇼핑", km=0.3) is True
        assert self.judge("경복궁", "경복궁", "역사관광", km=0.1) is True

    def test_가깝지만_이름이_다른_가게는_뺀다(self):
        """광화문 옆 '광화문집'은 광화문 문서와 가깝지만 같은 대상이 아니다."""
        assert self.judge("광화문집", "광화문", "음식", km=0.5) is False

    def test_좌표_없는_문서에_걸린_식당은_뺀다(self):
        """무궁화(꽃)·아리랑(민요)·중국(나라)에 식당 이름이 걸린다."""
        for t in ["무궁화", "아리랑", "중국", "나마스테", "보노보노"]:
            assert self.judge(t, t, "음식") is False

    def test_좌표_없는_문서라도_명소면_이름이_같을_때_남긴다(self):
        assert self.judge("청계천", "청계천", "자연관광") is True
        assert self.judge("성균관대학교", "성균관대학교", "문화관광") is True

    def test_지점은_좌표로_확인될_때만(self):
        assert self.judge("스타벅스 이대점", "스타벅스", "음식") is False
        assert self.judge("교보문고 광화문점", "교보문고", "쇼핑") is False

    def test_인물_기념일_문서는_뺀다(self):
        assert self.judge("윤봉길 의사 기념관", "윤봉길", "역사관광") is False
        assert self.judge("우리들의 광복절", "광복절", "축제/공연/행사") is False
        assert self.judge("2023 정조대왕 능행차 공동재현", "정조",
                          "축제/공연/행사") is False


class TestScoring:
    def test_검증에_떨어진_곳은_점수에_반영되지_않는다(self):
        good = Popularity(cid="A", title="경복궁", wiki_title="경복궁",
                          wiki_views=9352, geo_ok=True)
        bad = Popularity(cid="B", title="히말라야 종각점", wiki_title="히말라야산맥",
                         wiki_views=11700, geo_ok=False)
        normalize_scores([good, bad])
        assert bad.score == 0.0
        assert good.score > 0.5

    def test_조회수가_많을수록_높다(self):
        a = Popularity(cid="A", title="a", wiki_title="a", wiki_views=14264)
        b = Popularity(cid="B", title="b", wiki_title="b", wiki_views=500)
        normalize_scores([a, b])
        assert a.score > b.score

    def test_문서가_없으면_0(self):
        r = Popularity(cid="C", title="c")
        normalize_scores([r])
        assert r.score == 0.0

    def test_문서는_있고_조회가_없으면_약한_신호(self):
        r = Popularity(cid="D", title="d", wiki_title="d", wiki_views=0)
        normalize_scores([r])
        assert 0.0 < r.score < 0.5
