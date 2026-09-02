"""엔드포인트 통합 테스트.

실제 수집 데이터를 쓴다. data/raw가 비어 있으면 건너뛴다 —
새로 클론한 곳에서 테스트가 빨갛게 되면 안 되기 때문이다.
"""
import pytest
from fastapi.testclient import TestClient

from weatherfit import server

AT = "2026-09-03T12:00"
SEOUL = {"lat": 37.5665, "lon": 126.9780}


@pytest.fixture(scope="module")
def client():
    if not server.index().places:
        pytest.skip("data/raw가 비어 있습니다. python -m weatherfit.collect --all")
    return TestClient(server.app)


class TestHealth:
    def test_적재_상태를_알려준다(self, client):
        d = client.get("/api/health").json()
        assert d["ok"] and d["items"] > 0
        assert d["located"] <= d["items"]

    def test_키_보유_여부를_숨기지_않는다(self, client):
        d = client.get("/api/health").json()
        assert set(d["keys"]) >= {"visitseoul_api", "kma", "llm"}
        assert set(d["routing"]) == {"walk", "transit", "drive"}


class TestWhere:
    @pytest.mark.parametrize("lat,lon,gu", [
        (37.5570, 126.9245, "마포구"),      # 홍대입구역
        (37.4979, 127.0276, "서초구"),      # 강남역
        (37.5826, 126.9830, "종로구"),      # 북촌
    ])
    def test_좌표를_행정동으로_바꾼다(self, client, lat, lon, gu):
        d = client.get("/api/where", params={"lat": lat, "lon": lon}).json()
        assert d["in_seoul"] is True
        assert d["gu"] == gu
        assert d["dong"]

    def test_서울_밖은_알려준다(self, client):
        d = client.get("/api/where", params={"lat": 35.18, "lon": 129.08}).json()
        assert d["in_seoul"] is False
        assert d["label"] == "서울 밖"

    def test_주변_개수를_함께_준다(self, client):
        d = client.get("/api/where", params=SEOUL).json()
        assert d["nearby"] > 0

    def test_숫자가_아니면_422(self, client):
        assert client.get("/api/where",
                          params={"lat": "a", "lon": "b"}).status_code == 422


class TestCandidates:
    def test_반경_안에서만_준다(self, client):
        d = client.get("/api/candidates",
                       params={**SEOUL, "radius_m": 1000, "at": AT}).json()
        assert all(r["distance_m"] <= 1000 for r in d["items"])

    def test_가까운_순(self, client):
        rows = client.get("/api/candidates",
                          params={**SEOUL, "radius_m": 2500, "at": AT}).json()["items"]
        assert rows == sorted(rows, key=lambda r: r["distance_m"])

    def test_판정을_통과한_것만(self, client):
        rows = client.get("/api/candidates",
                          params={**SEOUL, "at": AT, "limit": 50}).json()["items"]
        assert all(r["verdict"] == "통과" for r in rows)

    def test_비오면_실외가_빠진다(self, client):
        def envs(mode):
            rows = client.get("/api/candidates",
                              params={**SEOUL, "at": AT, "mode": mode,
                                      "radius_m": 3000}).json()["items"]
            return {r["environment"] for r in rows}
        assert "outdoor" not in envs("rain")
        assert "outdoor" in envs("clear")


class TestPlan:
    def test_시간표가_이어진다(self, client):
        d = client.post("/api/plan",
                        json={**SEOUL, "hours": 4, "at": AT, "mode": "clear"}).json()
        assert d["steps"]
        times = [s["arrive"] for s in d["steps"]]
        assert times == sorted(times)
        assert d["total_min"] == d["travel_min"] + d["dwell_min"]

    def test_남은_시간을_지킨다(self, client):
        for hours in (2, 3, 4):
            d = client.post("/api/plan",
                            json={**SEOUL, "hours": hours, "at": AT}).json()
            assert d["total_min"] <= hours * 60

    def test_선정_근거가_붙는다(self, client):
        d = client.post("/api/plan", json={**SEOUL, "hours": 4, "at": AT}).json()
        why = d["steps"][0]["why"]
        assert {p["key"] for p in why["parts"]} >= {"near", "quality", "popular"}
        assert 0 <= why["score"] <= 1

    def test_교체하면_다른_곳(self, client):
        first = client.post("/api/plan",
                            json={**SEOUL, "hours": 4, "at": AT}).json()
        top = first["steps"][0]["cid"]
        again = client.post("/api/plan",
                            json={**SEOUL, "hours": 4, "at": AT,
                                  "exclude": [top]}).json()
        assert all(s["cid"] != top for s in again["steps"])

    @pytest.mark.parametrize("cat", ["쇼핑", "체험관광", "자연관광", "문화관광"])
    def test_관심사가_반영된다(self, client, cat):
        """말한 분류가 실제로 일정에 들어와야 한다.

        '역사관광'으로는 이걸 검증할 수 없다. 시청 주변은 그 관심사를
        말하지 않아도 덕수궁·명동성당이 1위라, 통과해도 반영된 건지
        원래 그런 건지 구분이 안 된다.
        """
        def rank_of(interests):
            """그 분류가 일정에서 몇 번째로 나오나. 없으면 99."""
            d = client.post("/api/plan",
                            json={**SEOUL, "hours": 4, "at": AT, "mode": "clear",
                                  "interests": interests}).json()
            for i, s in enumerate(d["steps"]):
                if s["category"] == cat:
                    return i, d
            return 99, d

        before, _ = rank_of([])
        after, d = rank_of([cat])
        assert d["taste_applied"] is True
        assert after < 99                    # 말한 분류가 일정에 들어와야 하고
        assert after <= before               # 말하기 전보다 앞으로 와야 한다

    def test_관심사가_첫_장소를_바꾼다(self, client):
        """첫 장소가 나머지 일정의 위치를 정한다. 여기가 안 바뀌면
        관심사는 사실상 반영되지 않은 것이다."""
        def anchor(interests):
            d = client.post("/api/plan",
                            json={**SEOUL, "hours": 4, "at": AT, "mode": "clear",
                                  "interests": interests}).json()
            return d["steps"][0]["category"]
        assert anchor(["쇼핑"]) == "쇼핑"
        assert anchor(["자연관광"]) == "자연관광"

    def test_서울_밖이면_도심으로_돌리고_알린다(self, client):
        d = client.post("/api/plan",
                        json={"lat": 35.18, "lon": 129.08, "hours": 4,
                              "at": AT}).json()
        assert d["origin"]["moved_to_seoul"] is True
        assert any("서울 밖" in n for n in d["notes"])

    def test_지역마다_다른_일정(self, client):
        seen = set()
        for lat, lon in [(37.5570, 126.9245), (37.5826, 126.9830),
                         (37.5445, 127.0557), (37.4979, 127.0276)]:
            d = client.post("/api/plan",
                            json={"lat": lat, "lon": lon, "hours": 4,
                                  "at": AT, "mode": "clear"}).json()
            seen.add(tuple(s["cid"] for s in d["steps"]))
        assert len(seen) == 4          # 반경이 고정이면 여기가 1~2로 줄어든다

    def test_짧은_시간에는_이유를_말한다(self, client):
        d = client.post("/api/plan",
                        json={**SEOUL, "hours": 0.5, "at": AT}).json()
        assert d["steps"] == []
        assert d["notes"] and "분" in d["notes"][0]


class TestChat:
    def test_빈_대화도_답한다(self, client):
        d = client.post("/api/chat", json={"messages": []}).json()
        assert d["reply"]

    def test_의도를_뽑는다(self, client):
        d = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "비 오는데 홍대에서 3시간"}],
            "at": AT}).json()
        assert d["intent"]["area"] == "홍대"
        assert d["intent"]["weather_mode"] == "rain"
        assert d["intent"]["hours"] == 3

    def test_앞_대화의_조건을_이어받는다(self, client):
        first = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "홍대에서 3시간"}],
            "at": AT}).json()
        second = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "밥 먹을 곳 위주로"}],
            "at": AT, "intent": first["intent"]}).json()
        assert second["intent"]["area"] == "홍대"        # 지역을 물려받는다
        assert "음식" in second["intent"]["interests"]

    def test_추천은_실제_콘텐츠에서만_나온다(self, client):
        """LLM이 장소를 지어내면 '갔는데 없더라'가 다시 시작된다."""
        d = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "성수에서 2시간"}],
            "at": AT}).json()
        known = server.index().by_cid
        assert all(s["cid"] in known for s in d["course"]["steps"])


class TestRouting:
    def test_가까우면_도보를_권한다(self, client):
        d = client.get("/api/routing", params={
            "from_lat": 37.5665, "from_lon": 126.9780,
            "to_lat": 37.5680, "to_lon": 126.9800}).json()
        assert d["recommended"] == "walk"
        assert d["walk"]["minutes"] > 0

    def test_멀면_대중교통을_함께_준다(self, client):
        d = client.get("/api/routing", params={
            "from_lat": 37.5665, "from_lon": 126.9780,
            "to_lat": 37.4979, "to_lon": 127.0276}).json()
        assert d["transit"] is not None

    def test_추정인지_실측인지_밝힌다(self, client):
        d = client.get("/api/routing", params={
            "from_lat": 37.5665, "from_lon": 126.9780,
            "to_lat": 37.5680, "to_lon": 126.9800}).json()
        assert "exact" in d["walk"] and "provider" in d["walk"]


class TestStats:
    def test_근거_수치를_준다(self, client):
        d = client.get("/api/stats", params={"at": AT}).json()
        assert d["total"] > 0
        assert set(d["hours_confidence"]) <= {"high", "low", "none"}
        assert set(d["environment"]) <= {"indoor", "outdoor", "unknown"}
        assert d["funnel"]["passed"] <= d["total"]

    def test_자치구_분포가_있다(self, client):
        d = client.get("/api/stats", params={"at": AT}).json()
        assert len(d["distribution"]) > 10


class TestStatic:
    def test_HTML은_캐시하지_않는다(self, client):
        """index.html이 캐시되면 스크립트 경로를 바꿔도 옛 파일을 계속 부른다."""
        r = client.get("/")
        assert "no-cache" in r.headers.get("cache-control", "")

    def test_행정동_경계를_서빙한다(self, client):
        assert client.get("/data/seoul_dong.geojson").status_code == 200

    def test_매니페스트가_있다(self, client):
        assert client.get("/manifest.webmanifest").status_code == 200


class TestLanguages:
    def test_한국어는_언제나_있다(self, client):
        assert "ko" in client.get("/api/health").json()["languages"]

    def test_절반도_못_채운_어권은_켜지_않는다(self, client):
        """전환했는데 대부분 한국어가 그대로 나오면 '지원한다'가 거짓말이다."""
        d = client.get("/api/health").json()
        cov = d["language_coverage"]
        for lang in d["languages"]:
            if lang == "ko":
                continue
            assert cov[lang] >= 0.5

    def test_켜진_어권은_실제로_번역이_나온다(self, client):
        d = client.get("/api/health").json()
        for lang in d["languages"]:
            if lang == "ko":
                continue
            plan = client.post("/api/plan", json={**SEOUL, "hours": 4,
                                                  "at": AT, "lang": lang}).json()
            assert any(s["text_lang"] == lang for s in plan["steps"])
