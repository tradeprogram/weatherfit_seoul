"""'지금 갈 수 있는가' 판정.

제안서의 [B] 웨더핏 판정 단계에 해당한다. 네 가지를 차례로 건다.

    1. 기간   — 종료된 행사인가
    2. 운영   — 오늘 휴무이거나 영업시간 밖인가
    3. 날씨   — 지금 날씨에 실외를 권할 수 있는가
    4. 거리   — 남은 시간 안에 닿을 수 있는가  (좌표가 있을 때만)

판정 불가(None)와 탈락(False)을 구분하는 게 핵심이다. 정보가 없어서 모르는 것을
'열려 있다'로 처리하면 헛걸음이 생기고, '닫혔다'로 처리하면 멀쩡한 후보가 사라진다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .models import Content
from .normalize import OpeningHours, parse_hours, tag_environment


@dataclass
class Weather:
    """판정에 필요한 최소한의 기상 상태. 기상청 초단기실황·예보에서 채운다."""
    temp_c: float = 20.0
    precip_mm: float = 0.0
    sky: str = "맑음"            # 맑음 | 구름많음 | 흐림
    pty: str = "없음"            # 없음 | 비 | 비/눈 | 눈 | 소나기
    source: str = "manual"      # kma | fallback | manual
    note: str = ""              # 출처·신뢰도에 대한 사람 읽을 설명

    @property
    def is_raining(self) -> bool:
        return self.pty != "없음" or self.precip_mm > 0

    @property
    def is_extreme(self) -> bool:
        return self.temp_c >= 33 or self.temp_c <= -6

    @property
    def outdoor_ok(self) -> bool:
        return not (self.is_raining or self.is_extreme)

    def describe(self) -> str:
        if self.is_raining:
            return f"{self.pty} (강수 {self.precip_mm}mm)"
        if self.temp_c >= 33:
            return f"폭염 {self.temp_c}°C"
        if self.temp_c <= -6:
            return f"한파 {self.temp_c}°C"
        return f"{self.sky} {self.temp_c}°C"


@dataclass
class Verdict:
    ok: bool | None            # True 통과 / False 탈락 / None 판정 불가
    stage: str                 # 어느 단계에서 갈렸나
    reason: str

    @property
    def label(self) -> str:
        return {True: "통과", False: "탈락", None: "판정불가"}[self.ok]


def parse_ymd(s: str) -> date | None:
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def check_period(item: Content, today: date) -> Verdict:
    """기간이 지정된 행사인지, 지났는지."""
    if not item.is_dated_event:
        return Verdict(True, "기간", "상시 콘텐츠 (기간 없음)")

    start = parse_ymd(item.schedule_start)
    end = parse_ymd(item.schedule_end) or start
    if end is None:
        return Verdict(None, "기간", "일정 표기를 해석할 수 없음")
    if end < today:
        return Verdict(False, "기간", f"{item.schedule_end} 종료")
    if start and start > today:
        return Verdict(False, "기간", f"{item.schedule_start} 시작 예정")
    return Verdict(True, "기간", "진행 중")


def check_hours(hours: OpeningHours, when: datetime) -> Verdict:
    state = hours.is_open_at(when)
    if state is None:
        return Verdict(None, "운영", hours.reason or "운영시간 판정 불가")
    if not state:
        return Verdict(False, "운영", "현재 휴무 또는 영업시간 밖")
    conf = "" if hours.confidence == "high" else f" (신뢰도 {hours.confidence})"
    return Verdict(True, "운영", f"영업 중{conf}")


def check_weather(environment: str, weather: Weather) -> Verdict:
    if environment == "indoor":
        return Verdict(True, "날씨", "실내")
    if environment == "outdoor":
        if weather.outdoor_ok:
            return Verdict(True, "날씨", f"실외 가능 — {weather.describe()}")
        return Verdict(False, "날씨", f"실외 부적합 — {weather.describe()}")
    # unknown: 날씨가 나쁘면 위험을 감수하지 않는다
    if weather.outdoor_ok:
        return Verdict(True, "날씨", "실내외 불명 — 날씨 양호로 통과")
    return Verdict(None, "날씨", "실내외 불명 — 악천후라 판정 보류")


def evaluate(item: Content, when: datetime, weather: Weather) -> tuple[Verdict, dict]:
    """한 건에 대한 최종 판정과 중간 근거."""
    hours = parse_hours(item.use_time_raw, item.closed_days_raw)
    environment, env_reason = tag_environment(
        item.category, item.title, item.description, item.tags
    )

    detail = {
        "hours_confidence": hours.confidence,
        "hours_reason": hours.reason,
        "environment": environment,
        "environment_reason": env_reason,
    }

    period = check_period(item, when.date())
    if period.ok is not True:
        return period, detail

    weather_v = check_weather(environment, weather)
    if weather_v.ok is False:
        return weather_v, detail

    hours_v = check_hours(hours, when)
    if hours_v.ok is False:
        return hours_v, detail

    # 남은 관문 중 하나라도 '모름'이면 최종도 '모름'이다
    for v in (weather_v, hours_v):
        if v.ok is None:
            return Verdict(None, v.stage, v.reason), detail

    return Verdict(True, "통과", "지금 갈 수 있음"), detail
