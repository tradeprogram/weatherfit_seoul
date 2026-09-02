"""경로 소요시간 — 직선거리 추정에서 실제 경로 API로.

직선거리를 4km/h로 나눈 값은 '대충 이 정도'일 뿐이다. 한강을 건너거나
고가·철길에 막히면 실제 도보 시간은 두 배가 되기도 한다. 관광객에게
"도보 4분"이라고 말하려면 실제 경로를 물어야 한다.

제공자를 갈아끼울 수 있게 어댑터로 묶었다. 키가 하나도 없어도 동작하며,
어떤 방식으로 잰 값인지는 결과의 provider에 항상 실린다.

    walk      TMAP 보행자 경로      TMAP_APP_KEY
    transit   ODsay 대중교통        ODSAY_API_KEY
    drive     네이버 Directions 5   NAVER_CLIENT_ID / NAVER_CLIENT_SECRET
    (없으면)  직선거리 추정         키 불필요

네이버 지도 API에는 보행자·대중교통 경로가 공개돼 있지 않아 자동차만 맡는다.
도보는 TMAP, 대중교통은 ODsay가 국내에서 가장 널리 쓰이는 조합이다.
"""
from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, field

import requests

WALK_M_PER_MIN = 67.0            # 도보 4km/h
DETOUR = 1.30                    # 직선 대비 실제 보행 경로가 길어지는 비율
WALKABLE_M = 900                 # 이보다 가까우면 대중교통이 오히려 손해다


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class Leg:
    """한 구간의 이동."""
    mode: str                      # walk | transit | drive
    minutes: int
    distance_m: int
    provider: str                  # tmap | odsay | naver | estimate
    summary: str = ""              # 예: "2호선 3정거장 · 환승 1회"
    steps: list[str] = field(default_factory=list)
    exact: bool = False            # 실제 경로 API로 잰 값인가

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------- 추정 (키 불필요)

def estimate_walk(o: tuple[float, float], d: tuple[float, float]) -> Leg:
    straight = haversine_m(*o, *d)
    real = straight * DETOUR
    return Leg(
        mode="walk",
        minutes=max(1, round(real / WALK_M_PER_MIN)),
        distance_m=round(real),
        provider="estimate",
        summary=f"직선 {round(straight)}m에 우회율 {DETOUR:g}배 적용",
        exact=False,
    )


def estimate_transit(o: tuple[float, float], d: tuple[float, float]) -> Leg:
    """대중교통 추정. 대기와 환승을 감안해 기본 6분에 거리분을 더한다."""
    straight = haversine_m(*o, *d)
    return Leg(
        mode="transit",
        minutes=max(6, 6 + round(straight / 300.0)),
        distance_m=round(straight),
        provider="estimate",
        summary="평균 이동속도 기반 추정",
        exact=False,
    )


# ----------------------------------------------------------------- 실제 경로 API

class Routing:
    """키가 있으면 실제 경로 API를, 없으면 추정을 쓴다."""

    def __init__(self, timeout: int = 8):
        self.timeout = timeout
        self.tmap = os.environ.get("TMAP_APP_KEY", "")
        self.odsay = os.environ.get("ODSAY_API_KEY", "")
        self.naver_id = os.environ.get("NAVER_CLIENT_ID", "")
        self.naver_secret = os.environ.get("NAVER_CLIENT_SECRET", "")
        self._cache: dict[tuple, Leg] = {}

    @property
    def providers(self) -> dict[str, str]:
        return {
            "walk": "tmap" if self.tmap else "estimate",
            "transit": "odsay" if self.odsay else "estimate",
            "drive": "naver" if (self.naver_id and self.naver_secret) else "estimate",
        }

    @staticmethod
    def _key(mode: str, o: tuple[float, float], d: tuple[float, float]) -> tuple:
        return (mode, round(o[0], 5), round(o[1], 5), round(d[0], 5), round(d[1], 5))

    # ---------- 도보: TMAP 보행자 경로 ----------

    def walk(self, o: tuple[float, float], d: tuple[float, float]) -> Leg:
        key = self._key("walk", o, d)
        if key in self._cache:
            return self._cache[key]

        leg = estimate_walk(o, d)
        if self.tmap:
            try:
                r = requests.post(
                    "https://apis.openapi.sk.com/tmap/routes/pedestrian",
                    params={"version": 1},
                    headers={"appKey": self.tmap,
                             "Content-Type": "application/json"},
                    json={
                        "startX": o[1], "startY": o[0],
                        "endX": d[1], "endY": d[0],
                        "startName": "출발", "endName": "도착",
                        "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO",
                    },
                    timeout=self.timeout,
                )
                r.raise_for_status()
                feats = r.json()["features"]
                props = feats[0]["properties"]
                names = [f["properties"].get("name", "") for f in feats[:12]
                         if f["properties"].get("name")]
                leg = Leg(
                    mode="walk",
                    minutes=max(1, round(int(props["totalTime"]) / 60)),
                    distance_m=int(props["totalDistance"]),
                    provider="tmap",
                    summary="TMAP 보행자 경로",
                    steps=names[:6],
                    exact=True,
                )
            except Exception:
                pass                          # 추정값을 그대로 쓴다
        self._cache[key] = leg
        return leg

    # ---------- 대중교통: ODsay ----------

    def transit(self, o: tuple[float, float], d: tuple[float, float]) -> Leg:
        key = self._key("transit", o, d)
        if key in self._cache:
            return self._cache[key]

        leg = estimate_transit(o, d)
        if self.odsay:
            try:
                r = requests.get(
                    "https://api.odsay.com/v1/api/searchPubTransPathT",
                    params={"SX": o[1], "SY": o[0], "EX": d[1], "EY": d[0],
                            "apiKey": self.odsay, "OPT": 0},
                    timeout=self.timeout,
                )
                r.raise_for_status()
                path = r.json()["result"]["path"][0]
                info = path["info"]
                lines: list[str] = []
                for sp in path.get("subPath", []):
                    lane = (sp.get("lane") or [{}])[0]
                    stops = sp.get("stationCount", 0)
                    if sp.get("trafficType") == 1:
                        lines.append(f"{lane.get('name', '지하철')} {stops}정거장")
                    elif sp.get("trafficType") == 2:
                        lines.append(f"{lane.get('busNo', '버스')} {stops}정거장")
                transfers = max(0, len(lines) - 1)
                summary = " · ".join(lines[:3])
                if transfers:
                    summary += f" · 환승 {transfers}회"
                leg = Leg(
                    mode="transit",
                    minutes=int(info["totalTime"]),
                    distance_m=int(info.get("totalDistance", 0)),
                    provider="odsay",
                    summary=summary or "대중교통",
                    steps=lines,
                    exact=True,
                )
            except Exception:
                pass
        self._cache[key] = leg
        return leg

    # ---------- 자동차: 네이버 Directions 5 ----------

    def drive(self, o: tuple[float, float], d: tuple[float, float]) -> Leg:
        key = self._key("drive", o, d)
        if key in self._cache:
            return self._cache[key]

        straight = haversine_m(*o, *d)
        leg = Leg(mode="drive", minutes=max(3, round(straight / 400)),
                  distance_m=round(straight * 1.4), provider="estimate",
                  summary="직선거리 기반 추정", exact=False)
        if self.naver_id and self.naver_secret:
            try:
                r = requests.get(
                    "https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving",
                    params={"start": f"{o[1]},{o[0]}", "goal": f"{d[1]},{d[0]}",
                            "option": "traoptimal"},
                    headers={"X-NCP-APIGW-API-KEY-ID": self.naver_id,
                             "X-NCP-APIGW-API-KEY": self.naver_secret},
                    timeout=self.timeout,
                )
                r.raise_for_status()
                s = r.json()["route"]["traoptimal"][0]["summary"]
                leg = Leg(mode="drive",
                          minutes=max(1, round(int(s["duration"]) / 60000)),
                          distance_m=int(s["distance"]),
                          provider="naver",
                          summary="네이버 실시간 최적경로",
                          exact=True)
            except Exception:
                pass
        self._cache[key] = leg
        return leg

    # ---------- 구간에 맞는 수단 고르기 ----------

    def best(self, o: tuple[float, float], d: tuple[float, float]) -> dict:
        """도보와 대중교통을 함께 재고 권장 수단을 정한다.

        가까우면 걷는 편이 빠르다. 대중교통은 대기와 환승 때문에 짧은
        거리에서 오히려 손해라, 도보권이면 아예 묻지 않는다.
        """
        walk = self.walk(o, d)
        if walk.distance_m <= WALKABLE_M:
            return {"recommended": "walk", "walk": walk.to_dict(), "transit": None}
        transit = self.transit(o, d)
        rec = "walk" if walk.minutes <= transit.minutes else "transit"
        return {"recommended": rec,
                "walk": walk.to_dict(), "transit": transit.to_dict()}


_router: Routing | None = None


def router() -> Routing:
    global _router
    if _router is None:
        _router = Routing()
    return _router
