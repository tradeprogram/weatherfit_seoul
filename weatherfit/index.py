"""적재 시점에 한 번만 계산하는 인덱스.

운영시간 정규화와 실내외 태깅은 콘텐츠가 바뀌지 않는 한 결과가 같다.
그런데 요청마다 3,788건을 다시 파싱하면 판정 한 번에 120ms가 든다.
사용자가 날씨 버튼을 누를 때마다 그만큼 기다리는 셈이다.

여기서 미리 계산해 두면 판정은 사전 조회로 끝난다. 행정동 매칭도
좌표가 고정이라 한 번이면 된다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .models import Content
from .normalize import OpeningHours, parse_hours, tag_environment


@dataclass
class Place:
    """콘텐츠 + 미리 계산해 둔 판정 재료."""
    content: Content
    hours: OpeningHours
    environment: str                 # indoor | outdoor | unknown
    env_reason: str
    gu: str = ""
    dong: str = ""

    # 자주 쓰는 것들을 끌어올려 둔다
    lat: float | None = None
    lon: float | None = None

    def __post_init__(self):
        self.lat = self.content.lat
        self.lon = self.content.lon

    @property
    def cid(self) -> str:
        return self.content.cid

    @property
    def title(self) -> str:
        return self.content.title


@dataclass
class Index:
    places: list[Place] = field(default_factory=list)
    by_cid: dict[str, Place] = field(default_factory=dict)
    built_at: str = ""
    build_ms: int = 0
    located: int = 0                 # 좌표가 있는 건수
    dong_matched: int = 0

    def __len__(self) -> int:
        return len(self.places)


def build_index(items: list[Content], dong_gdf=None) -> Index:
    """콘텐츠 목록 → 판정 준비가 끝난 인덱스."""
    t0 = time.time()
    places = [
        Place(
            content=it,
            hours=parse_hours(it.use_time_raw, it.closed_days_raw),
            **dict(zip(("environment", "env_reason"),
                       tag_environment(it.category, it.title,
                                       it.description, it.tags))),
        )
        for it in items
    ]

    idx = Index(
        places=places,
        by_cid={p.cid: p for p in places},
        built_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        located=sum(1 for p in places if p.lat and p.lon),
    )

    if dong_gdf is not None:
        _attach_dong(idx, dong_gdf)

    idx.build_ms = int((time.time() - t0) * 1000)
    return idx


def _attach_dong(idx: Index, dong_gdf) -> None:
    """좌표 → 행정동. 한 번의 공간 조인으로 전부 채운다."""
    try:
        import geopandas as gpd
        from shapely.geometry import Point
    except ImportError:
        return

    located = [p for p in idx.places if p.lat and p.lon]
    if not located:
        return

    pts = gpd.GeoDataFrame(
        {"cid": [p.cid for p in located]},
        geometry=[Point(p.lon, p.lat) for p in located],
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(pts, dong_gdf[["gu", "dong", "geometry"]],
                       how="left", predicate="within")
    for row in joined.itertuples():
        if isinstance(row.gu, str):
            p = idx.by_cid.get(row.cid)
            if p:
                p.gu, p.dong = row.gu, row.dong
                idx.dong_matched += 1
