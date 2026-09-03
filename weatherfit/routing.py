"""경로 소요시간 — 직선거리 추정에서 실제 경로 API로.

직선거리를 4km/h로 나눈 값은 '대충 이 정도'일 뿐이다. 한강을 건너거나
고가·철길에 막히면 실제 도보 시간은 두 배가 되기도 한다. 관광객에게
"도보 4분"이라고 말하려면 실제 경로를 물어야 한다.

제공자를 갈아끼울 수 있게 어댑터로 묶었다. 키가 하나도 없어도 동작하며,
어떤 방식으로 잰 값인지는 결과의 provider에 항상 실린다.

    walk      TMAP 보행자 경로      TMAP_APP_KEY
    transit   ODsay 대중교통        ODSAY_API_KEY
    drive     네이버 Directions 5   NAVER_CLIENT_ID / NAVER_CLIENT_SECRET
    walk      OSRM 보행 프로파일    키 불필요 (TMAP이 없을 때)
    (그래도 안 되면) 직선거리 추정   키 불필요

네이버 지도 API에는 보행자·대중교통 경로가 공개돼 있지 않아 자동차만 맡는다.
도보는 TMAP, 대중교통은 ODsay가 국내에서 가장 널리 쓰이는 조합이다.

키가 없는 사람에게도 도보만은 실측을 준다. OSM 도로망 위에서 도는 공개
OSRM 보행 프로파일이 있어서다. 덕수궁→명동성당을 직선 추정은 1,430m/21분,
OSRM은 1,443m/19분으로 답한다. 값은 비슷해도 하나는 잰 값이고 하나는
가정한 값이다 — 그 차이를 화면에 그대로 적는다.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import asdict, dataclass, field

import requests

WALK_M_PER_MIN = 67.0            # 도보 4km/h
DETOUR = 1.30                    # 직선 대비 실제 보행 경로가 길어지는 비율
WALKABLE_M = 900                 # 이보다 가까우면 대중교통이 오히려 손해다

# 공개 OSRM 보행 프로파일. 예의상 UA를 밝히고, 캐시로 호출을 줄인다.
OSRM_FOOT = ("https://routing.openstreetmap.de/routed-foot"
             "/route/v1/foot/{lon1},{lat1};{lon2},{lat2}")
OSRM_TABLE = "https://routing.openstreetmap.de/routed-foot/table/v1/foot/{coords}"
TABLE_MAX = 90                   # 공개 서버의 한 번 요청 상한(100)에서 여유를 둔다
OSRM_UA = {"User-Agent": "weatherfit-seoul/1.0 (tourism course planner; "
                         "https://github.com/tradeprogram/weatherfit_seoul)"}
OSRM_TIMEOUT = 4                 # 느리면 추정으로 넘어간다. 화면을 붙잡지 않는다
OSRM_COOLDOWN = 300              # 막힌 뒤 다시 시도하기까지
CACHE_MAX = 20000                # 구간 캐시 상한. 오래 켜 두면 계속 쌓인다


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

    def __init__(self, timeout: int = 8, offline: bool = False):
        self.timeout = timeout
        # offline이면 어떤 경로 API도 부르지 않고 추정만 낸다.
        # 테스트가 공개 서버에 매달리면 느려지고, 서버가 흔들리면 빨개진다.
        self.offline = offline
        self.tmap = os.environ.get("TMAP_APP_KEY", "")
        self.odsay = os.environ.get("ODSAY_API_KEY", "")
        # ODsay는 등록된 도메인에서 온 호출만 받는데, 그 판정을 Referer로 한다.
        # 서버에서 requests로 부르면 Referer가 아예 없어서, 도메인을 제대로
        # 등록해도 ApiKeyAuthFailed가 난다. 우리가 어느 서비스인지 밝힌다.
        self.odsay_referer = os.environ.get(
            "ODSAY_REFERER", "https://weatherfit-seoul.vercel.app")
        self.naver_id = os.environ.get("NAVER_CLIENT_ID", "")
        self.naver_secret = os.environ.get("NAVER_CLIENT_SECRET", "")
        self._cache: dict[tuple, Leg] = {}
        self._osrm_down = 0.0         # 공개 서버가 막히면 이 시각까지 묻지 않는다
        self._matrix: dict[tuple, list] = {}   # 거리 매트릭스 캐시

    @property
    def providers(self) -> dict[str, str]:
        return {
            "walk": ("estimate" if self.offline
                     else "tmap" if self.tmap else "osrm"),
            "transit": "odsay" if self.odsay else "estimate",
            "drive": "naver" if (self.naver_id and self.naver_secret) else "estimate",
        }

    @staticmethod
    def _key(mode: str, o: tuple[float, float], d: tuple[float, float]) -> tuple:
        return (mode, round(o[0], 5), round(o[1], 5), round(d[0], 5), round(d[1], 5))

    def _remember(self, key: tuple, leg: Leg) -> Leg:
        """구간 캐시. 오래 켜 두면 무한정 늘어나므로 상한을 둔다."""
        if len(self._cache) >= CACHE_MAX:
            for k in list(self._cache)[:CACHE_MAX // 4]:
                del self._cache[k]
        self._cache[key] = leg
        return leg

    # ---------- 도보: TMAP 보행자 경로 ----------

    def walk(self, o: tuple[float, float], d: tuple[float, float],
             measure: bool = True) -> Leg:
        key = self._key("walk", o, d)
        if key in self._cache:
            return self._cache[key]

        leg = estimate_walk(o, d)
        if not measure or self.offline:
            return leg                    # 캐시에 넣지 않는다 — 추정은 임시값이다
        if not self.tmap:
            leg = self._osrm_walk(o, d) or leg
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
        return self._remember(key, leg)

    def walk_matrix(self, origin: tuple[float, float],
                    dests: list[tuple[float, float]]) -> list[float | None]:
        """출발지에서 여러 목적지까지의 **실제 보행 거리(m)**. 못 재면 None.

        후보를 고를 때 직선거리를 쓰면 한강 건너편이나 철길 반대편이
        '가까운 곳'으로 올라온다. 지도상 900m인데 걸어서 2.4km인 구간이
        서울에 흔하다. 그렇다고 후보마다 경로를 물으면 수백 번을 부르게
        되니, OSRM의 table 서비스로 한 번에 받는다.
        """
        if self.offline or not dests:
            return [None] * len(dests)
        if self._osrm_down and time.time() < self._osrm_down:
            return [None] * len(dests)

        out: list[float | None] = []
        for i in range(0, len(dests), TABLE_MAX):
            chunk = dests[i:i + TABLE_MAX]
            key = ("table", round(origin[0], 5), round(origin[1], 5),
                   tuple((round(a, 5), round(b, 5)) for a, b in chunk))
            if key in self._matrix:
                out.extend(self._matrix[key])
                continue
            coords = ";".join(f"{lon},{lat}" for lat, lon in [origin] + chunk)
            try:
                r = requests.get(
                    OSRM_TABLE.format(coords=coords), headers=OSRM_UA,
                    params={"sources": "0", "annotations": "distance"},
                    timeout=OSRM_TIMEOUT + 6,
                )
                r.raise_for_status()
                row = r.json()["distances"][0][1:]
            except Exception:
                self._osrm_down = time.time() + OSRM_COOLDOWN
                return out + [None] * (len(dests) - len(out))
            row = [float(v) if v is not None else None for v in row]
            if len(self._matrix) > 400:
                self._matrix.clear()
            self._matrix[key] = row
            out.extend(row)
        return out

    def measure_many(self, pairs: list[tuple]) -> list[dict]:
        """여러 구간을 동시에 실측한다. 순서는 그대로 돌려준다.

        일정 한 개의 구간은 서넛뿐이라 한 번의 왕복으로 끝난다.
        하나씩 물으면 구간마다 0.7초씩 쌓여 화면이 눈에 띄게 느려진다.
        """
        if not pairs:
            return []
        # 이미 다 재 둔 구간이면 스레드를 띄우지 않는다. 풀을 만들고 접는
        # 것만으로 수십 ms가 드는데, 캐시 조회는 마이크로초짜리다.
        hit = [self._cached_best(o, d) for o, d in pairs]
        if all(h is not None for h in hit):
            return hit

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(6, len(pairs))) as ex:
            return list(ex.map(lambda p: self.best(p[0], p[1]), pairs))

    def _cached_best(self, o, d) -> dict | None:
        """캐시만으로 답할 수 있으면 답하고, 아니면 None."""
        walk = self._cache.get(self._key("walk", o, d))
        if walk is None:
            return None
        if walk.distance_m <= WALKABLE_M:
            return {"recommended": "walk", "walk": walk.to_dict(),
                    "transit": None}
        transit = self._cache.get(self._key("transit", o, d))
        if transit is None:
            return None
        rec = "walk" if walk.minutes <= transit.minutes else "transit"
        return {"recommended": rec, "walk": walk.to_dict(),
                "transit": transit.to_dict()}

    def _osrm_walk(self, o, d) -> "Leg | None":
        """키 없이 쓰는 실측 도보. 실패하면 None — 호출한 쪽이 추정을 쓴다."""
        if self._osrm_down and time.time() < self._osrm_down:
            return None
        try:
            r = requests.get(
                OSRM_FOOT.format(lat1=o[0], lon1=o[1], lat2=d[0], lon2=d[1]),
                params={"overview": "false"}, headers=OSRM_UA,
                timeout=OSRM_TIMEOUT,
            )
            r.raise_for_status()
            rt = r.json()["routes"][0]
        except Exception:
            # 한 번 막히면 대개 그 뒤로도 막힌다. 요청마다 4초씩 기다릴 이유가
            # 없다. 다만 일시적인 장애일 수도 있으니 5분 뒤에 다시 시도한다.
            self._osrm_down = time.time() + OSRM_COOLDOWN
            return None
        return Leg(
            mode="walk",
            minutes=max(1, round(rt["duration"] / 60)),
            distance_m=round(rt["distance"]),
            provider="osrm",
            summary="OSM 도로망 보행 경로",
            exact=True,
        )

    # ---------- 대중교통: ODsay ----------

    def transit(self, o: tuple[float, float], d: tuple[float, float],
                measure: bool = True) -> Leg:
        key = self._key("transit", o, d)
        if key in self._cache:
            return self._cache[key]

        leg = estimate_transit(o, d)
        if not measure or self.offline:
            return leg
        if self.odsay:
            try:
                r = requests.get(
                    "https://api.odsay.com/v1/api/searchPubTransPathT",
                    params={"SX": o[1], "SY": o[0], "EX": d[1], "EY": d[0],
                            "apiKey": self.odsay, "OPT": 0},
                    headers={"Referer": self.odsay_referer},
                    timeout=self.timeout,
                )
                r.raise_for_status()
                body = r.json()
                if "error" in body:      # 200으로 오류를 담아 보낸다
                    raise ValueError(body["error"][0].get("message", "ODsay 오류"))
                path = body["result"]["path"][0]
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
        return self._remember(key, leg)

    # ---------- 자동차: 네이버 Directions 5 ----------

    def drive(self, o: tuple[float, float], d: tuple[float, float]) -> Leg:
        key = self._key("drive", o, d)
        if key in self._cache:
            return self._cache[key]

        straight = haversine_m(*o, *d)
        leg = Leg(mode="drive", minutes=max(3, round(straight / 400)),
                  distance_m=round(straight * 1.4), provider="estimate",
                  summary="직선거리 기반 추정", exact=False)
        if self.offline:
            return leg
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
        return self._remember(key, leg)

    # ---------- 구간에 맞는 수단 고르기 ----------

    def best(self, o: tuple[float, float], d: tuple[float, float],
             measure: bool = True) -> dict:
        """도보와 대중교통을 함께 재고 권장 수단을 정한다.

        가까우면 걷는 편이 빠르다. 대중교통은 대기와 환승 때문에 짧은
        거리에서 오히려 손해라, 도보권이면 아예 묻지 않는다.

        measure=False면 네트워크를 타지 않고 추정만 낸다. 후보를 고르는
        동안에는 한 자리에 여덟 곳을 시도하므로, 시도마다 경로 API를
        부르면 일정 하나에 수십 번을 묻게 된다. 확정된 구간만 실측한다.
        """
        walk = self.walk(o, d, measure)
        if walk.distance_m <= WALKABLE_M:
            return {"recommended": "walk", "walk": walk.to_dict(), "transit": None}
        transit = self.transit(o, d, measure)
        rec = "walk" if walk.minutes <= transit.minutes else "transit"
        return {"recommended": rec,
                "walk": walk.to_dict(), "transit": transit.to_dict()}


_router: Routing | None = None


def router() -> Routing:
    global _router
    if _router is None:
        _router = Routing()
    return _router
