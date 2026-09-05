"""지금 붐비는가 — 서울시 실시간 도시데이터.

트렌드는 '요즘 뜨는가'를 말하고 이건 '지금 붐비는가'를 말한다. 둘은
다른 질문이고, 우리 서비스가 하려는 말은 그 교차점에 있다.

    뜨는데 아직 안 붐빈다      지금이 적기다
    이미 붐빈다               지금은 가지 마세요

지금까지 이 판단의 근거가 없었다. 가진 것이 작년 대비 추세뿐이라
"성수동이 +64.6%"까지는 말해도 "지금 성수동이 붐비는가"는 몰랐고,
추세는 오히려 사람을 더 보내는 신호다. 그래서 「지금 가지 마세요」는
주장일 뿐 기능이 아니었다.

서울시 실시간 도시데이터가 그 자리를 메운다.

    121곳 · 5~10분 갱신 · 12시간 예보
    혼잡도 4단계 · 실시간 인구 범위 · 거주/비거주 비율 · 연령 분포
    우리 콘텐츠 3,697건 중 2,851건(77.1%)이 반경 800m 안에 든다

**비거주 비율이 특히 값지다.** 관광객이 몰린 정도를 직접 잰다 —
붐비는 것이 출퇴근 인파인지 관광 인파인지 갈라 준다.

    python -m weatherfit.crowd areas    # 관측 지역 목록 갱신
    python -m weatherfit.crowd now      # 지금 혼잡한 곳
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
AREAS = ROOT / "data" / "crowd_areas.json"

LIST_URL = "https://data.seoul.go.kr/SeoulRtd/api/hotspot"
LIVE_URL = "http://openapi.seoul.go.kr:8088/{key}/json/citydata_ppltn/1/5/{area}"
UA = {"User-Agent": "weatherfit-seoul/1.0", "Referer": "https://data.seoul.go.kr/SeoulRtd/"}

NEAR_M = 800          # 관측 지역이 대표하는 반경. 이보다 멀면 다른 동네다.
TTL = 300             # 5분. 원자료가 그 주기로 갱신된다.

LEVELS = ("여유", "보통", "약간 붐빔", "붐빔")
CROWDED = ("약간 붐빔", "붐빔")


def fetch_areas(verbose: bool = True) -> Path:
    """관측 지역 목록과 좌표. 자주 바뀌지 않아 저장해 두고 쓴다."""
    r = requests.get(LIST_URL, params={"hotspotNm": ""}, headers=UA, timeout=60)
    r.raise_for_status()
    rows = r.json().get("row") or []
    out = []
    for x in rows:
        try:
            # x가 위도, y가 경도다. 이름이 뒤바뀌어 들어 있다.
            out.append({"name": x["area_nm"], "category": x.get("category", ""),
                        "lat": float(x["x"]), "lon": float(x["y"])})
        except (KeyError, TypeError, ValueError):
            continue
    AREAS.parent.mkdir(parents=True, exist_ok=True)
    AREAS.write_text(json.dumps(
        {"meta": {"source": "서울시 실시간 도시데이터",
                  "built_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                  "count": len(out), "near_m": NEAR_M},
         "areas": out}, ensure_ascii=False), encoding="utf-8")
    if verbose:
        print(f"관측 지역 {len(out)}곳 → {AREAS}")
    return AREAS


_areas: list | None = None


def areas() -> list:
    global _areas
    if _areas is None:
        try:
            _areas = json.loads(AREAS.read_text(encoding="utf-8"))["areas"]
        except Exception:
            _areas = []
    return _areas


def nearest(lat: float, lon: float) -> tuple[dict | None, float]:
    """가장 가까운 관측 지역과 거리. 멀면 None — 남의 동네 혼잡을
    이 자리의 혼잡이라고 말하면 안 된다."""
    from .routing import haversine_m

    best, bd = None, 1e12
    for a in areas():
        d = haversine_m(lat, lon, a["lat"], a["lon"])
        if d < bd:
            best, bd = a, d
    return (best, bd) if best and bd <= NEAR_M else (None, bd)


_live: dict = {}          # 지역명 → (받은 시각, 값)


def live(name: str) -> dict | None:
    """한 지역의 지금 혼잡도. 키가 없으면 None을 준다 — 모르면 모른다고 한다."""
    key = os.environ.get("SEOUL_RTD_KEY", "")
    if not key:
        return None
    hit = _live.get(name)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        r = requests.get(LIVE_URL.format(key=key, area=requests.utils.quote(name)),
                         timeout=20)
        r.raise_for_status()
        rows = r.json().get("SeoulRtd.citydata_ppltn") or []
        if not rows:
            return None
        x = rows[0]
        got = {
            "area": x.get("AREA_NM", name),
            "level": x.get("AREA_CONGEST_LVL", ""),
            "message": x.get("AREA_CONGEST_MSG", ""),
            "min": _int(x.get("AREA_PPLTN_MIN")),
            "max": _int(x.get("AREA_PPLTN_MAX")),
            # 붐비는 것이 출퇴근 인파인지 관광 인파인지 가른다
            "visitor_rate": _float(x.get("NON_RESNT_PPLTN_RATE")),
            "at": x.get("PPLTN_TIME", ""),
            "forecast": [
                {"at": f.get("FCST_TIME", ""), "level": f.get("FCST_CONGEST_LVL", ""),
                 "min": _int(f.get("FCST_PPLTN_MIN")), "max": _int(f.get("FCST_PPLTN_MAX"))}
                for f in (x.get("FCST_PPLTN") or [])[:12]],
        }
    except Exception:
        return None
    _live[name] = (time.time(), got)
    return got


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def at(lat: float, lon: float) -> dict | None:
    """이 좌표의 지금 혼잡. 가까운 관측 지역이 없으면 None."""
    a, _ = nearest(lat, lon)
    return live(a["name"]) if a else None


def is_crowded(got: dict | None) -> bool:
    return bool(got) and got.get("level") in CROWDED


def relief(got: dict | None) -> dict | None:
    """언제쯤 한산해지는가. 예보에서 처음으로 여유해지는 시각을 찾는다.

    '지금 가지 마세요'로 끝내면 갈 곳을 잃는다. 시간을 옮길 수 있으면
    옮겨 준다 — 그게 분산이고, 이 서비스가 하려는 일이다.
    """
    if not is_crowded(got):
        return None
    for f in got.get("forecast") or []:
        if f.get("level") not in CROWDED:
            return f
    return None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    ap = argparse.ArgumentParser(description="실시간 혼잡")
    ap.add_argument("action", choices=["areas", "now"])
    a = ap.parse_args()

    if a.action == "areas":
        fetch_areas()
        return

    r = requests.get(LIST_URL, params={"hotspotNm": ""}, headers=UA, timeout=60)
    rows = r.json().get("row") or []
    import collections
    print(f"관측 {len(rows)}곳 · "
          f"{dict(collections.Counter(x['area_congest_lvl'] for x in rows))}")
    hot = [x for x in rows if x["area_congest_lvl"] in CROWDED]
    print(f"\n지금 붐비는 곳 {len(hot)}곳")
    for x in hot:
        print(f"  {x['area_nm']:18} {x['area_congest_lvl']:6} {x.get('category','')}")


if __name__ == "__main__":
    main()
