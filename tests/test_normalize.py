"""운영시간 파서 테스트.

실제 비짓서울 콘텐츠에서 그대로 가져온 문자열을 쓴다. 이 파서가 틀리면
'지금 열려 있는가' 판정 전체가 틀어지므로, 특히 **신뢰도 등급이 정직한지**를 본다.
파싱에 성공한 것처럼 보이지만 사실은 가정을 얹은 경우를 high로 올리면 안 된다.
"""
from datetime import datetime

import pytest

from weatherfit.normalize import DAYS, parse_hours, tag_environment


def days(rule) -> str:
    return "".join(DAYS[d] for d in rule.days)


class TestDayRanges:
    def test_요일_범위가_끝요일만_남지_않는다(self):
        oh = parse_hours("화요일 ~ 일요일 09:00~18:00")
        assert days(oh.rules[0]) == "화수목금토일"

    def test_축약형_요일_범위(self):
        oh = parse_hours("화~금요일 12:15~22:00")
        assert days(oh.rules[0]) == "화수목금"

    def test_한_줄에_요일별_구간이_여러개(self):
        oh = parse_hours("화~금요일 12:15~22:00 토요일 : 12:00~22:00 일요일 : 12:30~21:00")
        assert [(days(r), r.ranges) for r in oh.rules] == [
            ("화수목금", [("12:15", "22:00")]),
            ("토", [("12:00", "22:00")]),
            ("일", [("12:30", "21:00")]),
        ]
        assert oh.confidence == "high"

    def test_평일_주말_표현(self):
        oh = parse_hours("평일 09:00~18:00 주말 10:00~17:00")
        assert [days(r) for r in oh.rules] == ["월화수목금", "토일"]
        assert oh.confidence == "high"


class TestConfidence:
    def test_요일이_없으면_매일로_가정하되_low(self):
        oh = parse_hours("11:00-18:00")
        assert days(oh.rules[0]) == "월화수목금토일"
        assert oh.confidence == "low"
        assert "요일" in oh.reason

    def test_예외_단서가_있으면_low(self):
        oh = parse_hours("10:00~20:00(* 프로그램별 상이)")
        assert oh.confidence == "low"

    def test_시각_패턴이_없으면_none(self):
        assert parse_hours("상세 일정은 홈페이지 참조").confidence == "none"

    def test_빈_문자열은_none(self):
        oh = parse_hours("")
        assert oh.confidence == "none"
        assert oh.rules == []

    def test_상시운영(self):
        oh = parse_hours("24시간")
        assert oh.always_open is True
        assert oh.is_open_at(datetime(2026, 9, 2, 3, 0)) is True

    def test_휴무일의_공휴일_조건절은_신뢰도를_낮춘다(self):
        oh = parse_hours(
            "화요일 ~ 일요일 09:00~18:00",
            "공휴일을 제외한 매주 월요일 ※월요일이 휴일인 경우 정상개관",
        )
        assert 0 in oh.closed_days           # 월요일
        assert oh.confidence == "low"
        assert oh.exceptions


class TestClosedDays:
    def test_접두어가_한번만_붙어도_모두_잡는다(self):
        oh = parse_hours("17:15-23:00", "매주 토요일,일요일")
        assert [DAYS[d] for d in oh.closed_days] == ["토", "일"]

    def test_연중무휴는_휴무없음(self):
        assert parse_hours("10:00~18:00", "연중무휴").closed_days == []


class TestIsOpenAt:
    @pytest.mark.parametrize("when,expected", [
        (datetime(2026, 9, 2, 14, 0), True),    # 수 14시
        (datetime(2026, 9, 2, 8, 0), False),    # 수 08시 — 개점 전
        (datetime(2026, 8, 31, 14, 0), False),  # 월 — 휴무
    ])
    def test_요일과_시각을_함께_본다(self, when, expected):
        oh = parse_hours("화요일 ~ 일요일 09:00~18:00", "매주 월요일")
        assert oh.is_open_at(when) is expected

    def test_자정을_넘기는_영업시간(self):
        oh = parse_hours("매일 18:00~02:00")
        assert oh.is_open_at(datetime(2026, 9, 2, 23, 0)) is True
        assert oh.is_open_at(datetime(2026, 9, 2, 1, 0)) is True
        assert oh.is_open_at(datetime(2026, 9, 2, 12, 0)) is False

    def test_판정_불가는_None(self):
        assert parse_hours("").is_open_at(datetime(2026, 9, 2, 14, 0)) is None


class TestEnvironment:
    def test_카테고리로_먼저_판정(self):
        assert tag_environment("음식", "아무개 식당", "", [])[0] == "indoor"
        assert tag_environment("자연관광", "아무개 공원", "", [])[0] == "outdoor"

    def test_궁은_실외(self):
        label, _ = tag_environment("문화관광", "경복궁 수문장 교대의식", "", [])
        assert label == "outdoor"

    def test_한_글자_키워드는_엉뚱한_단어에_걸리지_않는다(self):
        """'궁'을 그냥 두면 '가능'·'기능' 안에서 걸린다."""
        label, _ = tag_environment(
            "문화관광", "조수미 40주년 콘서트", "관람 가능한 공연입니다", [])
        assert label != "outdoor"

    def test_소분류가_키워드보다_먼저다(self):
        """제목에는 전시 '제목'이 들어가 도움이 안 되는 경우가 많다."""
        label, why = tag_environment(
            "문화관광", "《안식의 결 Texture of Rest》", "", [],
            category_path="문화관광 > 전시시설")
        assert label == "indoor"
        assert "전시시설" in why

    def test_도시공원은_실외(self):
        label, _ = tag_environment("문화관광", "아무개 공원", "", [],
                                   category_path="문화관광 > 도시공원")
        assert label == "outdoor"

    def test_축제는_실외_전시회는_실내(self):
        assert tag_environment("축제/공연/행사", "아무개 축제", "", [],
                               category_path="축제/공연/행사 > 축제")[0] == "outdoor"
        assert tag_environment("축제/공연/행사", "아무개 전시회", "", [],
                               category_path="축제/공연/행사 > 행사 > 전시회")[0] == "indoor"

    def test_박물관은_실내(self):
        label, _ = tag_environment("문화관광", "서울역사박물관 기획전", "", [])
        assert label == "indoor"

    def test_근거가_없으면_unknown(self):
        label, reason = tag_environment("축제/공연/행사", "2027 S/S 서울패션위크", "", [])
        assert label == "unknown"
        assert reason


class TestKoreanTime:
    """미술관·공연장 원문은 콜론 없이 '오전 10시–오후 8시'로 적는다.

    이걸 못 읽으면 서울시립미술관 계열이 통째로 '판정 불가'가 된다.
    """

    def test_한글_시각을_읽는다(self):
        oh = parse_hours("화요일~일요일, 오전11시-오후7시")
        assert oh.confidence == "high"
        assert oh.rules[0].ranges == [("11:00", "19:00")]

    @pytest.mark.parametrize("raw,want", [
        ("오전 10시", "10:00"), ("오후 8시", "20:00"), ("오후 3시 30분", "15:30"),
        ("저녁 7시", "19:00"), ("오전 12시", "00:00"), ("오후 12시", "12:00"),
        ("낮 2시", "14:00"),
    ])
    def test_오전_오후를_구분한다(self, raw, want):
        from weatherfit.normalize import to_24h
        assert to_24h(raw) == want

    def test_시간_길이는_건드리지_않는다(self):
        """'3시간 소요'의 3시간은 시각이 아니다."""
        from weatherfit.normalize import to_24h
        assert to_24h("관람 3시간 소요") == "관람 3시간 소요"

    def test_요일을_나열한_표기도_읽는다(self):
        """'토 · 일 · 공휴일'을 못 읽으면 주말 시간표가 평일 것으로 붙는다.
        모른다고 하는 것보다 나쁘다."""
        oh = parse_hours("평일(화-금): 오전 10시–오후 8시 "
                         "토 · 일 · 공휴일: 오전 10시–오후 7시")
        got = {days(r): r.ranges for r in oh.rules}
        assert got["화수목금"] == [("10:00", "20:00")]
        assert got["토일"] == [("10:00", "19:00")]


class TestShowtimes:
    """회차 공연은 영업시간이 아니다.

    '매일 3회 : 오전11시, 오후2시, 오후3시30분'을 못 읽으면 운영시간을
    10~20시로 가정해 10시에 도착하게 된다. 덕수궁 왕궁수문장 교대의식에서
    실제로 그랬다 — 갔는데 아무것도 없는 바로 그 상황이다.
    """
    RAW = "매일 3회 : 오전11시, 오후2시, 오후3시30분"

    def test_회차를_구간으로_바꾼다(self):
        oh = parse_hours(self.RAW)
        assert [r[0] for r in oh.rules[0].ranges] == ["11:00", "14:00", "15:30"]

    def test_회차_사이에는_닫혀_있다(self):
        oh = parse_hours(self.RAW)
        assert oh.is_open_at(datetime(2026, 9, 3, 10, 5)) is False
        assert oh.is_open_at(datetime(2026, 9, 3, 13, 0)) is False

    def test_회차_시간에는_열려_있다(self):
        oh = parse_hours(self.RAW)
        assert oh.is_open_at(datetime(2026, 9, 3, 11, 10)) is True
        assert oh.is_open_at(datetime(2026, 9, 3, 15, 45)) is True

    def test_가정한_회차_길이를_밝힌다(self):
        oh = parse_hours(self.RAW)
        assert oh.confidence == "low"        # 회당 길이는 우리 가정이다
        assert "회차" in oh.reason

    def test_한_시각뿐이면_회차로_보지_않는다(self):
        """'오후 7시 30분부터 90분간'은 회차 목록이 아니다."""
        assert parse_hours("오후 7시 30분부터 90분간 진행").rules == []
