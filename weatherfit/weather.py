"""기상 정보 어댑터.

기상청 단기예보 API(초단기실황)를 쓴다. 키가 없으면 서비스가 멈추는 대신
`source="fallback"`으로 표시된 온화한 기본값을 돌려준다. 데모에서는
`Weather.override(...)`로 임의의 날씨를 주입해 우천 시나리오를 보여줄 수 있다.

키 발급: https://www.data.go.kr — 기상청_단기예보 조회서비스
    환경변수 KMA_API_KEY (디코딩된 일반 인증키)
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta

import requests

from .validate import Weather

KMA_URL = ("https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
           "/getUltraSrtNcst")

# 기상청 격자(LCC) 변환 상수
_RE, _GRID = 6371.00877, 5.0
_SLAT1, _SLAT2 = 30.0, 60.0
_OLON, _OLAT = 126.0, 38.0
_XO, _YO = 43, 136

SEOUL_CITY_HALL = (37.5665, 126.9780)


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도 → 기상청 격자 좌표 (nx, ny)."""
    d2r = math.pi / 180.0
    re = _RE / _GRID
    slat1, slat2 = _SLAT1 * d2r, _SLAT2 * d2r
    olon, olat = _OLON * d2r, _OLAT * d2r

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = (sf**sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / (ro**sn)

    ra = math.tan(math.pi * 0.25 + lat * d2r * 0.5)
    ra = re * sf / (ra**sn)
    theta = lon * d2r - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    nx = int(ra * math.sin(theta) + _XO + 0.5)
    ny = int(ro - ra * math.cos(theta) + _YO + 0.5)
    return nx, ny


def _base_datetime(now: datetime) -> tuple[str, str]:
    """초단기실황은 매시 정시 생성, 40분경 제공. 안전하게 한 시간 전을 쓴다."""
    t = now - timedelta(hours=1) if now.minute < 45 else now
    return t.strftime("%Y%m%d"), t.strftime("%H00")


def _sky_label(code: str) -> str:
    return {"1": "맑음", "3": "구름많음", "4": "흐림"}.get(code, "맑음")


def _pty_label(code: str) -> str:
    return {"0": "없음", "1": "비", "2": "비/눈", "3": "눈",
            "5": "빗방울", "6": "빗방울눈날림", "7": "눈날림"}.get(code, "없음")


def get_weather(lat: float = SEOUL_CITY_HALL[0], lon: float = SEOUL_CITY_HALL[1],
                now: datetime | None = None, api_key: str | None = None) -> Weather:
    """현재 기상 상태. 키가 없거나 호출이 실패하면 fallback 값을 돌려준다."""
    now = now or datetime.now()
    key = api_key or os.environ.get("KMA_API_KEY", "")
    if not key:
        w = Weather(temp_c=21.0, precip_mm=0.0, sky="맑음", pty="없음")
        w.source = "fallback"
        w.note = "KMA_API_KEY 미설정 — 기본값(맑음 21°C)으로 판정합니다"
        return w

    nx, ny = latlon_to_grid(lat, lon)
    base_date, base_time = _base_datetime(now)
    try:
        r = requests.get(KMA_URL, timeout=10, params={
            "serviceKey": key, "dataType": "JSON", "numOfRows": 100,
            "pageNo": 1, "base_date": base_date, "base_time": base_time,
            "nx": nx, "ny": ny,
        })
        r.raise_for_status()
        items = r.json()["response"]["body"]["items"]["item"]
    except Exception as e:                       # 기상 실패로 서비스를 멈추지 않는다
        w = Weather(temp_c=21.0, precip_mm=0.0, sky="맑음", pty="없음")
        w.source = "fallback"
        w.note = f"기상청 호출 실패 — 기본값으로 판정합니다 ({type(e).__name__})"
        return w

    vals = {i["category"]: i["obsrValue"] for i in items}
    try:
        precip = float(str(vals.get("RN1", "0")).replace("강수없음", "0") or 0)
    except ValueError:
        precip = 0.0

    w = Weather(
        temp_c=float(vals.get("T1H", 21.0)),
        precip_mm=precip,
        sky=_sky_label(str(vals.get("SKY", "1"))),
        pty=_pty_label(str(vals.get("PTY", "0"))),
    )
    w.source = "kma"
    w.note = f"기상청 초단기실황 {base_date} {base_time} (격자 {nx},{ny})"
    return w
