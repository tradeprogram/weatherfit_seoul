"""웨더핏 에이전트 — 도구를 실제로 돌리고, 무엇을 했는지 보여 준다.

챗봇과 에이전트의 차이는 말투가 아니라 **행동**이다. 챗봇은 그럴듯한
문장을 만들고, 에이전트는 도구를 부르고 그 결과로 답한다. 그래서 여기선
순서를 뒤집었다.

    1. 규칙이 도구를 확정적으로 돌린다   위치 확인 → 날씨 → 위성 열지도 →
                                        운영 판정 → 코스 구성 → 구간 실측
    2. LLM은 그 결과를 사람 말로 옮기기만 한다

이 순서여야 두 가지가 보장된다. **키가 없어도 답이 나온다** — 도구는
그대로 돌고 문장만 템플릿이 된다. 그리고 **LLM이 장소를 지어낼 수 없다** —
답에 등장하는 모든 장소는 이미 판정을 통과한 실제 콘텐츠다. "갔는데
없더라"를 막는 것이 이 앱의 전부인데, 답변 생성기가 그걸 되살리면 안 된다.

응답에는 답변만이 아니라 `tool_trace`(무엇을 어떤 순서로 했나)와
`evidence`(그 근거가 무엇이었나)가 함께 실린다. 사용자가 우리 판단을
검증할 수 있어야 하기 때문이다.
"""
from __future__ import annotations

from typing import Any

from .chat import Intent, LANDMARKS, parse_intent
from .llm import LLM
from .taste import PARTY_AVOID, PARTY_TAGS, Taste, mood_interests

# "지금 짜 달라"가 아니라 "짜 둔 걸 고쳐 달라"는 말들.
# 이걸 못 알아들으면 이미 있는 일정을 버리고 처음부터 다시 짜게 된다.
REPLAN_WORDS = ("비 온대", "비온대", "비가 온대", "비 올", "비올",
                "소나기", "날씨 바뀌", "날씨가 바뀌", "우천", "갑자기 비",
                "폭염 온대", "더워진대", "다시 짜", "바꿔 줘", "바꿔줘")


# 화면에서 바로 눌러 갈 수 있는 곳. 답변 밑에 버튼으로 붙는다.
ACTIONS = {
    "plan": {"label": "일정 보기", "tab": "plan"},
    "nearby": {"label": "주변 둘러보기", "tab": "nearby"},
    "evidence": {"label": "판정 근거", "tab": "evidence"},
    "vault": {"label": "보관함에 저장", "tab": "plan", "act": "save"},
}

SYSTEM_PROMPT = """너는 '웨더핏 서울'의 여행 도우미다. 서울에 온 사람 옆에서
같이 계획을 짜 주는 사람처럼 말한다.

반드시 지킬 것:
- 한국어로, 친근하지만 과하지 않게. 존댓말을 쓰되 딱딱한 안내문 투는 피한다.
- **도구가 준 장소만 말한다.** 목록에 없는 곳을 지어내면 안 된다. 그게 이
  서비스의 존재 이유다.
- 운영시간을 '가정'한 곳은 반드시 그 사실을 말한다. "정보가 없어 일반적인
  영업시간으로 잡았으니 가시기 전에 확인해 보세요" 같은 식으로.
- 추정한 이동시간과 실제로 잰 이동시간을 섞어 말하지 않는다.
- 위성 지표면온도 근거가 있으면 왜 그 길을 골랐는지 한 줄로 설명한다.
- 답은 3~5문장. 순서대로 읽으면 그대로 움직일 수 있게 쓴다.
- 마지막에 다음에 물어볼 만한 것을 하나 슬쩍 권한다.
"""


def run_agent(payload: dict, deps: dict) -> dict:
    """도구를 순서대로 돌리고 근거를 모은다. LLM은 여기 관여하지 않는다.

    deps로 서버가 가진 것(인덱스·날씨·코스 생성기)을 받는다. 이 모듈이
    직접 import하면 순환 참조가 되고, 테스트에서 갈아끼우기도 어렵다.
    """
    message = str(payload.get("message", ""))[:2000]
    prev = _prev_intent(payload.get("intent"))
    trace: list[dict] = []
    evidence: list[dict] = []

    def record(tool: str, status: str, detail: str = "") -> None:
        trace.append({"tool": tool, "status": status, "detail": detail})

    # ---------- 1. 무슨 말인지 ----------
    intent = parse_intent(message, prev, deps.get("llm"))
    record("parse_intent", "ok" if message else "skip",
           _intent_line(intent) or "조건 없음")

    # ---------- 2. 어디서 ----------
    lat, lon = intent.lat, intent.lon
    if not intent.area and payload.get("lat") and payload.get("lon"):
        lat, lon = payload["lat"], payload["lon"]
    elif intent.area and intent.area not in LANDMARKS:
        center = deps["gu_center"](intent.area.replace("구", ""))
        if center:
            lat, lon = center
    where = deps["where"](lat, lon)
    record("resolve_where", "ok" if where.get("in_seoul") else "outside",
           f"{where.get('label', '')} · 주변 {where.get('nearby', 0)}곳")

    # ---------- 3. 취향 ----------
    taste = Taste.from_dict(payload.get("taste"))
    if intent.interests:
        taste.declare(intent.interests, weight=1.5)
    moods = mood_interests(message)
    for tag in moods:
        taste.tags[tag] = taste.tags.get(tag, 0.0) + 0.8
    party_tags = PARTY_TAGS.get(intent.party or "", ())
    for tag in party_tags:
        taste.tags[tag] = taste.tags.get(tag, 0.0) + 0.6
    if intent.interests or moods or party_tags:
        record("apply_taste", "ok", taste.describe() or "취향 반영")

    # ---------- 4. 일정 ----------
    at = deps["shift_start"](payload.get("at"), intent.start_hour)

    # 이미 일정이 있고 "비 온대요" 같은 말이면, 처음부터 다시 짜지 않고
    # 원래 하려던 경험을 지키며 고친다. 이게 이 서비스의 핵심 동작이다.
    prior = payload.get("course") or {}
    if (prior.get("steps") and _wants_replan(message)
            and deps.get("replan")):
        got = deps["replan"](
            prior_cids=[s["cid"] for s in prior["steps"]],
            lat=lat, lon=lon, at=at, hours=float(intent.hours or 4.0),
            mode=intent.weather_mode if intent.weather_mode != "auto" else "rain",
            taste=taste, styles=payload.get("styles") or [],
            done_until=payload.get("done_until"),
            lang=payload.get("lang", "ko"))
        if got:
            record("replan_course", "ok",
                   f"경험 보존 {got['experience_kept']:.0%} · "
                   f"{len(got['after'].get('steps') or [])}곳")
            for line in (got["after"].get("notes") or [])[1:4]:
                if "→" in line:
                    evidence.append({"kind": "swap", "title": "바뀐 곳",
                                     "summary": line, "note": ""})
            course = got["after"]
            course["replanned"] = True
            course["experience_kept"] = got["experience_kept"]
            return _finish(intent, course, taste, where, trace, evidence,
                           deps, record, message, (lat, lon))

    course = deps["make_course"](
        lat=lat, lon=lon, mode=intent.weather_mode, at=at,
        hours=float(intent.hours or 4.0),
        radius_m=(int(intent.max_walk_min * 67) if intent.walk_limited else 4000),
        interests=",".join(intent.interests), taste=taste,
        avoid=PARTY_AVOID.get(intent.party or "", ()),
        lang=intent.language if intent.language in ("ko", "en", "ja") else
        payload.get("lang", "ko"),
    )
    steps = course.get("steps") or []
    record("plan_course", "ok" if steps else "empty",
           f"{len(steps)}곳 · {course.get('total_min', 0)}분")

    w = course.get("weather") or {}
    record("read_weather", "ok",
           f"{w.get('desc', '')} · {w.get('source', '')}")
    evidence.append({
        "kind": "weather", "title": "지금 날씨",
        "summary": f"{w.get('desc', '')} — "
                   + ("실외 활동 가능" if w.get("outdoor_ok") else "실외 제외"),
        "note": {"kma": "기상청 초단기실황", "fallback": "기본값(키 없음)",
                 "manual": "직접 고른 시나리오"}.get(w.get("source"), ""),
    })

    # ---------- 5. 위성 열지도 ----------
    thermal = deps.get("thermal")
    heat = thermal(where.get("adm_cd")) if thermal else None
    if heat:
        record("read_thermal", "ok",
               f"지표면온도 상위 {heat['percentile']}% · 녹지 {heat['ndvi']:.2f}")
        evidence.append({
            "kind": "thermal", "title": f"{where.get('dong', '')} 지표 열부담",
            "summary": heat["label"],
            "note": heat["source"],
        })

    # ---------- 6. 구간과 근거 ----------
    if steps:
        exact = sum(1 for s in steps
                    if ((s.get("travel") or {}).get("walk") or {}).get("exact"))
        record("measure_walk", "ok", f"{exact}/{len(steps)}구간 실측")
        assumed = [s["title"] for s in steps if s.get("hours_assumed")]
        if assumed:
            record("check_hours", "assumed",
                   f"{len(assumed)}곳은 운영시간을 가정")
        for s in steps[:3]:
            why = s.get("why") or {}
            top = max(why.get("parts") or [{}],
                      key=lambda x: x.get("value", 0) * x.get("weight", 0))
            evidence.append({
                "kind": "pick", "title": s["title"],
                "summary": f"{s.get('arrive')} 도착 · {s.get('dwell_min')}분",
                "note": f"{top.get('label', '')} — {top.get('note', '')}",
            })

    return {
        "intent": intent, "course": course, "taste": taste, "where": where,
        "tool_trace": trace, "evidence": evidence, "heat": heat,
        "actions": _actions(steps, message),
        "origin": {"lat": lat, "lon": lon},
    }


def _prev_intent(d) -> Intent | None:
    """앞 턴의 의도를 되살린다. 화면은 dict로 들고 다닌다."""
    if not d:
        return None
    if isinstance(d, Intent):
        return d
    it = Intent()
    for k, v in d.items():
        if hasattr(it, k):
            setattr(it, k, v)
    if it.area in LANDMARKS:
        it.lat, it.lon = LANDMARKS[it.area]
    return it


def _wants_replan(message: str) -> bool:
    return any(w in message for w in REPLAN_WORDS)


def _finish(intent, course, taste, where, trace, evidence, deps, record,
            message, origin):
    """재편성 경로에서도 날씨·열지도·구간 기록은 똑같이 남긴다."""
    w = course.get("weather") or {}
    record("read_weather", "ok", f"{w.get('desc', '')} · {w.get('source', '')}")
    evidence.insert(0, {
        "kind": "weather", "title": "바뀐 날씨",
        "summary": f"{w.get('desc', '')} — "
                   + ("실외 활동 가능" if w.get("outdoor_ok") else "실외 제외"),
        "note": "",
    })
    heat = (deps.get("thermal") or (lambda _: None))(where.get("adm_cd"))
    steps = course.get("steps") or []
    return {
        "intent": intent, "course": course, "taste": taste, "where": where,
        "tool_trace": trace, "evidence": evidence, "heat": heat,
        "actions": _actions(steps, message),
        "origin": {"lat": origin[0], "lon": origin[1]},
    }


def _intent_line(it: Intent) -> str:
    bits = []
    if it.area:
        bits.append(it.area)
    if it.start_hour is not None:
        bits.append(f"{it.start_hour}시부터")
    if it.hours:
        bits.append(f"{it.hours:g}시간")
    if it.weather_mode != "auto":
        bits.append({"rain": "비", "heat": "폭염", "clear": "맑음"}[it.weather_mode])
    if it.interests:
        bits.append("·".join(it.interests))
    if it.party:
        bits.append(it.party)
    return " · ".join(bits)


def _actions(steps: list, message: str) -> list[dict]:
    out = [dict(ACTIONS["plan"])] if steps else [dict(ACTIONS["nearby"])]
    if steps:
        out.append(dict(ACTIONS["vault"]))
    out.append(dict(ACTIONS["evidence"]))
    return out


# ----------------------------------------------------------------- 답변

def josa(word: str, kind: str = "로") -> str:
    """받침에 맞는 조사. '정동극장로'가 아니라 '정동극장으로'.

    한글 음절은 (초성, 중성, 종성)이 규칙적으로 인코딩돼 있어
    코드포인트 산술만으로 받침 유무를 안다. 'ㄹ' 받침은 '로'를 그대로 쓴다.
    """
    if not word:
        return {"로": "으로", "은": "은", "이": "이", "을": "을"}[kind]
    ch = word.strip()[-1]
    if not ("가" <= ch <= "힣"):
        return {"로": "로", "은": "는", "이": "가", "을": "를"}[kind]
    jong = (ord(ch) - 0xAC00) % 28
    if kind == "로":
        return "로" if jong in (0, 8) else "으로"      # 8 = ㄹ
    pair = {"은": ("는", "은"), "이": ("가", "이"), "을": ("를", "을")}[kind]
    return pair[1] if jong else pair[0]


def local_answer(agent: dict) -> str:
    """키가 없을 때의 답. 도구가 이미 다 돌았으니 옮겨 적기만 하면 된다."""
    course = agent["course"]
    steps = course.get("steps") or []
    it: Intent = agent["intent"]
    w = (course.get("weather") or {}).get("desc", "")

    if not steps:
        why = (course.get("notes") or ["조건에 맞는 곳을 찾지 못했어요."])[0]
        return (f"{why}\n\n시간을 조금 늘리시거나 다른 동네로 바꿔서 다시 "
                "물어봐 주세요. 어디쯤 계신지 알려주시면 더 정확해요.")

    lead = _lead(it, agent)
    lines = []
    for i, s in enumerate(steps, 1):
        leg = _leg_text(s)
        lines.append(f"{i}. {s['arrive']} {s['title']}"
                     + (f" — {leg}" if leg else "")
                     + (f"\n   {s['line']}" if s.get("line") else ""))

    tail = []
    if course.get("replanned"):
        kept = course.get("experience_kept") or 0
        tail.append(f"원래 하려던 경험은 {kept:.0%} 지켰어요. "
                    "무엇을 무엇으로 바꿨는지는 아래에 적어 두었습니다.")
    # 더위 조언은 더울 때만. 비 오는 날 "그늘이 귀해요"는 헛말이다.
    hot = (course.get("weather") or {}).get("temp_c") or 0
    if agent.get("heat") and hot >= 28:
        tail.append(agent["heat"]["advice"])
    assumed = [s["title"] for s in steps if s.get("hours_assumed")]
    if assumed:
        tail.append(f"{assumed[0]}"
                    + (f" 외 {len(assumed) - 1}곳" if len(assumed) > 1 else "")
                    + "은 운영시간 정보가 없어 일반적인 영업시간으로 잡았어요. "
                      "가시기 전에 한 번 확인해 보시면 좋겠습니다.")
    backup = course.get("backup")
    if backup and not (course.get("weather") or {}).get("outdoor_ok", True):
        tail.append(f"날씨가 더 나빠지면 {backup['title']}"
                    f"{josa(backup['title'])} 피하실 수 있어요.")

    ask = ("시간을 더 쓸 수 있으면 말씀해 주세요. 늘려서 다시 짜 드릴게요."
           if (it.hours or 4) <= 3 else
           "마음에 안 드는 곳이 있으면 '다른 곳으로' 라고 말씀해 주세요.")
    return "\n".join([f"{lead} {w}예요.", "", *lines, "",
                      *(t for t in tail if t), ask])


def _lead(it: Intent, agent: dict) -> str:
    if agent["course"].get("replanned"):
        return "날씨가 바뀌어서 남은 일정을 고쳤어요. 지금은"
    where = agent["where"].get("label") or "지금 계신 곳"
    bits = []
    if it.start_hour is not None:
        bits.append(f"{agent['course'].get('start')}부터")
    if it.hours:
        bits.append(f"{it.hours:g}시간")
    if it.party:
        bits.append({"가족": "아이와 함께", "커플": "둘이서",
                     "친구": "여럿이", "혼자": "혼자"}[it.party])
    if it.interests:
        bits.append("·".join(it.interests) + " 위주로")
    body = " ".join(bits)
    return f"{where}에서 {body} 짜 봤어요. 날씨는".replace("  ", " ") if body \
        else f"{where} 기준으로 짜 봤어요. 날씨는"


def _leg_text(step: dict) -> str:
    tv = step.get("travel") or {}
    rec = tv.get("recommended")
    leg = (tv.get(rec) or {}) if rec else {}
    if not leg or not leg.get("minutes"):
        return ""
    mode = "걸어서" if rec == "walk" else "대중교통으로"
    tag = "" if leg.get("exact") else " (추정)"
    extra = f", {leg['summary']}" if rec == "transit" and leg.get("summary") else ""
    return f"{mode} {leg['minutes']}분{tag}{extra}"


def compose(payload: dict, agent: dict, llm: LLM | None = None) -> tuple[str, str]:
    """(답변, engine). LLM이 있으면 산문만 맡기고, 없으면 템플릿."""
    llm = llm or LLM()
    if not llm.available:
        return local_answer(agent), "rules"
    try:
        text = llm._call(_prompt(payload, agent), max_tokens=700)
        return text.strip(), "llm"
    except Exception:
        return local_answer(agent), "rules"


def _prompt(payload: dict, agent: dict) -> str:
    course = agent["course"]
    steps = course.get("steps") or []
    lines = []
    for i, s in enumerate(steps, 1):
        lines.append(
            f"{i}. {s['title']} ({s.get('category_path') or s.get('category')})"
            f" · {s.get('arrive')}~{s.get('depart')} {s.get('dwell_min')}분"
            f" · {_leg_text(s) or '출발지'}"
            + (" · 운영시간 가정함" if s.get("hours_assumed") else "")
            + (f" · {s.get('line')}" if s.get("line") else ""))
    # 열부담은 더울 때만 넣는다. 21°C에 "그늘이 귀하다"고 말하게 두면 안 된다.
    w = course.get("weather") or {}
    heat = agent.get("heat") if (w.get("temp_c") or 0) >= 28 else None
    return "\n".join([
        SYSTEM_PROMPT, "",
        f"사용자 말: {payload.get('message', '')}",
        f"장소: {agent['where'].get('label', '')}",
        f"날씨: {w.get('desc', '')}",
        "위성 열부담: " + (f"{heat['label']} / {heat['advice']}"
                       if heat else "해당 없음 — 꺼내지 말 것"),
        ("재편성: 날씨가 바뀌어 남은 일정을 고쳤다. 원래 경험을 "
         f"{(course.get('experience_kept') or 0):.0%} 지켰다."
         if course.get("replanned") else ""),
        "",
        "확정된 일정(이 목록 밖의 장소를 언급하지 말 것):",
        *(lines or ["(없음)"]),
        "",
        f"메모: {' / '.join(course.get('notes') or []) or '없음'}",
        "",
        "위 내용을 사람 말로 옮겨 답해라.",
    ])
