"""테스트는 바깥 세상에 묻지 않는다.

일정을 짜면 확정된 구간을 실제 경로 API로 재는데, 그 호출이 테스트에
섞이면 두 가지가 나빠진다. 12초짜리 스위트가 분 단위로 늘고, 공개 OSRM
서버가 흔들리는 날에 우리 코드와 무관하게 빨개진다.

실측 경로 자체는 test_routing.py에서 가짜 라우터로 따로 확인한다.

LLM도 마찬가지다. 키가 있는 기계에서 테스트를 돌리면 매 답변이 달라지고
스위트가 5초에서 6분 43초로 늘어난다. 규칙 경로만 검증한다 — 어차피
키가 없어도 돌아가는 것이 이 설계의 요점이다.
"""
import pytest

from weatherfit import routing


@pytest.fixture(autouse=True)
def offline_router(monkeypatch):
    monkeypatch.setattr(routing, "_router", routing.Routing(offline=True))


@pytest.fixture(autouse=True)
def no_external_keys(monkeypatch):
    """LLM과 기상청도 부르지 않는다.

    실제 날씨로 테스트하면 비 오는 날과 맑은 날의 결과가 달라져,
    같은 코드가 어제는 통과하고 오늘은 실패한다. 날씨 시나리오는
    mode=clear|rain|heat 로 명시해 검증한다.
    """
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "KMA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
