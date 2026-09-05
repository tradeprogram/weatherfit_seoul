"""영어로 내보내기 — 우리가 만든 문장을 경계에서 옮긴다.

외국인 관광객을 보는 서비스라고 해 놓고 화면이 한국어면 그 자체가 약점이다.
비짓서울 콘텐츠는 이미 영어가 붙어 있는데(제목·요약 95.8%), **우리가 생성한
문장**은 전부 한국어였다 — 판정 사유, 근거, 트렌드 라벨, 혼잡도.

호출부마다 언어를 넘기는 방법도 있지만, 판정과 코스 생성이 깊이 얽혀 있어
서명을 다 바꿔야 한다. 대신 **경계에서 옮긴다.** 우리가 문장을 만드는 쪽이라
나올 수 있는 표현이 닫힌 집합이고, 그래서 구절 표로 덮을 수 있다.

이 방식의 위험은 하나다 — 새 문장을 추가하고 표에 안 넣으면 조용히 한국어가
새어 나간다. 그래서 **영어 응답에 한글이 남으면 테스트가 깨지게** 해 두었다.
그 테스트가 이 파일의 유일한 보증이다.

일본어·중국어는 지금 다루지 않는다. 반쯤 번역된 언어를 고르게 두는 것보다
없는 편이 낫다.
"""
from __future__ import annotations

import re

LANGS = ("ko", "en")

# 낱말이 아니라 **구절**로 바꾼다. 낱말 단위로 치환하면 '실외 부적합'이
# 'outdoor 부적합'처럼 반쯤 남는다. 긴 것부터 먼저 맞춰야 짧은 것이
# 긴 것을 잘라먹지 않는다.
PHRASES: dict[str, str] = {
    # ── 판정 단계
    "판정 불가": "Unverifiable",
    "판정불가": "Unverifiable",
    "통과": "Open",
    "탈락": "Excluded",
    "기간": "Dates",
    "운영": "Hours",
    "날씨": "Weather",
    "이동": "Travel",

    # ── 기간
    "상시 콘텐츠 (기간 없음)": "Always open (no set dates)",
    "일정 표기를 해석할 수 없음": "Schedule text could not be parsed",
    "시작 예정": "starts",
    "종료": "ended",
    "진행 중": "Currently running",
    "오늘 마지막": "Last day today",

    # ── 운영시간
    "운영시간 판정 불가": "Opening hours could not be determined",
    "현재 휴무 또는 영업시간 밖": "Closed now or outside opening hours",
    "영업 중": "Open now",
    "시간 미상": "Hours unknown",
    "에 문을 열어 두는 곳입니다": " and open at this time",
    "도착이면 식사 시간에 맞습니다": " arrival fits a mealtime",
    "곧 닫습니다": "closing soon",

    # ── 날씨
    "실외 가능": "Outdoor OK",
    "실외 부적합": "Not suited to outdoors",
    "실외 유지": "outdoors kept",
    "실내외 불명": "Indoor/outdoor unknown",
    "실내": "Indoor",
    "실외": "Outdoor",
    "맑음": "Clear",
    "흐림": "Cloudy",
    "구름많음": "Mostly cloudy",
    "구름 많음": "Mostly cloudy",
    "비/눈": "Rain or snow",
    # 한 글자짜리는 처음엔 뺐다. 이제 앞뒤 한글 경계가 붙어 '비빔밥'이나
    # '분위기' 가운데를 자르지 않으므로 되살린다.
    "비": "Rain",
    "눈": "Snow",
    "소나기": "Showers",
    "빗방울": "Drizzle",
    "없음": "None",
    "폭염": "Extreme heat",
    "한파": "Extreme cold",
    "강수": "precipitation",
    "야외 활동에 무리가 없습니다": "fine for being outdoors",
    "지금 갈 수 있음": "You can go now",
    "지금 날씨": "Current weather",
    "주변에 열린 곳": "open nearby",
    "기준 위치": "Reference point",
    "주변": "nearby",

    # ── 트렌드
    "뜨는 중": "Rising",
    "최근 급등": "Recent spike",
    "올랐다 진정": "Rose, now easing",
    "꾸준함": "Steady",
    "식는 중": "Cooling",
    "자료 없음": "No data",
    "기준 흔들림": "Baseline shifted",
    "아직 조용함": "Still quiet",

    # ── 혼잡 (서울시 실시간 도시데이터가 한국어만 준다)
    # '덜 붐빔'을 따로 두지 않으면 '붐빔'만 잡혀 '덜 Crowded'가 된다.
    # 긴 키가 먼저 맞으므로 이 한 줄이 그걸 막는다.
    "덜 붐빔": "Less crowded",
    "혼잡 관측 지역 밖 — 순위에 영향 없음":
        "Outside the live-crowding areas — no effect on ranking",
    "붐빔": "Crowded",
    "약간 붐빔": "Somewhat crowded",
    "보통": "Moderate",
    "여유": "Not busy",

    # ── 끼니
    "식사": "Meals",
    "아침": "Breakfast",
    "점심": "Lunch",
    "저녁": "Dinner",
    "아침(7~11시)에 넣을 식당을 찾지 못했습니다.":
        "Could not fit a place to eat into breakfast (07–11).",
    "점심(11~15시)에 넣을 식당을 찾지 못했습니다.":
        "Could not fit a place to eat into lunch (11–15).",
    "저녁(17~21시)에 넣을 식당을 찾지 못했습니다.":
        "Could not fit a place to eat into dinner (17–21).",

    # ── 장소 검색
    "지명·장소로 찾기 (예: 성수, 경복궁)":
        "Search an area or place (e.g. Seongsu, Gyeongbokgung)",
    "장소 검색": "Place search",
    "찾지 못했습니다": "No match",
    "내 위치로": "Back to my location",
    "자세히 보기": "See details",
    "지역": "Area",
    "행정동": "Neighborhood",

    # ── 근거 항목
    "가까움": "Nearby",
    "정보 충실": "Well documented",
    "알려진 곳": "Well known",
    "요즘 뜨는": "Trending",
    "취향 일치": "Matches taste",
    "여행 스타일": "Travel style",
    "추이 자료 없음 — 순위에 영향 없음": "No trend data — does not affect ranking",
    "작년 같은 달 대비": "vs. the same month last year",
    "자료 없음 — 충실도로 대신": "No data — using content quality instead",

    # ── 이동
    "도보": "Walk",
    "대중교통": "Transit",
    "걷기": "Walking",
    "실측": "Measured",
    "추정": "Estimated",

    # ── 코스 역할
    "오늘의 앵커": "Today's anchor",
    "식사·카페": "Meal or cafe",
    "둘러볼 곳": "Something to see",
    "내 위치": "My location",
    "앵커": "anchor",

    # ── 자치구·행정동은 옮기지 않는다. 고유명사라 음차가 맞고, 그건
    #    번역이 아니라 표기 규칙이다. 다만 접미사만 로마자로 붙인다.
    "기상청 초단기실황": "KMA nowcast",
    "격자": "grid",
    "직선": "straight-line",
    "우회율": "detour factor",
    "평균 이동속도 기반 추정": "estimated from average travel speed",
    "보행자 경로": "pedestrian route",
    "도로망 보행 경로": "road-network walking route",
    "실시간 최적경로": "real-time optimal route",
    "직선거리 기반 추정": "estimated from straight-line distance",
    "직선거리 추정": "straight-line estimate",
    "환승": "transfers",

    # 자치구는 로마자로. 번역이 아니라 표기 규칙이다.
    "종로구": "Jongno-gu",
    "중구": "Jung-gu",
    "강남구": "Gangnam-gu",
    "용산구": "Yongsan-gu",
    "마포구": "Mapo-gu",
    "서초구": "Seocho-gu",
    "영등포구": "Yeongdeungpo-gu",
    "송파구": "Songpa-gu",
    "성동구": "Seongdong-gu",
    "서대문구": "Seodaemun-gu",
    "광진구": "Gwangjin-gu",
    "노원구": "Nowon-gu",
    "성북구": "Seongbuk-gu",
    "동대문구": "Dongdaemun-gu",
    "강서구": "Gangseo-gu",
    "은평구": "Eunpyeong-gu",
    "강북구": "Gangbuk-gu",
    "동작구": "Dongjak-gu",
    "관악구": "Gwanak-gu",
    "중랑구": "Jungnang-gu",
    "도봉구": "Dobong-gu",
    "강동구": "Gangdong-gu",
    "양천구": "Yangcheon-gu",
    "구로구": "Guro-gu",
    "금천구": "Geumcheon-gu",

    "운영시간 확정": "hours confirmed",
    "미설정 — 기본값": "not set — falling back to a default",
    "으로 판정합니다": " for the check",
    "기본값": "default",
    "설명 상세": "detailed description",
    "태그 다수": "many tags",
    "무장애 정보": "accessibility info",
    "근처에 지금 열린 행사가 없어 상시 콘텐츠로 시작합니다.":
        "No events are running nearby, so the plan starts from always-open places.",
    "실내라 날씨의 영향을 받지 않습니다.":
        "Indoors — the weather does not affect it.",

    # ── 코스 설명
    "라 날씨의 영향을 받지 않습니다.": " — weather does not affect it.",
    "날씨가 바뀌면 여기로 피할 수 있습니다.": "A fallback if the weather turns.",
    "일정 사이에 쉬어 가기 좋습니다.": "A good pause between stops.",
    "근처에 지금 열린 행사가 없어 상시 콘텐츠로 시작합니다.":
        "No events are running nearby, so the plan starts from always-open places.",
    "시각 패턴을 찾지 못함": "No time pattern found",
    "걸어서": "walk",
    "태그 다섯 개 넘음": "over five tags",
    "무장애 정보 있음": "accessibility info",
    "홈페이지": "website",
    "시간 확정": "hours confirmed",
    "설명이 긴 편": "long description",
    "위키백과": "Wikipedia",
    "보행자도로": "footpath",
    "도착": "arrive",
    "수도권": "Seoul metro",

    # ── 에이전트 도구 기록·근거·행동
    "시청": "City Hall",
    "지표면온도": "surface temperature",
    "상위": "top",
    "녹지": "greenery",
    "지표 열부담": "surface heat load",
    "서울 평균 수준": "around the Seoul average",
    "합성": "composite",
    "보관함에 저장": "Save it",
    "판정 근거": "Why this call",
    "제외": "excluded",
    "직접 지정": "set manually",

    "중": "of",
    "사용": "used",
    "일정 보기": "See the plan",
    "다시 짜기": "Rebuild",
    "기준으로 짜 봤어요.": " — here is a plan.",
    "날씨는": "Weather:",
    "예요.": ".",
    "마음에 안 드는 곳이 있으면": "If you do not like a stop, say",
    "라고 말씀해 주세요.": ".",
    "다른 곳으로": "swap it",
    "조건 없음": "no conditions given",
    "일정 보기": "See the plan",
    "다시 짜기": "Rebuild",
    "저장": "Save",
    "실외 활동 가능": "outdoors are fine",
    "으로": " by",
}

# 숫자에 붙는 단위는 낱말로 바꾸면 다른 말을 망가뜨린다. '조회'가 '조x'가
# 되고 '분위기'가 'min위기'가 된다. 숫자가 앞에 붙을 때만 바꾼다.
UNITS: list[tuple[str, str]] = [
    # 숫자가 가운데 든 문장은 구절 표에 담을 수 없다. 통째로 규칙을 둔다 —
    # 반만 옮겨져 "뒤쪽 1 places을 뺐습니다"가 되는 것이 제일 나쁘다.
    (r"실제 이동시간으로 다시 계산해 뒤쪽 ([\d,]+)곳을 뺐습니다\.",
     r"Recalculated with real travel times; dropped the last \1 stop(s)."),
    (r"지금\s*([\d,]+)\s*~\s*([\d,]+)\s*명",
     r"\1–\2 people right now"),
    (r"외지인\s*([\d.]+)\s*%", r"\1% visitors"),
    (r"(\d{1,2}:\d{2})\s*이후 여유", r"eases after \1"),
    (r"([\d,]+)\s*명", r"\1 people"),
    (r"연\s*([\d,]+)\s*회\s*조회", r"\1 views/yr"),
    (r"연\s*([\d,]+)\s*회", r"\1 views/yr"),
    (r"([\d,]+)\s*회\s*조회", r"\1 views"),
    (r"([\d,]+)\s*개월", r"\1 months"),
    (r"([\d,]+)\s*구간", r"\1 legs"),
    (r"([\d,]+)\s*정거장", r"\1 stops"),
    (r"([\d,]+)\s*호선", r"Line \1"),
    (r"([\d,]+)\s*곳", r"\1 places"),
    (r"([\d,]+)\s*건", r"\1 items"),
    (r"([\d,]+)\s*회", r"\1 views"),
    (r"([\d,]+)\s*시간", r"\1 hrs"),
    (r"([\d,]+)\s*분", r"\1 min"),
    (r"([\d,]+)\s*개", r"\1"),
]
_UNITS = [(re.compile(a), b) for a, b in UNITS]

# 긴 구절부터 맞춘다. 그리고 **짧은 키에는 경계를 건다.**
#
# 한국어는 낱말 사이에 경계 표시가 없어 부분 문자열이 그대로 걸린다.
# '도보'가 '듣도보도못한말' 안에서 잡혀 '듣Walk도못한말'이 됐고, '붐빔'이
# '붐빔은'에서 잡혀 'Crowded은'이 됐다. 세 글자 이하 키는 앞뒤가 한글이면
# 낱말 가운데를 자르는 것이므로 건너뛴다.
_SHORT = 3


def _pat(k: str) -> str:
    e = re.escape(k)
    return f"(?<![가-힣]){e}(?![가-힣])" if len(k) <= _SHORT else e


_ORDER = sorted(PHRASES, key=len, reverse=True)
_RE = re.compile("|".join(_pat(k) for k in _ORDER)) if _ORDER else None

_HANGUL = re.compile(r"[가-힣]")


def has_korean(s: str) -> bool:
    return bool(_HANGUL.search(s or ""))


def to_en(s: str) -> str:
    """우리가 만든 문장을 영어로. 표에 없는 한국어는 그대로 남는다 —
    조용히 지우면 뜻이 사라지고, 테스트가 그걸 잡아 준다."""
    if not s or not _RE:
        return s
    # 단위를 먼저 처리한다. 구절 표가 '조회'를 먼저 지우면 '588회 조회'를
    # 한 덩어리로 볼 기회가 사라져 '588 views views'가 된다.
    out = s
    for rx, rep in _UNITS:
        out = rx.sub(rep, out)
    return _RE.sub(lambda m: PHRASES[m.group(0)], out)


def line(s: str, lang: str) -> str:
    return to_en(s) if lang == "en" else s


# 응답에서 **우리가 만든** 필드만 옮긴다. 비짓서울 원문(use_time_raw 등)은
# 손대지 않는다 — 영업시간 원문을 어설프게 번역하면 없는 정보를 만든다.
OURS = ("verdict", "stage", "reason", "verdict_reason", "line", "label",
        "note", "level", "trend_label", "role_name", "desc", "sky",
        "pty", "environment_text", "summary", "kind")

# 외부에서 그대로 받은 것은 옮기지 않는다. 경로 안내의 '새문안로'는
# 거리 이름이라 번역 대상이 아니고, 옮기면 오히려 못 찾는다.
KEEP = ("steps",)


# 혼잡 안내문은 옮기지 않고 **다시 쓴다.**
#
# 서울시가 주는 문장은 "사람들이 몰려있을 가능성이 매우 크고 많이 붐빈다고
# 느낄 수 있어요..." 같은 긴 한국어다. 구절로 옮기면 '붐빔은'이 'Crowded은'이
# 되는 식으로 반쯤 남는다. 등급이 네 개뿐이니 영어 문장을 우리가 갖는 편이
# 짧고 정확하다.
CROWD_MSG = {
    "붐빔": "Very likely packed. Expect to bump into people in the busiest spots.",
    "약간 붐빔": "Getting busy. You may feel some congestion in places.",
    "보통": "A normal amount of people. Walking around is comfortable.",
    "여유": "Unlikely to be crowded. You can move around freely.",
}


def crowd_message(level: str, lang: str) -> str:
    return CROWD_MSG.get(level, "") if lang == "en" else ""


def deep_en(obj, lang: str = "ko"):
    """전부 우리가 쓴 글인 덩어리는 문자열을 통째로 옮긴다.

    도구 실행 기록·근거·행동 라벨이 그렇다. 여기엔 장소명이 안 들어가므로
    필드를 가릴 필요가 없고, 가리려 들면 'title'처럼 문맥에 따라 뜻이
    갈리는 키에서 반드시 틀린다.
    """
    if lang != "en":
        return obj
    if isinstance(obj, dict):
        return {k: deep_en(v, lang) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deep_en(v, lang) for v in obj]
    return to_en(obj) if isinstance(obj, str) else obj


def localize(obj, lang: str = "ko"):
    """응답 전체를 훑어 우리가 만든 문자열만 옮긴다."""
    if lang != "en":
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in KEEP:
                out[k] = v
            elif isinstance(v, str) and k in OURS:
                out[k] = to_en(v)
            else:
                out[k] = localize(v, lang)
        # 혼잡 안내문은 옮기지 않고 다시 쓴다
        if "level" in out and "message" in out:
            out["message"] = CROWD_MSG.get(obj.get("level", ""), out.get("message", ""))
        return out
    if isinstance(obj, list):
        return [to_en(v) if isinstance(v, str) else localize(v, lang) for v in obj]
    return obj
