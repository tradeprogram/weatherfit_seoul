"""서울 행정동 경계 처리.

원본은 통계청 행정동 경계(BND_ADM_DONG_PG, EPSG:5186 중부원점). 전국 3,559개에서
서울 426개만 뽑아 WGS84로 변환하고, 웹 지도에서 쓸 크기로 단순화한다.

용도는 두 가지다.
  1. 지도에 자치구·행정동 경계를 그린다
  2. 추천 결과가 어느 동에 떨어졌는지 집계한다 (관광 분산 지표)
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DATA = ROOT / "web" / "data"
DONG_GEOJSON = WEB_DATA / "seoul_dong.geojson"

SEOUL_SIDO = "11"

# SGIS 행정동코드 앞 5자리 → 자치구
GU_BY_PREFIX = {
    "11010": "종로구", "11020": "중구", "11030": "용산구", "11040": "성동구",
    "11050": "광진구", "11060": "동대문구", "11070": "중랑구", "11080": "성북구",
    "11090": "강북구", "11100": "도봉구", "11110": "노원구", "11120": "은평구",
    "11130": "서대문구", "11140": "마포구", "11150": "양천구", "11160": "강서구",
    "11170": "구로구", "11180": "금천구", "11190": "영등포구", "11200": "동작구",
    "11210": "관악구", "11220": "서초구", "11230": "강남구", "11240": "송파구",
    "11250": "강동구",
}


def build_seoul_geojson(shp_path: str | Path, out: Path = DONG_GEOJSON,
                        tolerance: float = 0.00006) -> Path:
    """전국 행정동 shapefile → 서울만 담은 WGS84 GeoJSON."""
    import geopandas as gpd

    gdf = gpd.read_file(str(shp_path), encoding="cp949")
    seoul = gdf[gdf["ADM_CD"].str.startswith(SEOUL_SIDO)].copy()
    if seoul.empty:
        raise ValueError("서울(ADM_CD 11xxxxxx) 행정동을 찾지 못했습니다")

    seoul["gu"] = seoul["ADM_CD"].str[:5].map(GU_BY_PREFIX)
    missing = seoul["gu"].isna().sum()
    if missing:
        raise ValueError(f"자치구를 매핑하지 못한 행정동 {missing}건")

    seoul = seoul.to_crs(epsg=4326)
    # 웹 전송량을 줄인다. 6e-5도 ≈ 6m라 화면에서는 차이가 보이지 않는다
    seoul["geometry"] = seoul.geometry.simplify(tolerance, preserve_topology=True)

    seoul = seoul.rename(columns={"ADM_CD": "adm_cd", "ADM_NM": "dong"})
    seoul = seoul[["adm_cd", "dong", "gu", "geometry"]]

    out.parent.mkdir(parents=True, exist_ok=True)
    seoul.to_file(out, driver="GeoJSON")
    return out


def load_dong_index(path: Path = DONG_GEOJSON):
    """좌표 → 행정동 조회용 인덱스. geopandas가 있을 때만 쓴다."""
    import geopandas as gpd

    return gpd.read_file(path)


def assign_dong(items, index=None) -> dict[str, tuple[str, str]]:
    """콘텐츠 좌표를 행정동에 매칭한다. {cid: (자치구, 행정동)}"""
    import geopandas as gpd
    from shapely.geometry import Point

    if index is None:
        index = load_dong_index()

    located = [i for i in items if i.lat and i.lon]
    if not located:
        return {}

    pts = gpd.GeoDataFrame(
        {"cid": [i.cid for i in located]},
        geometry=[Point(i.lon, i.lat) for i in located],
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(pts, index[["gu", "dong", "geometry"]],
                       how="left", predicate="within")
    return {
        r.cid: (r.gu, r.dong)
        for r in joined.itertuples()
        if isinstance(r.gu, str)
    }
