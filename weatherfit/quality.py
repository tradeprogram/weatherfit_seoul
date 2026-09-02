"""관광지로서의 품질과 일정의 다양성.

가까운 순으로만 고르면 약국과 관광안내소가 코스에 들어온다. 실제로 그랬다.
여의도에서는 식당만 네 곳이 나왔다. 거리는 필요조건이지 충분조건이 아니다.

두 가지를 더한다.

**품질** — 비짓서울 API가 콘텐츠에 얼마나 공을 들였는지가 곧 관광지로서의
가치와 상관이 있다. 설명이 길고 태그가 붙고 홈페이지와 무장애 정보까지
채워진 항목은 재단이 소개할 만하다고 판단한 곳이다.

**다양성** — 반나절에 식당 네 곳은 일정이 아니다. 대분류·소분류별 상한을 둔다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .index import Place

# 관광 일정에 넣지 않는다. 주변 목록에는 그대로 남는다 — 약국이 필요한 순간도 있다.
EXCLUDE_TITLE = re.compile(
    r"약국|편의점|세탁|관광안내소|포토부스|다이소|무인|코인빨래|"
    r"CU |GS25|세븐일레븐|이마트24|이마트\s|롯데마트|홈플러스"
)
EXCLUDE_PATH = ("쇼핑 > 편의점", "쇼핑 > 대형마트")

# 분류별 기본 가중. 관광 일정의 뼈대가 되는 것을 우대한다.
CATEGORY_WEIGHT = {
    "축제/공연/행사": 1.00,
    "문화관광": 0.95,
    "역사관광": 0.95,
    "체험관광": 0.90,
    "자연관광": 0.88,
    "음식": 0.80,
    "쇼핑": 0.70,
    "숙박": 0.30,
}

# 일정 안에서 같은 분류가 몇 번까지 나올 수 있는가
MAX_PER_CATEGORY = {"음식": 2}
DEFAULT_MAX_CATEGORY = 2
MAX_PER_SUBCATEGORY = 1


def is_touristic(place: Place) -> bool:
    """관광 일정에 넣을 만한가."""
    c = place.content
    if EXCLUDE_TITLE.search(c.title):
        return False
    path = c.category_path or c.category
    if any(path.startswith(p) for p in EXCLUDE_PATH):
        return False
    return True


def quality(place: Place) -> float:
    """0~1. 비짓서울 콘텐츠의 충실도를 관광지 가치의 대리 지표로 쓴다."""
    c = place.content
    score = 0.0

    # 설명 — 가장 강한 신호. 중앙값이 237자라 500자를 만점으로 둔다.
    score += min(len(c.description or "") / 500.0, 1.0) * 0.30
    score += min(len(c.tags) / 6.0, 1.0) * 0.15
    if c.homepage:
        score += 0.10
    if c.accessibility:
        score += 0.10                     # 무장애 정보까지 채운 곳은 관리되는 시설
    if c.phone:
        score += 0.05
    if c.subway_raw:
        score += 0.05                     # 대중교통 안내가 있으면 찾아가기 쉽다

    score += {"high": 0.15, "low": 0.07}.get(place.hours.confidence, 0.0)
    if c.is_short_event:
        score += 0.10                     # 지금만 볼 수 있는 것

    return min(score, 1.0) * CATEGORY_WEIGHT.get(c.category, 0.8)


def subcategory(place: Place) -> str:
    path = place.content.category_path or place.content.category
    parts = [p.strip() for p in path.split(">") if p.strip()]
    return " > ".join(parts[:2]) if len(parts) > 1 else (parts[0] if parts else "")


@dataclass
class Diversity:
    """일정에 담은 분류를 세어 상한을 지킨다."""
    categories: dict[str, int] = None
    subcategories: dict[str, int] = None

    def __post_init__(self):
        self.categories = self.categories or {}
        self.subcategories = self.subcategories or {}

    def allows(self, place: Place) -> bool:
        cat = place.content.category
        sub = subcategory(place)
        cap = MAX_PER_CATEGORY.get(cat, DEFAULT_MAX_CATEGORY)
        if self.categories.get(cat, 0) >= cap:
            return False
        if self.subcategories.get(sub, 0) >= MAX_PER_SUBCATEGORY:
            return False
        return True

    def add(self, place: Place) -> None:
        cat = place.content.category
        sub = subcategory(place)
        self.categories[cat] = self.categories.get(cat, 0) + 1
        self.subcategories[sub] = self.subcategories.get(sub, 0) + 1


def rank(cands, origin, quality_weight: float = 0.55):
    """(거리 가까움 × 품질)으로 정렬한다.

    거리만 보면 약국이, 품질만 보면 반대편 동네 명소가 온다. 둘을 섞는다.
    2km를 거리 점수 0점의 기준으로 삼는다 — 도보와 짧은 대중교통의 범위다.
    """
    from .routing import haversine_m

    scored = []
    for item in cands:
        p = item[0]
        d = haversine_m(*origin, p.lat, p.lon) if origin else 0.0
        near = max(0.0, 1.0 - d / 2000.0)
        score = near * (1 - quality_weight) + quality(p) * quality_weight
        scored.append((score, d, item))
    scored.sort(key=lambda t: -t[0])
    return [t[2] for t in scored]


def radius_for(hours: float) -> float:
    """쓸 수 있는 시간에 맞춘 탐색 반경.

    2시간짜리에 4km 밖 앵커를 주면 이동에만 절반을 쓴다. 반대로 6시간이면
    좀 더 멀리 나가도 된다. 반경을 고정하면 이태원과 북촌이 같은 코스를 받는다.
    """
    if hours <= 2:
        return 1300.0
    if hours <= 3:
        return 1900.0
    if hours <= 4:
        return 2600.0
    return 4000.0
