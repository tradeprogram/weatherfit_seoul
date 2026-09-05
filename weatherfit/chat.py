"""대화형 코스 추천.

한 줄 명령이 아니라 대화다. "비 오는데 강남" → 코스 → "좀 더 조용한 데로"
→ 다시 코스. 앞의 맥락을 이어받아야 두 번째 요청이 말이 된다.

설계 원칙은 하나다. **LLM은 말을 알아듣고 말을 만드는 데만 쓰고,
무엇을 추천할지는 판정 엔진이 정한다.** LLM이 장소를 지어내면
"갔는데 없더라"가 다시 시작되기 때문이다. LLM은 두 곳에만 관여한다.

    1. 의도 추출   자연어 → {지역, 날씨, 시간, 관심사, 동행}
    2. 답변 생성   판정된 코스 → 사람이 읽는 문장

키가 없으면 1은 규칙으로, 2는 템플릿으로 떨어진다. 어느 쪽이 쓰였는지는
응답의 engine에 실린다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .llm import LLM

# ----------------------------------------------------------------- 장소 사전

SEOUL_CITY_HALL = (37.5665, 126.9780)

# 사람들은 자치구가 아니라 동네 이름으로 말한다
LANDMARKS: dict[str, tuple[float, float]] = {
    "홍대": (37.5570, 126.9245), "합정": (37.5495, 126.9137),
    "연남": (37.5601, 126.9256), "망원": (37.5556, 126.9016),
    "성수": (37.5445, 127.0557), "서울숲": (37.5443, 127.0374),
    "건대": (37.5403, 127.0695), "명동": (37.5636, 126.9827),
    "인사동": (37.5735, 126.9860), "북촌": (37.5826, 126.9830),
    "삼청동": (37.5825, 126.9810), "익선동": (37.5732, 126.9905),
    "을지로": (37.5660, 126.9910), "종로": (37.5701, 126.9925),
    "광화문": (37.5720, 126.9769), "경복궁": (37.5796, 126.9770),
    "이태원": (37.5346, 126.9946), "한남": (37.5343, 127.0016),
    "여의도": (37.5215, 126.9243), "잠실": (37.5133, 127.1000),
    "가로수길": (37.5205, 127.0230), "압구정": (37.5271, 127.0286),
    "청담": (37.5250, 127.0530), "강남역": (37.4979, 127.0276),
    "신촌": (37.5551, 126.9368), "동대문": (37.5654, 127.0090),
    "남산": (37.5512, 126.9882), "DDP": (37.5665, 127.0093),
    "노량진": (37.5140, 126.9425), "혜화": (37.5822, 127.0019),
    "대학로": (37.5822, 127.0019), "서촌": (37.5787, 126.9700),
}

# 로마자 표기. 영어 화면을 쓰는 사람은 'Seongsu'라고 친다 — 한글 이름만
# 들고 있으면 정작 이 서비스가 겨냥한 사람이 아무것도 못 찾는다.
# 행정동 424개까지 옮기지는 않는다. 검색해서 쓸모 있는 이름은 동네 쪽이고,
# 여기 32개가 그 대부분이다.
LANDMARK_EN: dict[str, str] = {
    "홍대": "Hongdae", "합정": "Hapjeong", "연남": "Yeonnam",
    "망원": "Mangwon", "성수": "Seongsu", "서울숲": "Seoul Forest",
    "건대": "Konkuk Univ.", "명동": "Myeongdong", "인사동": "Insadong",
    "북촌": "Bukchon", "삼청동": "Samcheong-dong", "익선동": "Ikseon-dong",
    "을지로": "Euljiro", "종로": "Jongno", "광화문": "Gwanghwamun",
    "경복궁": "Gyeongbokgung", "이태원": "Itaewon", "한남": "Hannam",
    "여의도": "Yeouido", "잠실": "Jamsil", "가로수길": "Garosu-gil",
    "압구정": "Apgujeong", "청담": "Cheongdam", "강남역": "Gangnam Stn.",
    "신촌": "Sinchon", "동대문": "Dongdaemun", "남산": "Namsan",
    "DDP": "DDP", "노량진": "Noryangjin", "혜화": "Hyehwa",
    "대학로": "Daehak-ro", "서촌": "Seochon",
}

GU_SHORT = ("종로|중구|용산|성동|광진|동대문|중랑|성북|강북|도봉|노원|은평|"
            "서대문|마포|양천|강서|구로|금천|영등포|동작|관악|서초|강남|송파|강동")

_GU_RE = re.compile(f"({GU_SHORT})")
_LANDMARK_RE = re.compile("(" + "|".join(sorted(LANDMARKS, key=len, reverse=True)) + ")")
_HOUR_RE = re.compile(r"(\d+)\s*시간")
# "3시부터"는 시작 시각, "3시간"은 길이다. 뒤에 '간'이 오면 이 패턴이 아니다.
_START_RE = re.compile(r"(오전|오후|저녁|밤|아침|낮)?\s*(\d{1,2})\s*시(?!간)")
_WHEN_WORDS = {"아침": 9, "점심": 12, "낮": 14, "오후": 15, "저녁": 18, "밤": 20}
NEAR_WORDS = ("가까운", "가까이", "멀지 않", "근처", "걸어서", "도보로만")
FAR_OK_WORDS = ("멀어도", "좀 멀", "어디든")

RAIN_WORDS = ("비 ", "비가", "비오", "비 오", "우천", "소나기", "장마", "빗")
HEAT_WORDS = ("더위", "더운", "덥", "폭염", "무더")
CLEAR_WORDS = ("맑", "화창", "해 뜨")

INTEREST_WORDS = {
    "음식": ("먹", "맛집", "식당", "밥", "카페", "커피", "디저트", "술", "바"),
    "문화관광": ("전시", "미술", "박물", "공연", "영화", "책", "갤러리"),
    "쇼핑": ("쇼핑", "옷 사", "소품", "기념품", "면세"),
    "자연관광": ("공원", "산책", "자연", "한강", "강변", "숲", "등산", "둘레길"),
    "역사관광": ("역사", "고궁", "한옥", "전통", "유적", "왕릉"),
    "체험관광": ("체험", "만들", "공방", "클래스"),
}


@dataclass
class Intent:
    area: str | None = None
    lat: float = SEOUL_CITY_HALL[0]
    lon: float = SEOUL_CITY_HALL[1]
    weather_mode: str = "auto"
    hours: float | None = None
    start_hour: int | None = None     # "오후 3시부터"
    max_walk_min: int = 25
    walk_limited: bool = False        # 거리를 직접 말했는가
    interests: list[str] = field(default_factory=list)
    party: str | None = None          # 혼자 | 커플 | 가족 | 친구
    language: str = "ko"
    engine: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return {
            "area": self.area, "weather_mode": self.weather_mode,
            "hours": self.hours, "start_hour": self.start_hour,
            "max_walk_min": self.max_walk_min,
            "walk_limited": self.walk_limited,
            "interests": self.interests, "party": self.party,
            "language": self.language, "engine": self.engine,
        }


# ----------------------------------------------------------------- 규칙 기반 추출

def parse_intent_rules(message: str, prev: Intent | None = None) -> Intent:
    """LLM 없이도 쓸 만한 의도 추출. 앞 대화의 조건을 물려받는다."""
    it = Intent()
    if prev:                                   # 이어지는 대화면 기본값을 승계
        it.area, it.lat, it.lon = prev.area, prev.lat, prev.lon
        it.weather_mode = prev.weather_mode
        it.hours, it.max_walk_min = prev.hours, prev.max_walk_min
        it.start_hour, it.walk_limited = prev.start_hour, prev.walk_limited
        it.interests, it.party = list(prev.interests), prev.party

    msg = message.strip()

    if any(k in msg for k in RAIN_WORDS):
        it.weather_mode = "rain"
    elif any(k in msg for k in HEAT_WORDS):
        it.weather_mode = "heat"
    elif any(k in msg for k in CLEAR_WORDS):
        it.weather_mode = "clear"

    if (m := _LANDMARK_RE.search(msg)):
        it.area = m.group(1)
        it.lat, it.lon = LANDMARKS[it.area]
    elif (m := _GU_RE.search(msg)):
        it.area = m.group(1) if m.group(1) == "중구" else m.group(1) + "구"

    if (m := _HOUR_RE.search(msg)):
        it.hours = float(m.group(1))
        it.max_walk_min = max(10, min(40, int(it.hours * 10)))
    elif "반나절" in msg:
        it.hours, it.max_walk_min = 4.0, 30
    elif "잠깐" in msg or "짧게" in msg:
        it.hours, it.max_walk_min = 1.5, 12

    # 시작 시각. "3시간"과 헷갈리지 않게 '간'을 배제한 뒤에 본다.
    if (m := _START_RE.search(msg)):
        h = int(m.group(2))
        ap = m.group(1)
        if ap in ("오후", "저녁", "밤") and h < 12:
            h += 12
        if 0 <= h <= 23:
            it.start_hour = h
    else:
        for word, h in _WHEN_WORDS.items():
            if word + "에" in msg or word + "부터" in msg:
                it.start_hour = h
                break

    # 거리 표현은 말했을 때만 반영한다. 말하지 않았으면 남은 시간에 맞춘
    # 기본 반경을 쓴다 — 여기서 늘 좁히면 4시간짜리도 동네 한 바퀴가 된다.
    if any(w in msg for w in NEAR_WORDS):
        it.max_walk_min, it.walk_limited = 12, True
    elif any(w in msg for w in FAR_OK_WORDS):
        it.max_walk_min, it.walk_limited = 40, True

    found = [cat for cat, words in INTEREST_WORDS.items()
             if any(w in msg for w in words)]
    if found:
        it.interests = found

    for label, words in (("가족", ("가족", "아이", "부모", "애들")),
                         ("커플", ("커플", "데이트", "여자친구", "남자친구")),
                         ("친구", ("친구", "친구들")),
                         ("혼자", ("혼자", "혼행", "나홀로"))):
        if any(w in msg for w in words):
            it.party = label
            break

    if re.search(r"[a-zA-Z]{4,}", msg) and not re.search(r"[가-힣]", msg):
        it.language = "en"

    return it


_INTENT_PROMPT = """서울 여행 안내 서비스의 의도 분석기다. 사용자 메시지에서
조건을 뽑아 JSON으로만 답하라. 언급되지 않은 항목은 null로 두어라.
추측해서 채우지 마라.

- area: 서울의 동네나 자치구 이름 (예: "홍대", "강남구"). 없으면 null
- weather_mode: "rain"(비 언급) | "heat"(더위) | "clear"(맑음) | null
- hours: 쓸 수 있는 시간(숫자, 단위 시간). 없으면 null
- interests: ["음식","문화관광","쇼핑","자연관광","역사관광","체험관광"] 중 해당하는 것
- party: "혼자" | "커플" | "가족" | "친구" | null
- language: 사용자가 쓴 언어의 코드 ("ko","en","ja","zh-CN" 등)

이전 대화 조건: {prev}
사용자 메시지: {message}

JSON만 출력:
{{"area":null,"weather_mode":null,"hours":null,"interests":[],"party":null,"language":"ko"}}"""


def parse_intent(message: str, prev: Intent | None = None,
                 llm: LLM | None = None) -> Intent:
    """LLM으로 의도를 뽑고, 실패하면 규칙으로 떨어진다."""
    base = parse_intent_rules(message, prev)
    llm = llm or LLM()
    if not llm.available:
        return base

    try:
        raw = llm._call(_INTENT_PROMPT.format(
            prev=json.dumps(prev.to_dict() if prev else {}, ensure_ascii=False),
            message=message), max_tokens=400)
        d = LLM._extract_json(raw)
    except Exception:
        return base

    it = base
    it.engine = "llm"
    if d.get("area"):
        it.area = str(d["area"])
        key = _LANDMARK_RE.search(it.area)
        if key:
            it.lat, it.lon = LANDMARKS[key.group(1)]
    if d.get("weather_mode") in ("rain", "heat", "clear"):
        it.weather_mode = d["weather_mode"]
    if d.get("hours"):
        try:
            it.hours = float(d["hours"])
            it.max_walk_min = max(10, min(40, int(it.hours * 10)))
        except (TypeError, ValueError):
            pass
    if isinstance(d.get("interests"), list) and d["interests"]:
        it.interests = [str(x) for x in d["interests"]]
    if d.get("party"):
        it.party = str(d["party"])
    if d.get("language"):
        it.language = str(d["language"])
    return it


# ----------------------------------------------------------------- 답변 생성

_REPLY_PROMPT = """너는 서울 여행 안내자다. 아래 코스는 이미 검증된 것이다.
지금 열려 있고, 오늘 날씨에 갈 수 있는 곳만 남긴 결과다.

**규칙**
- 코스에 없는 장소를 지어내지 마라
- 주어진 소요시간과 이유만 사용하라
- 3~4문장으로 짧게, 친근하지만 과장 없이
- {language} 언어로 답하라

지금: {when} · 날씨: {weather}
사용자 요청: {message}
{party_note}
코스:
{course}

참고사항: {notes}"""


def _course_text(steps: list[dict]) -> str:
    out = []
    for i, s in enumerate(steps, 1):
        tv = s.get("travel") or {}
        rec = tv.get("recommended")
        leg = (tv.get(rec) or {}) if rec else {}
        move = ""
        if leg:
            label = "도보" if rec == "walk" else "대중교통"
            move = f" (앞 장소에서 {label} {leg.get('minutes')}분"
            if leg.get("summary") and rec == "transit":
                move += f", {leg['summary']}"
            move += ")"
        out.append(f"{i}. {s['title']} — {s.get('category_path') or s.get('category')}"
                   f"{move} / {s.get('line', '')}")
    return "\n".join(out)


def _applied_line(intent: Intent, course: dict, message: str) -> str:
    """이번 답에 무엇이 반영됐는지 한 줄로 적는다."""
    bits = []
    if intent.area:
        bits.append(f"{intent.area} 기준")
    start = course.get("start")
    if intent.start_hour is not None and start:
        bits.append(f"{start}부터")
    if intent.hours:
        h = intent.hours
        bits.append(f"{int(h) if h == int(h) else h}시간에 맞춰")
    if intent.interests:
        bits.append(" · ".join(intent.interests) + " 위주로")
    if intent.party:
        bits.append({"가족": "아이와 함께 가기 좋은 곳으로",
                     "커플": "둘이 가기 좋은 곳으로",
                     "친구": "여럿이 가기 좋은 곳으로",
                     "혼자": "혼자 다니기 좋은 곳으로"}[intent.party])
    if intent.walk_limited and intent.max_walk_min <= 12:
        bits.append("걸어서 닿는 거리만")
    for mood in ("조용", "활기", "이색", "전통", "현대"):
        if mood in message:
            bits.append(f"{mood}한 쪽으로")
            break
    if not bits:
        return "현재 위치 기준으로 지금 갈 수 있는 곳만 골랐습니다."
    return "지금 갈 수 있는 곳 중에서 " + ", ".join(bits) + " 골랐습니다."


def compose_reply(message: str, intent: Intent, course: dict,
                  llm: LLM | None = None) -> tuple[str, str]:
    """(답변 문장, engine)"""
    steps = course.get("steps") or []
    weather = (course.get("weather") or {}).get("desc", "")
    notes = " / ".join(course.get("notes") or []) or "없음"

    if not steps:
        return ("지금 조건에 맞는 곳을 찾지 못했습니다. 시간대나 지역을 조금 "
                "바꿔서 다시 물어봐 주세요.", "rules")

    llm = llm or LLM()
    if llm.available:
        party = f"동행: {intent.party}" if intent.party else ""
        try:
            text = llm._call(_REPLY_PROMPT.format(
                language=intent.language, when=course.get("when", ""),
                weather=weather, message=message, party_note=party,
                course=_course_text(steps), notes=notes), max_tokens=600)
            return text.strip(), "llm"
        except Exception:
            pass

    # 템플릿 답변 — 키가 없어도 대화가 성립해야 한다.
    # 무엇을 반영했는지 첫 줄에 적는다. 늘 같은 문장이면 사용자는 자기가
    # 말한 조건이 들어간 건지 알 수 없고, 그건 대화가 아니라 안내문이다.
    head = _applied_line(intent, course, message) + f" 날씨는 {weather}입니다."
    body = []
    for i, s in enumerate(steps, 1):
        tv = s.get("travel") or {}
        rec = tv.get("recommended")
        leg = (tv.get(rec) or {}) if rec else {}
        move = ""
        if leg:
            move = ("도보 " if rec == "walk" else "대중교통 ") + f"{leg.get('minutes')}분"
            if rec == "transit" and leg.get("summary"):
                move += f" ({leg['summary']})"
            move = " · " + move
        body.append(f"{i}. {s['title']}{move} — {s.get('line', '')}")
    tail = ""
    if course.get("notes"):
        tail = "\n" + " ".join(course["notes"])
    return head + "\n" + "\n".join(body) + tail, "rules"
