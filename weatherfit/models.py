"""비짓서울 콘텐츠의 도메인 모델."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


CATEGORIES = {
    "문화관광": "Ca0o2d4",
    "쇼핑": "Cu8e6t5",
    "숙박": "Ch4v8z7",
    "역사관광": "Ca1z6p7",
    "음식": "Cl9s3y9",
    "자연관광": "Co6c2n2",
    "체험관광": "Cc9i5o2",
    "축제": "Cv7s8m5",
}

LANGS = ["ko", "en", "ja", "zh-CN", "zh-TW", "ru", "ms"]


@dataclass
class Content:
    """콘텐츠 1건. 공식 API와 공개 카탈로그 양쪽에서 같은 형태로 채운다."""

    cid: str
    title: str
    category: str = ""              # "축제/공연/행사" 같은 대분류 표시명
    category_path: str = ""         # "음식 > 한식"
    summary: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)

    # 시간 — 유효성 판정의 재료
    schedule_start: str = ""        # "2024.08.09"
    schedule_end: str = ""
    use_time_raw: str = ""          # 자유 문장 (cmmn_use_time)
    closed_days_raw: str = ""       # 자유 문장 (closed_days)

    # 공간
    address: str = ""
    lon: float | None = None
    lat: float | None = None
    subway_raw: str = ""            # "5호선 광화문역 7번 출구에서 약 462m (도보 7분)"
    place: str = ""

    # 부가
    phone: str = ""
    homepage: str = ""
    fee_raw: str = ""
    accessibility: list[str] = field(default_factory=list)
    note: str = ""                  # "이것만은 꼭!"

    lang: str = "ko"
    source: str = ""                # "catalog" | "api"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Content":
        known = {f for f in cls.__dataclass_fields__}
        item = cls(**{k: v for k, v in d.items() if k in known})
        # 예전에 수집한 파일에는 구분자가 겹친 경로가 들어 있다
        # ("문화관광 > > > 전시시설"). 읽을 때 정리한다.
        if ">" in item.category_path:
            parts = [p.strip() for p in item.category_path.split(">") if p.strip()]
            item.category_path = " > ".join(parts)
        return item

    @property
    def is_dated_event(self) -> bool:
        """기간이 지정된 콘텐츠인가."""
        return bool(self.schedule_start or self.schedule_end)

    @property
    def run_days(self) -> int | None:
        """행사 기간 일수. 기간 표기가 없거나 못 읽으면 None."""
        from .validate import parse_ymd
        s = parse_ymd(self.schedule_start)
        e = parse_ymd(self.schedule_end) or s
        return (e - s).days + 1 if s and e else None

    @property
    def is_short_event(self) -> bool:
        """놓치면 사라지는 행사인가.

        연중 상시 영업을 2026.01.01~12.31로 적어 둔 식당이 적지 않아,
        기간이 있다는 것만으로는 '행사'라고 볼 수 없다. 90일 이하만 행사로 본다.
        """
        d = self.run_days
        return d is not None and d <= 90
