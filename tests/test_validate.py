"""판정 로직 테스트.

이 모듈의 존재 이유는 **탈락(False)과 판정 불가(None)를 섞지 않는 것**이다.
모르는 것을 '열려 있다'로 처리하면 헛걸음이 생기고, '닫혔다'로 처리하면 멀쩡한
후보가 사라진다. 그 경계가 무너지지 않는지 확인한다.
"""
from datetime import date, datetime

import pytest

from weatherfit.models import Content
from weatherfit.validate import (Weather, check_period, check_weather,
                                 evaluate, evaluate_place, parse_ymd)

WHEN = datetime(2026, 9, 2, 14, 0)
TODAY = WHEN.date()
CLEAR = Weather(temp_c=22.0)
RAIN = Weather(temp_c=19.0, precip_mm=4.0, pty="비", sky="흐림")
HEAT = Weather(temp_c=35.0)


def content(**kw) -> Content:
    base = dict(cid="X1", title="테스트", category="문화관광",
                use_time_raw="매일 09:00~18:00")
    return Content(**{**base, **kw})


class TestPeriod:
    def test_종료된_행사는_탈락(self):
        v = check_period(content(schedule_start="2026.08.01",
                                 schedule_end="2026.08.20"), TODAY)
        assert v.ok is False and "종료" in v.reason

    def test_시작_전_행사도_탈락(self):
        v = check_period(content(schedule_start="2026.10.01",
                                 schedule_end="2026.10.10"), TODAY)
        assert v.ok is False and "예정" in v.reason

    def test_진행중이면_통과(self):
        v = check_period(content(schedule_start="2026.09.01",
                                 schedule_end="2026.09.06"), TODAY)
        assert v.ok is True

    def test_기간이_없으면_상시로_통과(self):
        assert check_period(content(), TODAY).ok is True

    def test_해석할_수_없는_날짜는_판정불가(self):
        v = check_period(content(schedule_start="상시", schedule_end="미정"), TODAY)
        assert v.ok is None


class TestWeather:
    def test_실내는_날씨와_무관(self):
        assert check_weather("indoor", RAIN).ok is True
        assert check_weather("indoor", HEAT).ok is True

    def test_실외는_비에_탈락(self):
        assert check_weather("outdoor", RAIN).ok is False

    def test_실외는_폭염에도_탈락(self):
        assert check_weather("outdoor", HEAT).ok is False

    def test_실내외_불명은_날씨가_좋으면_통과(self):
        assert check_weather("unknown", CLEAR).ok is True

    def test_실내외_불명은_악천후에_판정보류(self):
        """모르는 것을 '괜찮다'고 하지 않는다 — 탈락이 아니라 보류."""
        assert check_weather("unknown", RAIN).ok is None


class TestEvaluate:
    def test_기간에서_먼저_걸리면_뒤는_보지_않는다(self):
        v, _ = evaluate(content(schedule_start="2026.01.01",
                                schedule_end="2026.01.31"), WHEN, CLEAR)
        assert v.ok is False and v.stage == "기간"

    def test_영업시간_밖이면_탈락(self):
        v, _ = evaluate(content(category="음식", use_time_raw="매일 18:00~23:00"),
                        WHEN, CLEAR)
        assert v.ok is False and v.stage == "운영"

    def test_운영정보가_없으면_판정불가(self):
        v, d = evaluate(content(category="음식", use_time_raw=""), WHEN, CLEAR)
        assert v.ok is None
        assert d["hours_confidence"] == "none"

    def test_모두_통과(self):
        v, d = evaluate(content(category="음식", use_time_raw="매일 09:00~22:00"),
                        WHEN, CLEAR)
        assert v.ok is True
        assert d["environment"] == "indoor"


class TestParseYmd:
    def test_여러_형식(self):
        assert parse_ymd("2026.09.02") == date(2026, 9, 2)
        assert parse_ymd("2026-09-02") == date(2026, 9, 2)
        assert parse_ymd("20260902") == date(2026, 9, 2)

    def test_못_읽으면_None(self):
        assert parse_ymd("추후 공지") is None
        assert parse_ymd("") is None


class TestShortEvent:
    def test_연중_상시는_행사가_아니다(self):
        """식당이 영업기간을 2026.01.01~12.31로 적어 두는 경우가 많다."""
        c = content(schedule_start="2026.01.01", schedule_end="2026.12.31")
        assert c.is_dated_event is True
        assert c.is_short_event is False

    def test_며칠짜리는_행사(self):
        c = content(schedule_start="2026.09.01", schedule_end="2026.09.06")
        assert c.is_short_event is True
        assert c.run_days == 6


class TestPathsAgree:
    """같은 콘텐츠를 어느 경로로 판정하든 결과가 같아야 한다.

    evaluate()(단건)와 evaluate_place()(적재 인덱스)가 tag_environment에
    category_path를 넘기는지 여부로 갈렸다. 그래서 근거 리포트가 실내외
    불명을 27.7%로 냈다 — 실제는 6.9%다. 제안서에 싣는 수치가 어느 함수를
    부르느냐에 따라 네 배 달라지면 안 된다.
    """

    @pytest.mark.parametrize("category,path,title", [
        ("문화관광", "문화관광 > 전시시설", "《안식의 결 Texture of Rest》"),
        ("문화관광", "문화관광 > 도시공원", "아무개 근린공원"),
        ("축제/공연/행사", "축제/공연/행사 > 축제", "아무개 축제"),
        ("축제/공연/행사", "축제/공연/행사 > 행사 > 전시회", "아무개 전시회"),
    ])
    def test_단건과_인덱스가_같은_실내외를_낸다(self, category, path, title):
        from weatherfit.index import build_index

        c = Content(cid="KO1", title=title, category=category,
                    category_path=path, use_time_raw="매일 09:00~18:00",
                    lat=37.5665, lon=126.9780)
        when = datetime(2026, 9, 3, 12, 0)
        w = Weather(temp_c=22.0)

        _, single = evaluate(c, when, w)
        place = build_index([c]).places[0]
        _, indexed = evaluate_place(place, when, w)
        assert single["environment"] == indexed["environment"]
        assert single["environment"] != "unknown"
