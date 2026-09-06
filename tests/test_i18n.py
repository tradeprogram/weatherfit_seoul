"""영어 응답에 한글이 새지 않는가.

경계에서 옮기는 방식의 위험은 하나다 — 새 문장을 추가하고 구절 표에
안 넣으면 조용히 한국어가 나간다. 이 파일이 그걸 잡는 유일한 장치다.

고유명사는 예외다. 장소명·자치구·행정동은 음차가 맞고, 그건 번역이
아니라 표기 규칙이다. 비짓서울 원문(운영시간·휴무일)도 손대지 않는다 —
영업시간 원문을 어설프게 옮기면 없는 정보를 만든다.
"""
import io
import re

import pytest

from weatherfit.i18n import OURS, PHRASES, has_korean, localize, to_en

KO = re.compile(r"[가-힣]")


class TestPhrases:
    def test_긴_구절이_먼저_맞는다(self):
        """낱말 단위로 바꾸면 '실외 부적합'이 'Outdoor 부적합'으로 반쯤 남는다."""
        assert "부적합" not in to_en("실외 부적합 — 비 5mm")
        assert "Not suited to outdoors" in to_en("실외 부적합 — 비 5mm")

    def test_판정_사유가_영어로_나온다(self):
        for ko in ("상시 콘텐츠 (기간 없음)", "현재 휴무 또는 영업시간 밖",
                   "운영시간 판정 불가", "지금 갈 수 있음"):
            assert not has_korean(to_en(ko)), ko

    def test_트렌드_라벨이_영어로_나온다(self):
        for ko in ("뜨는 중", "최근 급등", "올랐다 진정", "꾸준함",
                   "식는 중", "자료 없음", "기준 흔들림"):
            assert not has_korean(to_en(ko)), ko

    def test_혼잡도가_영어로_나온다(self):
        """서울시 실시간 도시데이터는 한국어만 준다."""
        for ko in ("붐빔", "약간 붐빔", "보통", "여유"):
            assert not has_korean(to_en(ko)), ko

    def test_한국어면_그대로_둔다(self):
        assert localize({"reason": "영업 중"}, "ko")["reason"] == "영업 중"

    def test_모르는_말은_지우지_않는다(self):
        """조용히 지우면 뜻이 사라진다. 남겨 두고 테스트가 잡게 한다."""
        assert "듣도보도못한말" in to_en("듣도보도못한말")


class TestBoundary:
    def test_우리가_만든_필드만_옮긴다(self):
        """비짓서울 원문은 손대지 않는다."""
        got = localize({"reason": "영업 중", "use_time": "매주 금/토/일"}, "en")
        assert got["reason"] == "Open now"
        assert got["use_time"] == "매주 금/토/일"

    def test_중첩된_구조도_훑는다(self):
        got = localize({"items": [{"crowd": {"level": "붐빔"}}]}, "en")
        assert got["items"][0]["crowd"]["level"] == "Crowded"


class TestLiveResponse:
    """실제 응답에 한글이 남는지 본다. 이게 진짜 보증이다."""

    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient

        from weatherfit.server import app
        return TestClient(app)

    # 고유명사와 원문은 옮기지 않기로 한 필드.
    #
    # 'steps'는 TMAP·ODsay가 준 경로 안내다. '새문안로'는 거리 이름이라
    # 번역 대상이 아니고, 옮기면 오히려 현지에서 못 찾는다.
    SKIP = {"title", "address", "summary", "description", "use_time",
            "closed_days", "subway", "phone", "homepage", "gu", "dong",
            "category", "category_path", "tags", "name", "area", "source",
            "accessibility", "place", "steps"}

    def _scan(self, obj, path="", out=None):
        out = out if out is not None else []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in self.SKIP:
                    continue
                self._scan(v, f"{path}.{k}" if path else k, out)
        elif isinstance(obj, list):
            for v in obj:
                self._scan(v, path + "[]", out)
        elif isinstance(obj, str) and KO.search(obj):
            out.append((path, obj[:60]))
        return out

    def test_후보_응답에_한글이_없다(self, client):
        r = client.get("/api/candidates", params={
            "lat": 37.5665, "lon": 126.978, "radius_m": 1500,
            "limit": 30, "lang": "en"})
        assert r.status_code == 200
        left = self._scan(r.json())
        assert not left, f"영어 응답에 한글이 남았다: {left[:6]}"

    def test_일정_응답에_한글이_없다(self, client):
        r = client.post("/api/plan", json={
            "lat": 37.5665, "lon": 126.978, "hours": 4, "lang": "en"})
        assert r.status_code == 200
        left = self._scan(r.json())
        assert not left, f"영어 응답에 한글이 남았다: {left[:6]}"


class TestLanguageSticks:
    """고른 언어를 서비스가 도로 뺏어 가면 안 된다.

    영어로 고르고 '내 위치로 시작'을 누르면 새로 그려지는 화면이 한국어로
    나왔다. `syncLanguages()`가 서버에 어떤 어권을 쓸 수 있는지 묻고,
    **못 물어봤을 때도** 한국어만 있다고 단정해 고른 언어를 덮어썼기
    때문이다. 배포본에서 잘 났다 — 무료 티어 API는 15분 놀면 자고 깨는 데
    50초가 걸리는데 화면은 즉시 뜨므로, 그 사이에 실패한 응답이 돌아온다.

    못 물어본 것과 없는 것은 다르다.
    """

    def app(self):
        return io.open("web/app.js", encoding="utf-8").read()

    def block(self, name):
        """그 함수의 본문만 떼어 온다."""
        s = self.app()
        i = s.index(f"function {name}(")
        depth, j = 0, s.index("{", i)
        for k in range(j, len(s)):
            if s[k] == "{":
                depth += 1
            elif s[k] == "}":
                depth -= 1
                if depth == 0:
                    return s[i:k + 1]
        raise AssertionError(f"{name} 본문을 못 찾음")

    def braces(self, text, start):
        """`start`부터 첫 중괄호 한 쌍만 떼어 온다."""
        depth, i = 0, text.index("{", start)
        for k in range(i, len(text)):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    return text[i:k + 1]
        raise AssertionError("짝이 안 맞는 중괄호")

    def test_못_물어봤다고_한국어로_내리지_않는다(self):
        body = self.block("syncLanguages")
        catch = self.braces(body, body.index("catch"))
        assert "langsReady" not in catch, "실패 처리에서 어권 목록을 단정했다"
        assert "S.lang" not in catch, "실패 처리에서 고른 언어를 덮어썼다"

    def test_내릴_때는_화면도_같이_내린다(self):
        """상태만 바꾸면 화면은 영어인데 다음에 그리는 것부터 한국어가 된다."""
        body = self.block("syncLanguages")
        i = body.index("S.lang = 'ko'")
        assert "applyChrome()" in body[i:], "언어를 내리면서 화면을 안 고쳤다"

    def test_저장한_일정이_언어를_바꾸지_않는다(self):
        """언어는 지금 보고 있는 사람의 것이다."""
        assert "S.lang" not in self.block("restorePlan")

    def test_링크의_어권은_아는_값만_받는다(self):
        s = self.app()
        assert "LANGS.includes(p.get('lang'))" in s
        assert "const LANGS = [" in s

class TestUnitParity:
    """숫자에 붙는 단위 규칙도 서버와 화면이 같아야 한다.

    구절 표(PHRASES↔KO2EN)만 맞춰 두고 단위 규칙을 잊었더니 화면에
    '588 views 조회'가 남았다. 서버에는 '회 조회'를 한 덩어리로 보는
    규칙이 있는데 화면에 없어서 일반 규칙이 먼저 '회'만 먹은 것이다.
    """

    # 화면에 일부러 두지 않는 규칙과 그 이유.
    EXEMPT = {
        "호선": "지하철 안내는 비짓서울 원문이라 손대지 않는다",
        "개": "'3개'는 우리 문장에 안 쓴다 — 화면에서 '개월'을 깨뜨릴 위험만 남는다",
    }
    # 통문장 규칙은 일정 메모에만 쓰이고, 메모는 서버가 옮겨서 내려준다.
    # 낱말 규칙과 달리 화면이 다시 가질 이유가 없다.
    SENTENCE = ("습니다", "주세요")

    def js_units(self):
        s = io.open("web/app.js", encoding="utf-8").read()
        i = s.index("const KO_UNITS")
        return s[i:s.index("];", i)]

    def words(self, pat):
        """규칙이 잡는 한국어 낱말. '개'와 '개월'을 섞지 않으려면
        부분 문자열이 아니라 낱말 단위로 봐야 한다."""
        return set(re.findall(r"[가-힣]+", pat))

    def test_서버_규칙이_화면에도_있다(self):
        from weatherfit.i18n import UNITS

        js = self.js_units()
        missing = [pat for pat, _ in UNITS
                   if not (self.words(pat) & set(self.EXEMPT))
                   and not any(w in pat for w in self.SENTENCE)
                   and pat not in js]
        assert not missing, f"화면 단위 규칙에 빠짐: {missing}"

    def test_긴_규칙이_먼저_온다(self):
        """정규식은 먼저 맞는 것이 이긴다. '회'가 '회 조회'보다 앞에
        오면 조회수 문장이 반만 옮겨진다."""
        js = self.js_units()
        assert js.index(r"회\s*조회") < js.index(r"([\d,]+)\s*회/g")

    def test_혼잡_한_줄이_다_옮겨진다(self):
        """선정 근거의 혼잡 줄은 steps 안이라 서버가 손대지 않는다.
        화면이 못 옮기면 영어 화면에 한국어가 그대로 남는다."""
        from weatherfit.i18n import has_korean, to_en

        line = "약간 붐빔 · 지금 10,000~12,000명 · 외지인 35% · 15:00 이후 여유"
        assert not has_korean(to_en(line))
        js = self.js_units()
        for token in ("외지인", "이후 여유", "명"):
            assert token in js


class TestChrome:
    """화면 문구도 서버와 같은 표를 쓴다. 두 표가 갈라지면 한쪽만 영어가 된다."""

    def test_화면_표가_서버_표를_담고_있다(self):
        import json
        import pathlib
        import re

        s = pathlib.Path("web/app.js").read_text(encoding="utf-8")
        i = s.index("const KO2EN = ")
        j = s.index("};", i) + 1
        web = json.loads(s[i + len("const KO2EN = "):j])
        missing = [k for k in PHRASES if k not in web]
        assert not missing, f"화면 표에 빠진 구절: {missing[:8]}"

    def test_자치구는_로마자로_적는다(self):
        """번역이 아니라 표기 규칙이다. 현지에서 찾을 수 있어야 한다."""
        assert to_en("종로구") == "Jongno-gu"
        assert to_en("중구 명동").startswith("Jung-gu")

    def test_언어는_한국어와_영어_둘뿐이다(self):
        """반쯤 번역된 언어를 고르게 두는 것보다 없는 편이 낫다."""
        import pathlib

        from weatherfit.i18n import LANGS
        assert LANGS == ("ko", "en")
        html = pathlib.Path("web/index.html").read_text(encoding="utf-8")
        assert 'data-lang="ja"' not in html and 'data-lang="zh-CN"' not in html


class TestAgentEnglish:
    """도우미는 답변만이 아니라 도구 기록·근거·행동까지 영어여야 한다.
    화면에 그대로 나가는 값들이다."""

    def test_전부_우리_글인_덩어리는_통째로_옮긴다(self):
        """도구 기록에는 장소명이 안 들어가므로 필드를 가릴 필요가 없다.
        가리려 들면 'title'처럼 문맥에 따라 뜻이 갈리는 키에서 반드시 틀린다."""
        from weatherfit.i18n import deep_en

        got = deep_en([{"tool": "read_weather", "detail": "비 (강수 4.0mm)"},
                       {"label": "판정 근거"}], "en")
        assert got[0]["detail"] == "Rain (precipitation 4.0mm)"
        assert got[1]["label"] == "Why this call"

    def test_한국어면_건드리지_않는다(self):
        from weatherfit.i18n import deep_en

        got = deep_en([{"detail": "비 (강수 4.0mm)"}], "ko")
        assert got[0]["detail"] == "비 (강수 4.0mm)"

    def test_한_글자_날씨말이_낱말을_자르지_않는다(self):
        """'비'를 살리면서 '비빔밥'과 '분위기'는 건드리지 않아야 한다.
        경계가 없던 동안은 이 키들을 아예 뺄 수밖에 없었다."""
        assert to_en("비") == "Rain"
        assert to_en("비 (강수 4.0mm)").startswith("Rain")
        assert to_en("비빔밥") == "비빔밥"
        assert to_en("분위기 좋은 곳") == "분위기 좋은 곳"

    def test_도우미_안내문이_두_언어를_모두_가진다(self):
        import pathlib

        s = pathlib.Path("web/app.js").read_text(encoding="utf-8")
        assert "AI_PLACEHOLDER" in s
        assert "Where are you, and how long do you have?" in s
