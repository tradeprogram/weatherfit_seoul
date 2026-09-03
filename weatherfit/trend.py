"""트렌드 지능 — 2026 서울관광 트렌드 VITALITY를 계산 가능한 값으로.

서울관광재단의 2026 트렌드 분석은 여덟 글자다. Vibrant Content City ·
Immersive Local Life · Tailored Smart Travel · Ambient Wellness ·
Living Emotion · Inclusive Choice · Trusted Global Hub · Your Seoul.

이걸 추천에 쓰려면 장소마다 숫자가 있어야 한다. 그런데 여덟 개를 그냥
장소에 매기면 안 되는 것이 둘 있다.

    Tailored Smart Travel   '초개인화'는 장소의 성질이 아니다. 경복궁이
                            얼마나 개인화됐냐고 물으면 답이 없다. 이건
                            **서비스가 하는 일**이다.
    Your Seoul              '나만의 서울'도 마찬가지다. 같은 장소가 누구에겐
                            나만의 서울이고 누구에겐 아니다.

그래서 이 둘은 장소 벡터에서 빼고 서비스 축으로 따로 잰다(`service_axes`).
장소에 매길 수 있는 것은 여섯이다.

두 번째 원칙: **키워드로 지어내지 말고 이미 재 둔 값에 연결한다.**
리서치 초안은 전부 이름·설명 키워드 매칭을 제안했지만, 우리는 그보다
나은 재료를 이미 갖고 있다.

    Ambient Wellness    위성 NDVI(식생지수). 그늘과 녹지의 실측값이다.
                        '힐링'이라는 낱말이 설명에 있느냐보다 정확하다.
    Trusted Global Hub  `accessibility`(무장애 시설 배열)와 지하철 정보,
                        번역 보유 어권 수. 전부 API가 준 실제 필드다.
    Inclusive Choice    그 행정동에 실제로 몇 분류가 모여 있는가.
                        우리는 3,788건을 행정동에 다 붙여 두었다.

나머지 셋(Vibrant · Immersive Local · Living Emotion)은 분류 체계와
해시태그로 판정한다. 소분류를 먼저 보는 것은 `tag_environment`와 같은
이유다 — 제목에는 전시 '제목'이 들어가 도움이 안 되는 경우가 많다.

**Inclusive Choice는 뺐다.** '럭셔리도 가성비도, 단체도 혼행도'를 재려면
가격대와 수용 인원이 있어야 하는데 비짓서울 API에 없다. 행정동의 분류
다양성으로 대신해 봤더니 상위가 전부 종로1·2·3·4가동이었다 — '선택의 폭'이
아니라 '도심인가'를 재고 있었다. 못 재는 것을 그럴듯한 대리값으로 채우면
그때부터 점수는 근거가 아니라 장식이 된다.

값을 모르면 0이 아니라 None으로 둔다. 0은 '해당 없음'이고 None은
'모른다'인데, 둘을 섞으면 평균이 조용히 틀어진다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 장소에 매길 수 있는 여섯 축 (VITALITY 8 - 서비스 축 2)
PLACE_AXES = (
    "vibrant_content",     # V 콘텐츠 실감 — 공연·전시·K컬처·촬영지
    "immersive_local",     # I 로컬 몰입 — 동네·시장·골목, 살아보는 여행
    "ambient_wellness",    # A 일상형 회복 — 한강·숲·공원·찜질방·한방
    "living_emotion",      # L 감정 체류 — 분위기·감성·야경·카페
    "trusted_global",      # T 안심의 흐름 — 무장애·무슬림 친화
)
# 장소가 아니라 서비스가 책임지는 축
SERVICE_AXES = ("tailored_smart", "your_seoul")
# 잴 수 없어 뺀 축. 아래 주석 참고.
UNMEASURABLE = ("inclusive_choice",)

AXIS_LABEL = {
    "vibrant_content": "콘텐츠 실감",
    "immersive_local": "로컬 몰입",
    "ambient_wellness": "일상형 회복",
    "living_emotion": "감정 체류",
    "inclusive_choice": "열린 선택지",
    "trusted_global": "안심의 흐름",
    "tailored_smart": "초개인화",
    "your_seoul": "나만의 서울",
}

# 관심 4축. VITALITY와 별개로 '무엇을' 좋아하는지를 잡는다.
INTEREST_AXES = ("k_culture", "k_beauty", "k_food", "local_life")
INTEREST_LABEL = {
    "k_culture": "K-컬처", "k_beauty": "K-뷰티",
    "k_food": "미식", "local_life": "로컬 라이프",
}

# ------------------------------------------------------------------ 분류 근거
# 소분류를 먼저 본다. 제목은 전시 '제목'이라 도움이 안 되는 경우가 많다.

SUB_VIBRANT = ("공연시설", "전시시설", "축제", "행사", "공연", "전시회",
               "박람회", "콘서트", "영화관", "복합문화")
SUB_LOCAL = ("전통시장", "재래시장", "시장", "골목", "한옥", "공방",
             "테마거리", "쇼핑거리", "지역상권")
SUB_WELLNESS = ("도시공원", "자연경관", "산", "하천", "공원", "찜질방",
                "스파", "한방", "온천", "산책로", "둘레길", "수목원")
SUB_EMOTION = ("카페/찻집", "카페", "찻집", "전망", "야경", "테마카페")
SUB_BEAUTY = ("뷰티", "미용", "화장품", "네일", "헤어", "피부", "성형")
SUB_FOOD = ("음식", "한식", "카페", "주점", "제과", "외국식", "뷔페")
SUB_CULTURE = ("문화관광", "역사관광", "박물관", "미술관", "고궁", "유적",
               "전통", "공연", "전시")

# 서울관광재단이 트렌드 문서에서 로컬 몰입의 사례로 든 동네
LOCAL_HOTSPOTS = ("성수", "연남", "익선", "을지로", "망원", "서촌", "북촌",
                  "해방촌", "문래", "샤로수", "송리단", "용리단")

# 태그 중 실제로 쓸 만한 것만 남긴다. 전수를 세어 보고 고른 값이다.
# '감성'은 5건, '조용'은 0건, '포토'는 1건이라 신호가 되지 않는다.
TAG_VIBRANT = ("전시", "공연", "축제", "케이팝", "K-POP", "드라마", "촬영지",
               "굿즈", "팝업", "서울전시", "서울축제")     # 전시 308 · 공연 134
TAG_LOCAL = ("오래가게", "전통시장", "레트로", "노포", "골목")  # 오래가게 115
# 서울시가 직접 지정한 무슬림 친화 업소. VITALITY의 '무슬림 친화·유니버설'을
# 우리가 추측하지 않고 그대로 쓸 수 있는 유일한 필드다.
TAG_HALAL = ("살람서울", "할랄")                              # 152 + 11
# 정확 일치로 세어 본 값. 다 합쳐 2.4%뿐이지만 정확도가 높다.
TAG_EMOTION = ("야경", "힐링", "루프탑", "한강뷰", "산책", "뷰맛집",
               "야경명소", "감성카페", "노을", "전망")

# 무장애 항목은 최대 5종(접근가능·장애인화장실·전용주차·엘리베이터·안내).
# 82.4%가 0개라 이 축의 실질적인 변별력은 여기서 나온다.
ACCESS_MAX = 5


@dataclass
class TrendVector:
    """한 장소의 트렌드 좌표. 모르는 축은 None으로 남긴다."""
    axes: dict = field(default_factory=dict)        # 6축, 0~1 또는 None
    interests: dict = field(default_factory=dict)   # 4축, 0~1 또는 None
    basis: dict = field(default_factory=dict)       # 축 → 무엇을 보고 정했나

    def to_dict(self) -> dict:
        return {"axes": self.axes, "interests": self.interests,
                "basis": self.basis}

    @property
    def known(self) -> int:
        return sum(1 for v in self.axes.values() if v is not None)


def _hit(text: str, words) -> bool:
    """분류 경로에는 부분 일치가 맞다 — '문화관광 > 전시시설'처럼 구조가 있다."""
    return any(w in text for w in words)


def _tagged(tagset: set, words) -> bool:
    """태그에는 정확 일치를 쓴다.

    부분 일치로 두면 '데이트코스'가 '데이트'에 걸려 전통공예명품전이
    '감정 체류 0.85'가 된다. 실제로 그랬다. 태그는 낱말이지 문장이 아니다.
    """
    return bool(tagset & set(words))


def tag_place(place, ndvi: float | None = None) -> TrendVector:
    """장소 하나의 트렌드 벡터.

    ndvi는 그 장소가 속한 행정동의 위성 식생지수다. 없으면 해당 축을
    None으로 남긴다 — 0으로 채우면 '녹지가 없다'는 거짓말이 된다.
    """
    c = place.content
    path = c.category_path or c.category
    tagset = {t.strip() for t in (c.tags or []) if t and t.strip()}
    tags = " ".join(tagset)
    title = c.title or ""
    v = TrendVector()

    def put(axis, value, why):
        v.axes[axis] = value
        v.basis[axis] = why

    # V 콘텐츠 실감 — 분류가 먼저, 태그가 보조
    if _hit(path, SUB_VIBRANT):
        put("vibrant_content", 0.9, f"분류 {path.split(' > ')[-1]}")
    elif _tagged(tagset, TAG_VIBRANT):
        put("vibrant_content", 0.6, "콘텐츠 태그")
    elif c.is_short_event:
        put("vibrant_content", 0.7, "기간이 정해진 행사")
    else:
        put("vibrant_content", 0.1, "상시 콘텐츠")

    # I 로컬 몰입 — 동네 이름이 근거가 되는 유일한 축이다
    dong = getattr(place, "dong", "") or ""
    if "오래가게" in tagset:
        put("immersive_local", 0.95, "서울시 오래가게 지정")
    elif _hit(path, SUB_LOCAL) or _tagged(tagset, TAG_LOCAL):
        put("immersive_local", 0.9, "시장·골목·전통 분류")
    elif _hit(dong + title, LOCAL_HOTSPOTS):
        put("immersive_local", 0.7, f"로컬 핫스폿 {dong}")
    elif "음식" in path and "외국식" not in path:
        put("immersive_local", 0.5, "동네 음식점")
    else:
        put("immersive_local", 0.2, "일반 관광 콘텐츠")

    # A 일상형 회복 — 위성 NDVI가 있으면 그걸 쓴다
    if _hit(path, ("찜질방", "스파", "한방", "온천")):
        put("ambient_wellness", 0.9, "회복 시설 분류")
    elif ndvi is not None:
        outdoor = place.environment == "outdoor"
        green = max(0.0, min(1.0, (ndvi + 0.05) / 0.55))
        put("ambient_wellness", round(green * (0.9 if outdoor else 0.45), 2),
            f"위성 식생지수 {ndvi:.2f}" + ("· 실외" if outdoor else "· 실내"))
    elif _hit(path, SUB_WELLNESS):
        put("ambient_wellness", 0.7, "공원·자연 분류")
    else:
        v.axes["ambient_wellness"] = None
        v.basis["ambient_wellness"] = "녹지 자료 없음"

    # L 감정 체류
    # 태그로는 안 잡힌다('감성' 5건, '조용' 0건). 소분류와 제목이 실질이다.
    if _tagged(tagset, TAG_EMOTION) or _hit(title, ("야경", "전망", "루프탑")):
        put("living_emotion", 0.85, "야경·전망·힐링")
    elif "카페/찻집" in path:
        put("living_emotion", 0.75, "카페·찻집")
    elif _hit(path, SUB_EMOTION):
        put("living_emotion", 0.6, "머무는 공간")
    elif _hit(path, ("주점", "베이커리", "제과")):
        put("living_emotion", 0.45, "머물 수 있는 음식점")
    elif place.environment == "indoor" and "음식" not in path:
        put("living_emotion", 0.3, "실내 관람 공간")
    else:
        put("living_emotion", 0.15, "감성 신호 없음")

    # T 안심의 흐름
    # 지하철 안내는 93.6%, 2개 어권 보유는 89.5%가 갖고 있어 변별력이 없다.
    # 실제로 갈리는 것은 무장애 정보(17.6%)와 무슬림 친화 지정(4.0%)이다.
    score, why = 0.0, []
    if c.accessibility:
        n = len(c.accessibility)
        score += 0.75 * min(1.0, n / ACCESS_MAX)
        why.append(f"무장애 {n}종")
    if _tagged(tagset, TAG_HALAL):
        score += 0.25
        why.append("무슬림 친화 지정")
    if not why and c.subway_raw:
        score, why = 0.15, ["지하철 안내만 있음"]
    put("trusted_global", round(min(1.0, score), 2),
        " · ".join(why) or "접근성 정보 없음")

    # 관심 4축
    v.interests["k_food"] = 0.9 if "음식" in path else (
        0.4 if _tagged(tagset, ("맛집", "먹거리", "한식맛집")) else 0.05)
    v.interests["k_beauty"] = 0.9 if _hit(path, SUB_BEAUTY) or _tagged(
        tagset, ("K뷰티", "뷰티", "화장품", "스킨케어")) else 0.05
    v.interests["k_culture"] = 0.85 if _hit(path, SUB_CULTURE) else 0.1
    v.interests["local_life"] = v.axes.get("immersive_local") or 0.2
    return v


# ------------------------------------------------------------------ 서비스 축

def service_axes(course: dict, taste=None) -> dict:
    """Tailored Smart Travel과 Your Seoul은 장소가 아니라 서비스가 답한다.

    장소마다 '초개인화 0.8'을 매기는 건 뜻이 없다. 대신 이 일정이 실제로
    얼마나 그 사람에게 맞춰졌는지를 잰다.
    """
    steps = course.get("steps") or []
    if not steps:
        return {a: 0.0 for a in SERVICE_AXES}

    # 취향이 실제로 순위를 바꿨는가
    tailored = 0.0
    if taste is not None and not taste.is_empty:
        used = [s.get("why") or {} for s in steps]
        got = [p["value"] for w in used for p in (w.get("parts") or [])
               if p.get("key") == "taste"]
        tailored = round(sum(max(0.0, g) for g in got) / max(len(got), 1), 2)

    # 흔한 곳만 돌지 않았는가 — 남들 다 가는 곳뿐이면 '나만의 서울'이 아니다
    from .popularity import scores as pop_scores
    pops = [pop_scores().get(s["cid"], 0.0) for s in steps]
    common = sum(1 for p in pops if p > 0.6) / len(pops)
    return {"tailored_smart": tailored,
            "your_seoul": round(1.0 - common, 2)}
