"""운영정보(이용시간·휴무일) 자유 문장을 구조화한다.

목적은 두 가지다.

1. 실제로 "지금 열려 있는가"를 판정할 수 있는 시간표를 만든다
2. **규칙만으로는 어디까지 되는가**를 측정한다. 규칙이 못 푸는 비율이
   곧 LLM이 필요한 이유이고, 제안서의 근거가 된다

그래서 파서는 실패를 숨기지 않는다. 애매하면 confidence를 낮추고 이유를 남긴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time
from typing import Any

DAYS = "월화수목금토일"  # 0=월 … 6=일

# "09:00~18:00", "10:00~ 20:00", "17:15-23:00"
_TIME_RANGE = re.compile(
    r"(\d{1,2})\s*[:：]\s*(\d{2})\s*[~\-–—]\s*(\d{1,2})\s*[:：]\s*(\d{2})"
)
# "화~금요일", "화요일 ~ 일요일", "월~일"
_DAY_RANGE = re.compile(rf"([{DAYS}])(?:요일)?\s*[~\-–]\s*([{DAYS}])(?:요일)?")
_DAY_SINGLE = re.compile(rf"([{DAYS}])요일")

# 한 줄 안에서 요일 구간이 바뀌는 지점. 범위형("화~금요일")을 단일형보다 먼저 시도해야
# "화~금요일"이 "금요일"로 잘리지 않는다.
_DAY_SCOPE = re.compile(
    rf"(?:[{DAYS}](?:요일)?\s*[~\-–]\s*[{DAYS}](?:요일)?|[{DAYS}]요일|평일|주말|매일)"
)

# 이 표현이 있으면 문장 하나로 시간표가 확정되지 않는다
_AMBIGUOUS = [
    "※", "상이", "별도", "문의", "참조", "참고", "홈페이지", "확인",
    "변경", "시즌", "동절기", "하절기", "예약", "사전", "프로그램별", "매장별",
]
_ALWAYS_OPEN = ["24시간", "상시", "연중무휴"]


@dataclass
class DayRule:
    days: list[int]                       # 0=월 … 6=일
    ranges: list[tuple[str, str]]         # [("09:00", "18:00")]


@dataclass
class OpeningHours:
    rules: list[DayRule] = field(default_factory=list)
    closed_days: list[int] = field(default_factory=list)
    always_open: bool = False
    exceptions: list[str] = field(default_factory=list)
    confidence: str = "none"              # high | low | none
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rules"] = [{"days": r.days, "ranges": r.ranges} for r in self.rules]
        return d

    def is_open_at(self, when: datetime) -> bool | None:
        """해당 시각에 열려 있는가. 판정 불가면 None."""
        if self.confidence == "none":
            return None
        if self.always_open:
            return True
        wd = when.weekday()
        if wd in self.closed_days:
            return False
        t = when.time()
        for rule in self.rules:
            if wd not in rule.days:
                continue
            for start, end in rule.ranges:
                if _within(t, start, end):
                    return True
        return False if self.rules else None


def _within(t: time, start: str, end: str) -> bool:
    s = _to_time(start)
    e = _to_time(end)
    if s is None or e is None:
        return False
    if e <= s:                      # 자정 넘김 (예: 18:00~02:00)
        return t >= s or t <= e
    return s <= t <= e


def _to_time(hhmm: str) -> time | None:
    try:
        h, m = hhmm.split(":")
        h, m = int(h), int(m)
        if h >= 24:                 # "24:00", "26:00" 같은 심야 표기
            h -= 24
        return time(h % 24, m)
    except (ValueError, AttributeError):
        return None


def _split_exceptions(text: str) -> tuple[str, list[str]]:
    """※·* 뒤의 단서 절을 본문에서 떼어낸다."""
    parts = re.split(r"[※*]", text)
    body = parts[0]
    notes = [p.strip() for p in parts[1:] if p.strip()]
    return body, notes


def _days_from(text: str) -> list[int]:
    """문장에서 요일 범위를 뽑는다. 못 찾으면 빈 리스트."""
    if "평일" in text:
        return [0, 1, 2, 3, 4]
    if "주말" in text:
        return [5, 6]
    if "매일" in text or "연중" in text:
        return list(range(7))

    days: set[int] = set()
    for a, b in _DAY_RANGE.findall(text):
        i, j = DAYS.index(a), DAYS.index(b)
        days.update(range(i, j + 1) if i <= j else list(range(i, 7)) + list(range(0, j + 1)))
    if not days:
        days.update(DAYS.index(d) for d in _DAY_SINGLE.findall(text))
    return sorted(days)


def parse_hours(use_time: str, closed_days: str = "") -> OpeningHours:
    """이용시간·휴무일 원문 → 구조화 시간표."""
    oh = OpeningHours()
    raw = (use_time or "").strip()

    if not raw:
        oh.reason = "이용시간 정보 없음"
        return oh

    body, oh.exceptions = _split_exceptions(raw)

    if any(k in raw for k in _ALWAYS_OPEN):
        oh.always_open = True
        oh.confidence = "high" if not oh.exceptions else "low"
        oh.reason = "상시 운영 표기"
        _apply_closed(oh, closed_days)
        return oh

    # 줄 단위로 끊고, 한 줄 안에서 요일 구간이 바뀌면
    # ("토요일 : 12:00~22:00 일요일 : 12:30~21:00") 그 지점에서 다시 끊는다
    segments: list[tuple[str | None, str]] = []
    for line in re.split(r"[\n\r]+", body):
        line = line.strip()
        if not line:
            continue
        scopes = list(_DAY_SCOPE.finditer(line))
        if not scopes:
            segments.append((None, line))
            continue
        if scopes[0].start() > 0:          # 첫 요일 표기 앞의 시간도 버리지 않는다
            segments.append((None, line[: scopes[0].start()]))
        for i, m in enumerate(scopes):
            end = scopes[i + 1].start() if i + 1 < len(scopes) else len(line)
            segments.append((m.group(), line[m.start(): end]))

    stated_days = False
    for scope, seg in segments:
        ranges = [
            (f"{int(h1):02d}:{m1}", f"{int(h2):02d}:{m2}")
            for h1, m1, h2, m2 in _TIME_RANGE.findall(seg)
        ]
        if not ranges:
            continue
        days = _days_from(scope) if scope else []
        if days:
            stated_days = True
        else:
            days = list(range(7))          # 요일 언급이 없으면 매일로 본다 (가정)
        oh.rules.append(DayRule(days=days, ranges=ranges))

    if not oh.rules:
        oh.confidence = "none"
        oh.reason = "시각 패턴을 찾지 못함"
        return oh

    # 요일 언급 없이 시간만 있는 경우, "매일"이라는 건 우리 가정이지 원문의 진술이 아니다
    has_ambiguity = any(k in raw for k in _AMBIGUOUS)

    if has_ambiguity:
        oh.confidence = "low"
        oh.reason = "예외 단서 포함 (" + ", ".join(
            k for k in _AMBIGUOUS if k in raw
        )[:60] + ")"
    elif not stated_days:
        oh.confidence = "low"
        oh.reason = "요일 표기 없음 — 매일 운영으로 가정"
    else:
        oh.confidence = "high"
        oh.reason = "요일·시각 모두 명시"

    _apply_closed(oh, closed_days)
    return oh


def _apply_closed(oh: OpeningHours, closed_days: str) -> None:
    """휴무일 문장에서 정기 휴무 요일을 뽑는다."""
    if not closed_days:
        return
    text = closed_days.strip()

    if "연중무휴" in text or "없음" in text:
        return

    # "매주 토요일,일요일"처럼 접두어가 한 번만 붙는 경우가 흔하므로
    # 접두어 유무와 무관하게 언급된 요일을 모두 모은다
    days = {DAYS.index(d) for d in _DAY_SINGLE.findall(text)}
    for a, b in _DAY_RANGE.findall(text):
        i, j = DAYS.index(a), DAYS.index(b)
        days.update(range(i, j + 1) if i <= j else list(range(i, 7)) + list(range(0, j + 1)))
    oh.closed_days = sorted(days)

    # "공휴일을 제외한 매주 월요일", "월요일이 휴일인 경우 정상개관" 같은 조건절은
    # 요일 규칙만으로 표현할 수 없다 — 한국 공휴일 달력이 있어야 판정된다
    if any(k in text for k in ["공휴일", "명절", "설", "추석", "대체"]):
        oh.exceptions.append(text)
        if oh.confidence == "high":
            oh.confidence = "low"
            oh.reason += " / 휴무일에 공휴일 조건절"


# ---------------------------------------------------------------- 실내·실외

# 소분류가 가장 정확한 신호다. "전시시설"은 건물이고 "도시공원"은 야외다.
# 제목 키워드보다 먼저 본다 — 제목에는 전시 제목이 들어가 도움이 안 되는 경우가 많다.
SUBCATEGORY_ENV = {
    # ── 실내 ──
    "문화관광 > 전시시설": "indoor",          # 박물관·미술관·기타전시 모두 포함
    "문화관광 > 공연시설": "indoor",
    "문화관광 > 교육시설": "indoor",
    "축제/공연/행사 > 공연": "indoor",
    "축제/공연/행사 > 행사 > 전시회": "indoor",
    "체험관광 > 공예체험": "indoor",
    "체험관광 > 웰니스관광": "indoor",
    "쇼핑 > 백화점": "indoor",
    "쇼핑 > 쇼핑몰": "indoor",
    "쇼핑 > 전문매장/상가": "indoor",
    "역사관광 > 역사유적지 > 근대건축물": "indoor",

    # ── 실외 ──
    "문화관광 > 도시공원": "outdoor",
    "문화관광 > 테마공원": "outdoor",
    "문화관광 > 레저스포츠시설": "outdoor",
    "축제/공연/행사 > 축제": "outdoor",
    "역사관광 > 역사유적지 > 고궁": "outdoor",
    "역사관광 > 역사유적지 > 성/문": "outdoor",
    "역사관광 > 역사유적지 > 사적지": "outdoor",
    "역사관광 > 역사유적지": "outdoor",
    "쇼핑 > 시장": "outdoor",
}

# 아래는 일부러 비워 둔다. 실제로 반반이라 규칙으로 정하면 거짓말이 된다.
#   문화관광 > 랜드마크관광   N서울타워는 전망대(실내)이자 남산(실외)이다
#   문화관광 > 기타문화관광지
#   역사관광 > 종교성지       사찰은 법당(실내)과 경내(실외)를 함께 갖는다
#   체험관광 > 기타체험·산업관광·전통체험
#   축제/공연/행사 (소분류 없음)
# 이 구간이 LLM 태깅이 실제로 값을 하는 자리다.

_OUTDOOR_HINT = [
    "공원", "둘레길", "한강", "광장", "야외", "숲길", "정원", "호수", "하천",
    "캠핑", "해변", "전망대", "거리축제", "야시장", "산책로", "등산", "트레킹",
    "고궁", "궁궐", "왕릉", "능원", "성곽", "노천", "야경", "퍼레이드",
    # 궁·능은 한 글자로 두면 "가능"·"기능" 안에서 걸린다. 이름을 직접 적는다.
    "경복궁", "창덕궁", "창경궁", "덕수궁", "경희궁", "운현궁", "종묘",
    "선릉", "정릉", "헌릉", "태릉", "의릉", "서오릉", "동구릉",
    "수문장", "교대의식", "한옥마을", "성벽", "돌담길",
    "플리마켓", "나들이", "피크닉", "유원지", "동물원", "식물원",
]
_INDOOR_HINT = [
    "박물관", "미술관", "전시관", "전시실", "전시장", "기념관", "도서관",
    "체육관", "실내", "카페", "레스토랑", "백화점", "쇼핑몰", "지하상가",
    "면세점", "극장", "영화관", "공연장", "아트홀", "아쿠아리움", "찜질방",
    "스파", "문화센터", "복합문화공간", "갤러리", "서점", "공방", "아트센터",
    "체험관", "과학관", "천문대", "웨딩홀", "상영관", "스튜디오", "홀",
]
_INDOOR_CATEGORIES = {"음식", "숙박"}
_OUTDOOR_CATEGORIES = {"자연관광"}


def _by_subcategory(category_path: str) -> str | None:
    """소분류 경로로 판정. 긴 경로부터 맞춰 본다."""
    path = (category_path or "").strip()
    if not path:
        return None
    for key in sorted(SUBCATEGORY_ENV, key=len, reverse=True):
        if path.startswith(key):
            return SUBCATEGORY_ENV[key]
    return None


def tag_environment(category: str, title: str, description: str,
                    tags: list[str] | None = None,
                    category_path: str = "") -> tuple[str, str]:
    """실내/실외/불명 태깅. (라벨, 근거) 반환.

    API에 없는 필드를 만들어 내는 일이라 규칙만으로는 한계가 뚜렷하다.
    'unknown'으로 남는 비율이 곧 LLM이 필요한 몫이다.

    순서가 중요하다. 소분류 → 대분류 → 키워드. 제목에는 전시 제목이 들어가
    도움이 안 되는 경우가 많고, 한 글자 키워드는 엉뚱한 단어 안에서 걸린다.
    """
    path = category_path or category
    if (env := _by_subcategory(path)):
        sub = path.split(">")[-1].strip() if ">" in path else path
        return env, f"분류 '{sub}'"

    cat = (category or "").split(">")[0].strip()
    if cat in _INDOOR_CATEGORIES:
        return "indoor", f"카테고리 '{cat}'"
    if cat in _OUTDOOR_CATEGORIES:
        return "outdoor", f"카테고리 '{cat}'"

    haystack = " ".join(filter(None, [title, description[:400], " ".join(tags or [])]))
    out_hits = [k for k in _OUTDOOR_HINT if k in haystack]
    in_hits = [k for k in _INDOOR_HINT if k in haystack]

    if out_hits and not in_hits:
        return "outdoor", f"키워드 {out_hits[:3]}"
    if in_hits and not out_hits:
        return "indoor", f"키워드 {in_hits[:3]}"
    if in_hits and out_hits:
        return "unknown", f"실내·실외 키워드 충돌 {in_hits[:2]} vs {out_hits[:2]}"
    return "unknown", "판정 근거 없음"
