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


class TestChatIntentDetail:
    """대화에서 말한 조건이 실제로 일정에 반영되는가."""

    def ask(self, client, text, at=AT):
        return client.post("/api/chat", json={
            "messages": [{"role": "user", "content": text}], "at": at}).json()

    def test_시작_시각을_반영한다(self, client):
        """'오후 3시부터'를 흘리면 3시 일정을 달라고 했는데 지금 시각으로 짠다."""
        d = self.ask(client, "오후 3시부터 3시간", at="2026-09-03T10:00")
        assert d["intent"]["start_hour"] == 15
        assert d["course"]["start"] == "15:00"

    def test_시간_길이와_시작_시각을_헷갈리지_않는다(self):
        from weatherfit.chat import parse_intent_rules
        it = parse_intent_rules("3시간만 있어요")
        assert it.hours == 3.0 and it.start_hour is None

    @pytest.mark.parametrize("text,hour", [
        ("저녁에 2시간", 18), ("아침에 갈 만한 곳", 9), ("밤 9시에", 21),
    ])
    def test_시간대_표현도_읽는다(self, text, hour):
        from weatherfit.chat import parse_intent_rules
        assert parse_intent_rules(text).start_hour == hour

    def test_아이와_함께면_주점을_빼고_말해_준다(self, client):
        d = self.ask(client, "아이랑 같이 갈 만한 곳")
        assert d["intent"]["party"] == "가족"
        assert all("주점" not in (s["category_path"] or "")
                   for s in d["course"]["steps"])
        assert "아이와 함께" in d["reply"]

    def test_가까운_데로만_하면_반경이_좁아진다(self, client):
        wide = self.ask(client, "성수에서 4시간")
        near = self.ask(client, "성수에서 가까운 데로만")
        assert near["intent"]["walk_limited"] is True
        assert wide["intent"]["walk_limited"] is False

    def test_무엇을_반영했는지_답에_적는다(self, client):
        """늘 같은 문장이면 사용자는 자기 말이 들어갔는지 알 수 없다."""
        plain = self.ask(client, "추천해 주세요")["reply"].splitlines()[0]
        rich = self.ask(client, "북촌에서 전시 보고 밥 먹고 싶어요")
        first = rich["reply"].splitlines()[0]
        assert first != plain
        assert "북촌" in first and "음식" in first


class TestOffline:
    def test_서비스_워커를_서빙한다(self, client):
        import re
        r = client.get("/sw.js")
        assert r.status_code == 200
        # 캐시 버전은 배포마다 올린다. 값을 고정하면 올릴 때마다 빨개진다.
        assert re.search(r"const VERSION = 'weatherfit-v\d+'", r.text)

    def test_판정_결과는_캐시하지_않는다(self, client):
        """어제의 '열려 있음'을 오늘 답으로 주면 이 앱의 존재 이유가 사라진다."""
        sw = client.get("/sw.js").text
        assert "/api/" in sw and "캐시하지 않는다" in sw

    def test_서비스_워커에는_no_store를_붙이지_않는다(self, client):
        """no-store가 붙으면 크롬이 워커 등록을 거부한다.
        revalidate만 시키면 새로 배포한 워커는 그대로 잡힌다."""
        cc = client.get("/sw.js").headers.get("cache-control", "")
        assert "no-cache" in cc and "no-store" not in cc

    def test_다른_스크립트는_캐시하지_않는다(self, client):
        cc = client.get("/app.js").headers.get("cache-control", "")
        assert "no-store" in cc

    def test_껍데기를_미리_받아_둔다(self, client):
        sw = client.get("/sw.js").text
        for asset in ("index.html", "style.css", "app.js", "vendor/leaflet.js"):
            assert asset in sw


class TestAgent:
    """에이전트는 도구를 돌리고 무엇을 했는지 남긴다."""

    def ask(self, client, text, **kw):
        body = {"message": text, "at": AT, **SEOUL}
        body.update(kw)
        return client.post("/api/agent", json=body).json()

    def test_도구를_순서대로_돌린다(self, client):
        d = self.ask(client, "지금 여기서 3시간")
        tools = [t["tool"] for t in d["tool_trace"]]
        assert tools[:2] == ["parse_intent", "resolve_where"]
        assert "plan_course" in tools

    def test_근거를_함께_준다(self, client):
        d = self.ask(client, "지금 여기서 3시간")
        kinds = {e["kind"] for e in d["evidence"]}
        assert "weather" in kinds and "pick" in kinds

    def test_답에_등장하는_장소는_전부_실제_콘텐츠다(self, client):
        """LLM이 장소를 지어내면 '갔는데 없더라'가 다시 시작된다."""
        d = self.ask(client, "성수에서 2시간")
        known = server.index().by_cid
        assert all(s["cid"] in known for s in d["course"]["steps"])
        for s in d["course"]["steps"]:
            assert s["title"] in d["answer"]

    def test_키가_없어도_답이_나온다(self, client):
        d = self.ask(client, "북촌에서 3시간")
        assert d["answer"] and d["engine"] in ("rules", "llm")

    def test_빈_말에는_되묻는다(self, client):
        d = client.post("/api/agent", json={"message": "  "}).json()
        assert "알려주시면" in d["answer"] and d["course"] is None

    def test_바로_누를_수_있는_행동을_준다(self, client):
        d = self.ask(client, "지금 여기서 3시간")
        assert {a["label"] for a in d["actions"]} >= {"일정 보기", "판정 근거"}

    def test_폭염이면_위성_열지도를_본다(self, client):
        d = self.ask(client, "폭염인데 3시간", at="2026-08-05T14:00")
        assert any(t["tool"] == "read_thermal" for t in d["tool_trace"])
        assert d["heat"] and "lst_c" in d["heat"]


class TestAgentReplan:
    """이미 일정이 있는데 "비 온대요"면 처음부터 다시 짜지 않는다."""

    def plan(self, client):
        return client.post("/api/agent", json={
            "message": "성수에서 4시간", "lat": 37.5445, "lon": 127.0557,
            "at": AT}).json()

    def test_기존_일정을_고친다(self, client):
        first = self.plan(client)
        assert first["course"]["steps"]
        again = client.post("/api/agent", json={
            "message": "갑자기 비 온대요", "lat": 37.5445, "lon": 127.0557,
            "at": AT, "intent": first["intent"], "taste": first["taste"],
            "course": first["course"]}).json()
        assert any(t["tool"] == "replan_course" for t in again["tool_trace"])
        assert again["course"]["replanned"] is True
        assert 0 <= again["course"]["experience_kept"] <= 1

    def test_일정이_없으면_그냥_짠다(self, client):
        d = client.post("/api/agent", json={
            "message": "갑자기 비 온대요", "lat": 37.5445, "lon": 127.0557,
            "at": AT}).json()
        assert not any(t["tool"] == "replan_course" for t in d["tool_trace"])

    def test_바꾼_내용을_근거로_보여_준다(self, client):
        first = self.plan(client)
        again = client.post("/api/agent", json={
            "message": "비 온대요", "lat": 37.5445, "lon": 127.0557,
            "at": AT, "intent": first["intent"], "course": first["course"]}).json()
        assert "지켰어요" in again["answer"] or "경험" in again["answer"]


class TestDeploy:
    """배포본이 콘텐츠를 가진 채로 뜨는지."""

    def test_압축본만_있어도_읽는다(self):
        """배포 저장소에는 *.jsonl.gz만 들어간다. 원본이 없다고
        콘텐츠 0건으로 뜨면 서버가 올라와도 아무 쓸모가 없다."""
        import gzip
        from weatherfit import report

        assert list(report.RAW.glob("*.jsonl.gz")), "압축본이 저장소에 없다"
        # 원본을 감춰도 같은 건수가 나와야 한다
        raws = list(report.RAW.glob("*.jsonl"))
        hidden = [p.rename(p.with_suffix(".jsonl.hidden")) for p in raws]
        try:
            assert len(report.load("ko")) > 3000
        finally:
            for p in hidden:
                p.rename(p.with_suffix("").with_suffix(".jsonl"))

    def test_배포_설정이_같은_주소를_가리킨다(self):
        """Render 서비스 이름을 바꾸면 Vercel rewrite도 같이 고쳐야 한다."""
        import json
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        render = (root / "render.yaml").read_text(encoding="utf-8")
        name = next(l.split("name:")[1].strip() for l in render.splitlines()
                    if l.strip().startswith("name:"))
        vercel = json.loads((root / "vercel.json").read_text(encoding="utf-8"))
        dest = vercel["rewrites"][0]["destination"]
        assert f"{name}.onrender.com" in dest, f"{name} ≠ {dest}"

    def test_정적_파일이_출력_디렉터리에_있다(self):
        import json
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        out = json.loads((root / "vercel.json").read_text(encoding="utf-8"))
        web = root / out["outputDirectory"]
        for f in ("index.html", "app.js", "style.css", "sw.js",
                  "manifest.webmanifest", "data/seoul_dong.geojson",
                  "vendor/leaflet.js"):
            assert (web / f).exists(), f
