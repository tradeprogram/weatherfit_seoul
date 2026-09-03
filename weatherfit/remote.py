"""원격탐사 — 위성으로 만드는 '골목 단위 더위 지도'.

기상청은 서울에 대푯값 하나를 준다. 오늘 서울 33°C. 그런데 실제 지표는
같은 시각 같은 도시 안에서 **25.0°C부터 42.6°C까지** 벌어진다(2025-06-22
Landsat 8 실측). 아스팔트 광장과 청계천변이 같은 33°C일 리가 없다.

이 17.6도 차이는 지상 관측망으로는 못 만든다. 관측소는 서울에 몇 곳뿐이고
그 사이를 보간해 봐야 골목이 나오지 않는다. **위성은 30m 격자로 도시
전체를 한 번에 찍는다.** 이 앱이 '날씨에 맞는 곳'을 고르는 서비스인 이상,
날씨를 도시 하나로 뭉뚱그리지 않는 것이 핵심이다.

무엇을 쓰나
    Landsat 8/9 Collection 2 Level-2  `lwir11`(ST_B10) → 지표면온도(LST)
        30m. 대기보정·방사보정이 끝난 Level-2라 계수만 곱하면 섭씨가 된다.
        ST = DN × 0.00341802 + 149.0 [K]
    Sentinel-2 L2A  `B04`/`B08` → NDVI(정규화식생지수)
        10m. 그늘과 증발산의 대리 지표다. 같은 온도라도 나무가 있는 길과
        없는 길은 체감이 다르다.
    QA_PIXEL / SCL 로 구름·그림자를 마스킹하고 여러 장면의 중앙값을 합성한다.

무엇을 만드나
    서울 행정동 426개별로
      lst_c        여름 한낮 지표면온도 중앙값 (°C)
      lst_pct      서울 안에서의 백분위 (0=가장 시원, 100=가장 더움)
      ndvi         식생지수 중앙값 (-1~1)
      heat_index   열부담 0~1  = 온도 백분위와 녹지 부족을 합친 값
    → data/dong_thermal.json (426행, 저장소에 포함)

어떻게 쓰나
    폭염일에 실외를 일률적으로 빼지 않는다. 열부담이 낮은 동네의 실외는
    남기고, 높은 동네의 실외를 먼저 뺀다. 그리고 왜 그랬는지 숫자로 적는다.

    python -m weatherfit.remote build          # 위성에서 새로 만든다 (약 10분)
    python -m weatherfit.remote stats          # 만들어진 값 확인

키가 필요 없다. Microsoft Planetary Computer의 STAC과 SAS 토큰 발급이
모두 공개다. 만들어 둔 결과가 저장소에 있으므로 앱을 쓰는 데는 이
모듈이 아예 필요 없다 — 근거를 재현하고 싶을 때만 돌린다.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "dong_thermal.json"

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}"
UA = {"User-Agent": "weatherfit-seoul/1.0 (tourism course planner; "
                    "https://github.com/tradeprogram/weatherfit_seoul)"}

SEOUL_BBOX = (126.76, 37.42, 127.19, 37.70)

# Landsat Collection 2 Level-2 지표면온도 환산 계수 (USGS 문서 값)
LST_SCALE, LST_OFFSET = 0.00341802, 149.0
KELVIN = 273.15

# QA_PIXEL 비트: 1 확장구름, 3 구름, 4 구름그림자, 5 눈
QA_BAD_BITS = (1, 3, 4, 5)
# Sentinel-2 SCL: 3 구름그림자, 8 중간구름, 9 고확률구름, 10 권운, 11 눈
SCL_BAD = (0, 1, 3, 8, 9, 10, 11)


# ----------------------------------------------------------------- STAC

def _sign(collection: str, session: requests.Session) -> str:
    r = session.get(SAS.format(collection=collection), timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def search(collection: str, start: str, end: str, cloud: int,
           limit: int, session: requests.Session, extra: dict | None = None):
    """서울을 덮는 장면 목록. 구름이 적은 순으로."""
    q = {"eo:cloud_cover": {"lt": cloud}}
    q.update(extra or {})
    r = session.post(STAC, timeout=60, json={
        "collections": [collection], "bbox": list(SEOUL_BBOX),
        "datetime": f"{start}/{end}", "query": q, "limit": limit,
    })
    r.raise_for_status()
    feats = r.json().get("features", [])
    feats.sort(key=lambda f: f["properties"].get("eo:cloud_cover", 99))
    return feats


# ----------------------------------------------------------------- 래스터

def _window_read(url: str, bbox=SEOUL_BBOX):
    """서울 창만 읽는다. COG라 장면 전체를 내려받지 않아도 된다."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    with rasterio.open(url) as src:
        b = transform_bounds("EPSG:4326", src.crs, *bbox)
        win = from_bounds(*b, transform=src.transform)
        arr = src.read(1, window=win, boundless=True, fill_value=0)
        return arr, src.window_transform(win), src.crs


def _qa_mask(qa) -> "object":
    import numpy as np
    bad = np.zeros(qa.shape, dtype=bool)
    for bit in QA_BAD_BITS:
        bad |= ((qa >> bit) & 1).astype(bool)
    bad |= (qa == 0)                       # fill
    return bad


def landsat_lst(scenes, session, verbose=True):
    """장면들의 지표면온도를 구름 마스킹 후 중앙값으로 합성한다."""
    import numpy as np

    token = _sign("landsat-c2-l2", session)
    stack, ref = [], None
    for f in scenes:
        try:
            st, tr, crs = _window_read(f["assets"]["lwir11"]["href"] + "?" + token)
            qa, _, _ = _window_read(f["assets"]["qa_pixel"]["href"] + "?" + token)
        except Exception as e:
            if verbose:
                print(f"  건너뜀 {f['id']}: {type(e).__name__}", flush=True)
            continue
        if ref is None:
            ref = (tr, crs, st.shape)
        elif st.shape != ref[2]:
            continue
        c = st.astype("float32") * LST_SCALE + LST_OFFSET - KELVIN
        c[_qa_mask(qa)] = np.nan
        c[(c < -10) | (c > 70)] = np.nan
        keep = float(np.isfinite(c).mean())
        if verbose:
            print(f"  {f['id']} {f['properties']['datetime'][:10]} "
                  f"구름 {f['properties'].get('eo:cloud_cover', 0):.0f}% "
                  f"· 유효 {keep * 100:.0f}%", flush=True)
        if keep > 0.25:
            stack.append(c)
    if not stack:
        return None, None
    with np.errstate(all="ignore"):
        comp = np.nanmedian(np.stack(stack), axis=0)
    return comp, ref


def sentinel_ndvi(scenes, session, verbose=True):
    """NDVI = (NIR − RED) / (NIR + RED). 그늘과 증발산의 대리 지표."""
    import numpy as np

    token = _sign("sentinel-2-l2a", session)
    stack, ref = [], None
    for f in scenes:
        a = f["assets"]
        try:
            red, tr, crs = _window_read(a["B04"]["href"] + "?" + token)
            nir, _, _ = _window_read(a["B08"]["href"] + "?" + token)
            scl, _, _ = _window_read(a["SCL"]["href"] + "?" + token)
        except Exception as e:
            if verbose:
                print(f"  건너뜀 {f['id']}: {type(e).__name__}", flush=True)
            continue
        if ref is None:
            ref = (tr, crs, red.shape)
        elif red.shape != ref[2]:
            continue
        r_, n_ = red.astype("float32"), nir.astype("float32")
        with np.errstate(all="ignore"):
            ndvi = (n_ - r_) / (n_ + r_)
        # SCL은 20m라 10m 밴드에 맞춰 확대한다
        if scl.shape != red.shape:
            fy, fx = red.shape[0] / scl.shape[0], red.shape[1] / scl.shape[1]
            yi = (np.arange(red.shape[0]) / fy).astype(int).clip(0, scl.shape[0] - 1)
            xi = (np.arange(red.shape[1]) / fx).astype(int).clip(0, scl.shape[1] - 1)
            scl = scl[yi][:, xi]
        ndvi[np.isin(scl, SCL_BAD)] = np.nan
        ndvi[(ndvi < -1) | (ndvi > 1)] = np.nan
        keep = float(np.isfinite(ndvi).mean())
        if verbose:
            print(f"  {f['id']} {f['properties']['datetime'][:10]} "
                  f"· 유효 {keep * 100:.0f}%", flush=True)
        if keep > 0.25:
            stack.append(ndvi)
    if not stack:
        return None, None
    with np.errstate(all="ignore"):
        return np.nanmedian(np.stack(stack), axis=0), ref


# ----------------------------------------------------------------- 행정동 집계

def zonal(raster, ref, gdf):
    """행정동별 중앙값. rasterstats 없이 rasterize + 인덱스 그룹으로."""
    import numpy as np
    from rasterio.features import rasterize

    tr, crs, shape = ref
    g = gdf.to_crs(crs)
    shapes = [(geom, i + 1) for i, geom in enumerate(g.geometry)]
    zones = rasterize(shapes, out_shape=shape, transform=tr,
                      fill=0, dtype="int32")
    out: dict[str, float] = {}
    flat_z, flat_v = zones.ravel(), raster.ravel()
    ok = (flat_z > 0) & np.isfinite(flat_v)
    order = np.argsort(flat_z[ok], kind="stable")
    zs, vs = flat_z[ok][order], flat_v[ok][order]
    bounds = np.searchsorted(zs, np.arange(1, len(g) + 1), side="left")
    ends = np.searchsorted(zs, np.arange(1, len(g) + 1), side="right")
    for i, (a, b) in enumerate(zip(bounds, ends)):
        if b - a >= 5:                     # 화소 5개 미만이면 신뢰하지 않는다
            out[str(g.iloc[i]["adm_cd"])] = float(np.median(vs[a:b]))
    return out


# ----------------------------------------------------------------- 빌드

def build(years: int = 3, scenes: int = 6, verbose: bool = True) -> Path:
    import numpy as np
    from .geo import load_dong_index

    session = requests.Session()
    session.headers.update(UA)
    gdf = load_dong_index()
    if gdf is None:
        raise SystemExit("행정동 경계를 찾지 못했습니다.")

    now = time.gmtime()
    lo, hi = now.tm_year - years, now.tm_year
    # 한여름 한낮만 본다. 열섬은 이때 가장 뚜렷하고, 이 앱이 대비하려는 것도 그때다.
    windows = [(f"{y}-06-15T00:00:00Z", f"{y}-09-05T00:00:00Z")
               for y in range(lo, hi + 1)]

    if verbose:
        print(f"■ Landsat 지표면온도 ({lo}~{hi} 여름)", flush=True)
    ls = []
    for a, b in windows:
        ls += search("landsat-c2-l2", a, b, 25, scenes, session,
                     {"platform": {"in": ["landsat-8", "landsat-9"]}})
    lst, ref_l = landsat_lst(ls[:scenes * 2], session, verbose)
    if lst is None:
        raise SystemExit("쓸 만한 Landsat 장면을 찾지 못했습니다.")

    if verbose:
        print(f"■ Sentinel-2 NDVI ({lo}~{hi} 여름)", flush=True)
    s2 = []
    for a, b in windows:
        s2 += search("sentinel-2-l2a", a, b, 10, scenes, session)
    ndvi, ref_s = sentinel_ndvi(s2[:scenes], session, verbose)

    if verbose:
        print("■ 행정동 집계", flush=True)
    lst_by = zonal(lst, ref_l, gdf)
    ndvi_by = zonal(ndvi, ref_s, gdf) if ndvi is not None else {}

    temps = sorted(lst_by.values())
    rows = {}
    for _, r in gdf.iterrows():
        cd = str(r["adm_cd"])
        t = lst_by.get(cd)
        if t is None:
            continue
        pct = round(100.0 * sum(1 for x in temps if x < t) / max(len(temps), 1))
        v = ndvi_by.get(cd)
        # 열부담 = 지표온도 백분위 70% + 녹지 부족 30%
        green = 0.5 if v is None else max(0.0, min(1.0, (v + 0.1) / 0.6))
        rows[cd] = {
            "dong": r["dong"], "gu": r["gu"],
            "lst_c": round(t, 1), "lst_pct": pct,
            "ndvi": None if v is None else round(v, 3),
            "heat_index": round(0.7 * (pct / 100.0) + 0.3 * (1 - green), 3),
        }

    meta = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "landsat_scenes": [f["id"] for f in ls[:scenes * 2]],
        "sentinel_scenes": [f["id"] for f in s2[:scenes]],
        "lst_min": round(min(temps), 1), "lst_max": round(max(temps), 1),
        "dong": len(rows),
        "note": "Landsat 8/9 C2 L2 ST_B10 · Sentinel-2 L2A NDVI · "
                "여름 한낮 중앙값 합성 · 행정동 중앙값",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"meta": meta, "dong": rows}, ensure_ascii=False),
                   encoding="utf-8")
    if verbose:
        print(f"완료 {len(rows)}개 행정동 · 지표면온도 "
              f"{meta['lst_min']}~{meta['lst_max']}°C → {OUT}")
    return OUT


# ----------------------------------------------------------------- 조회

_table: dict | None = None


def table() -> dict:
    global _table
    if _table is None:
        try:
            _table = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            _table = {"meta": {}, "dong": {}}
    return _table


def reset() -> None:
    global _table
    _table = None


def heat_of(adm_cd: str | None) -> dict | None:
    """행정동의 열부담. 없으면 None — 모르면 모른다고 한다."""
    if not adm_cd:
        return None
    t = table()
    row = (t.get("dong") or {}).get(str(adm_cd))
    if not row:
        return None
    pct, hi = row["lst_pct"], row["heat_index"]
    if pct >= 75:
        label = f"서울에서 더운 편 (상위 {100 - pct}%)"
        advice = ("한낮에는 그늘이 귀한 동네예요. 실내를 사이사이 "
                  "넣어 두는 편이 좋습니다.")
    elif pct <= 30:
        label = f"서울에서 시원한 편 (하위 {pct}%)"
        advice = "지표가 시원한 편이라 한낮 야외도 견딜 만합니다."
    else:
        label = f"서울 평균 수준 (상위 {100 - pct}%)"
        advice = "무난한 편이지만 한낮에는 물을 챙기시는 게 좋아요."
    meta = t.get("meta", {})
    return {
        "adm_cd": str(adm_cd), "dong": row["dong"], "gu": row["gu"],
        "lst_c": row["lst_c"], "percentile": pct, "ndvi": row["ndvi"] or 0.0,
        "heat_index": hi, "label": label, "advice": advice,
        "source": f"Landsat 8/9 지표면온도 · {meta.get('built_at', '')[:10]} 합성",
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    ap = argparse.ArgumentParser(description="위성 열지도 생성")
    ap.add_argument("action", choices=["build", "stats"])
    ap.add_argument("--scenes", type=int, default=6)
    ap.add_argument("--years", type=int, default=3)
    a = ap.parse_args()

    if a.action == "build":
        build(years=a.years, scenes=a.scenes)
        return

    t = table()
    rows = list((t.get("dong") or {}).values())
    if not rows:
        raise SystemExit("아직 만들지 않았습니다. python -m weatherfit.remote build")
    m = t["meta"]
    print(f"{m.get('dong')}개 행정동 · 지표면온도 {m.get('lst_min')}~"
          f"{m.get('lst_max')}°C · {m.get('built_at', '')[:10]} 합성")
    rows.sort(key=lambda r: -r["lst_c"])
    print("\n가장 더운 곳")
    for r in rows[:8]:
        print(f"  {r['gu']} {r['dong']:12} {r['lst_c']:5.1f}°C  NDVI {r['ndvi']}")
    print("\n가장 시원한 곳")
    for r in rows[-8:]:
        print(f"  {r['gu']} {r['dong']:12} {r['lst_c']:5.1f}°C  NDVI {r['ndvi']}")


if __name__ == "__main__":
    main()
