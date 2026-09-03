"""LLM 어댑터 — 규칙이 못 푸는 구간을 담당한다.

세 가지 일을 시킨다.

  normalize_hours   자유 문장 운영시간 → 요일별 구조화 시간표
  tag_environment   실내/실외/반실내 + 우천 가능 여부
  explain_course    선택한 코스에 "왜 지금인지" 한 줄씩

두 제공자를 받는다. `ANTHROPIC_API_KEY`가 있으면 Claude를, 없고
`GEMINI_API_KEY`가 있으면 Gemini를 쓴다. 어느 쪽도 없으면 호출하지 않고
`weatherfit.normalize`의 규칙 결과를 그대로 쓴다. 즉 키가 없어도 파이프라인
전체가 돈다 — 정확도만 낮아진다. 어느 쪽이 쓰였는지는 결과의 `engine`
필드와 `/api/health`로 항상 구분된다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests

API_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta"
              "/models/{model}:generateContent")
DEFAULT_MODEL = os.environ.get("WEATHERFIT_MODEL", "claude-sonnet-5")
GEMINI_MODEL = os.environ.get("WEATHERFIT_GEMINI_MODEL", "gemini-3.6-flash")

_HOURS_SCHEMA = """{
  "always_open": false,
  "rules": [{"days": [0,1,2,3,4], "ranges": [["09:00","18:00"]]}],
  "closed_days": [0],
  "closed_note": "공휴일 제외 조건이 있으면 여기에",
  "confidence": "high"
}"""

_HOURS_PROMPT = """다음은 서울 관광 콘텐츠의 이용시간·휴무일 원문이다. 표기가 제각각이고
한국의 공휴일 규칙이 문장에 섞여 있다. 이를 기계가 판정할 수 있는 시간표로 바꿔라.

규칙:
- days는 0=월요일 … 6=일요일
- ranges는 24시간제 "HH:MM"
- 원문에 없는 요일을 추측해 넣지 마라. 요일 언급이 없으면 매일로 보되 confidence를 "low"로 하라
- "공휴일을 제외한 매주 월요일"처럼 달력이 있어야 판정되는 조건은 closed_note에 남기고 confidence를 "low"로 하라
- 판정이 불가능하면 confidence를 "none"으로 하고 rules를 비워라

JSON만 출력하라. 형식:
""" + _HOURS_SCHEMA + """

이용시간: {use_time}
휴무일: {closed_days}"""

_ENV_PROMPT = """다음 서울 관광 콘텐츠가 실내인지 실외인지 판정하라.
비가 오면 갈 수 없는 곳인지가 핵심이다.

- "indoor"   건물 안에서 대부분이 이루어짐 (전시관, 식당, 쇼핑몰)
- "outdoor"  야외가 주 무대 (공원, 거리축제, 산책로)
- "mixed"    실내외가 섞임 (야외 마당이 있는 한옥, 일부 프로그램만 야외)

JSON만 출력하라: {{"environment": "...", "rain_ok": true, "reason": "20자 이내"}}

제목: {title}
분류: {category}
설명: {description}
태그: {tags}"""


def load_env(path: str = ".env") -> None:
    """.env를 읽어 환경변수에 채운다. 이미 있는 값은 덮지 않는다.

    python-dotenv를 의존성에 더하지 않은 것은 이 한 가지만 필요해서다.
    실제로 넣은 키가 안 읽혀서 '키가 없다'고 표시되면 한참을 헤맨다.
    """
    from pathlib import Path

    f = Path(__file__).resolve().parent.parent / path
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and not os.environ.get(k):
            os.environ[k] = v


load_env()


@dataclass
class LLMResult:
    data: dict[str, Any]
    engine: str          # "llm" | "rules"
    error: str = ""


class LLM:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL,
                 timeout: int = 60):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.gemini_key = os.environ.get("GEMINI_API_KEY", "")
        self.model = model
        self.timeout = timeout

    @property
    def provider(self) -> str:
        if self.api_key:
            return "anthropic"
        return "gemini" if self.gemini_key else "none"

    @property
    def available(self) -> bool:
        return self.provider != "none"

    def _call(self, prompt: str, max_tokens: int = 1024) -> str:
        if self.provider == "gemini":
            return self._call_gemini(prompt, max_tokens)
        r = requests.post(
            API_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]

    def _call_gemini(self, prompt: str, max_tokens: int) -> str:
        """Gemini 호출.

        두 가지를 조심해야 한다. 사고 과정이 담긴 part가 섞여 오므로
        `thought` 표시가 붙은 것은 버린다 — 그대로 이으면 답변에
        "Wait, let's look" 같은 혼잣말이 사용자에게 나간다. 그리고 사고에도
        토큰이 들어가므로 예산을 넉넉히 준다. 모자라면 본문이 잘린 채
        생각만 남는다.
        """
        r = requests.post(
            GEMINI_URL.format(model=GEMINI_MODEL),
            headers={"x-goog-api-key": self.gemini_key,
                     "content-type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {
                      "maxOutputTokens": max(max_tokens, 1024) * 4,
                      "temperature": 0.4,
                      "thinkingConfig": {"thinkingLevel": "low"}}},
            timeout=self.timeout,
        )
        r.raise_for_status()
        cand = (r.json().get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts
                       if not p.get("thought")).strip()
        if not text:
            raise ValueError(f"빈 응답: {cand.get('finishReason', '')}")
        if cand.get("finishReason") == "MAX_TOKENS":
            # 문장 중간에서 끊긴 답을 내보내느니 규칙 답변이 낫다.
            raise ValueError("길이 제한에 걸려 답이 잘렸다")
        return text

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """모델이 설명을 덧붙여도 JSON 본문만 건져낸다."""
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0:
            raise ValueError("JSON을 찾지 못함")
        return json.loads(text[start:end + 1])

    # ---------------------------------------------------------------

    def normalize_hours(self, use_time: str, closed_days: str = "") -> LLMResult:
        from .normalize import parse_hours

        if not self.available:
            oh = parse_hours(use_time, closed_days)
            return LLMResult(oh.to_dict(), "rules")
        try:
            raw = self._call(_HOURS_PROMPT.format(
                use_time=use_time or "(없음)", closed_days=closed_days or "(없음)"))
            return LLMResult(self._extract_json(raw), "llm")
        except Exception as e:
            oh = parse_hours(use_time, closed_days)
            return LLMResult(oh.to_dict(), "rules", f"{type(e).__name__}: {e}")

    def tag_environment(self, title: str, category: str, description: str,
                        tags: list[str] | None = None) -> LLMResult:
        from .normalize import tag_environment as rule_tag

        if not self.available:
            label, reason = rule_tag(category, title, description, tags)
            return LLMResult({"environment": label, "reason": reason}, "rules")
        try:
            raw = self._call(_ENV_PROMPT.format(
                title=title, category=category,
                description=(description or "")[:600],
                tags=", ".join(tags or [])), max_tokens=256)
            return LLMResult(self._extract_json(raw), "llm")
        except Exception as e:
            label, reason = rule_tag(category, title, description, tags)
            return LLMResult({"environment": label, "reason": reason}, "rules",
                             f"{type(e).__name__}: {e}")

    def explain_course(self, course: list[dict], weather_desc: str,
                       when: str, lang: str = "ko") -> LLMResult:
        """코스 각 장소에 '왜 지금인지' 한 줄. 실패하면 규칙 문장을 쓴다."""
        if not self.available:
            return LLMResult({"lines": [_rule_line(s) for s in course]}, "rules")
        listing = "\n".join(
            f'{n}. {s["title"]} — {s.get("category","")} / '
            f'{s.get("environment","")} / {s.get("verdict_reason","")}'
            for n, s in enumerate(course, 1)
        )
        prompt = (
            f"지금은 {when}, 날씨는 {weather_desc}다.\n"
            f"아래 장소들로 서울 반나절 코스를 안내한다. 각 장소마다 "
            f"'왜 지금 여기인지'를 한 문장으로 써라. 사실에 없는 내용을 지어내지 마라.\n"
            f"{lang} 언어로, JSON만 출력: {{\"lines\": [\"...\", \"...\"]}}\n\n{listing}"
        )
        try:
            return LLMResult(self._extract_json(self._call(prompt, 512)), "llm")
        except Exception as e:
            return LLMResult({"lines": [_rule_line(s) for s in course]}, "rules",
                             f"{type(e).__name__}: {e}")


def _rule_line(step: dict) -> str:
    """LLM 없이 쓰는 설명 문장.

    코스 구성 단계(`course._default_line`)가 이미 역할·거리를 반영해 문장을
    만들어 두므로, 그것이 있으면 덮어쓰지 않는다.
    """
    if step.get("line"):
        return step["line"]
    if step.get("ends_today"):
        return "오늘이 마지막 날인 행사입니다."
    if step.get("environment") == "indoor":
        return "날씨와 무관하게 들어갈 수 있는 실내 장소입니다."
    if step.get("environment") == "outdoor":
        return "지금 날씨에 야외 활동이 가능합니다."
    return step.get("verdict_reason") or "지금 이용할 수 있습니다."
