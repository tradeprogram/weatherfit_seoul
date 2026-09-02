"""관심사 분석 — 무엇을 좋아하는지 알아내고 그에 맞춰 고른다.

"전시 보고 싶어"는 명시적 신호다. 하지만 사람은 대개 그렇게 말하지 않는다.
어떤 장소를 눌러 봤는지, 어떤 걸 빼 달라고 했는지가 더 정확하다.

세 겹으로 취향을 모은다.

    말한 것    대화에서 뽑은 관심사 (chat.Intent.interests)
    고른 것    상세를 열어 본 장소, 좋아요를 누른 장소
    뺀 것      관심없음을 누른 장소

각 장소는 **태그 벡터**를 갖는다. 비짓서울 콘텐츠에는 해시태그가 붙어 있어
(#북촌한옥마을 #전통 #한복체험) 이걸 그대로 쓰면 분류보다 훨씬 촘촘한
취향 표현이 된다. 좋아한 장소들의 태그를 모아 프로필을 만들고,
후보와의 코사인 유사도를 점수에 더한다.

프로필은 서버에 저장하지 않는다. 화면이 들고 있다가 요청에 실어 보낸다.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .index import Place

# 태그가 없는 콘텐츠도 많다. 제목·설명에서 뽑아 보완한다.
_TOKEN = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")

# 어느 장소에나 붙어 의미가 없는 말
STOPWORDS = {
    "서울", "서울시", "위치", "이용", "가능", "안내", "운영", "제공", "다양", "각종",
    "시간", "관람", "무료", "유료", "예약", "문의", "홈페이지", "주차", "지하철",
    "가까운", "있습니다", "합니다", "때문", "그리고", "하지만", "또한", "특히",
}

# 분류를 취향 축으로 바꾼 것. 사용자가 "조용한", "활기찬" 같은 말을 쓸 때 쓴다.
MOOD_TAGS = {
    "조용": ("박물관", "미술관", "도서관", "고궁", "한옥", "정원", "산책"),
    "활기": ("시장", "축제", "거리", "쇼핑몰", "야시장", "공연"),
    "이색": ("체험", "공방", "전시", "팝업", "테마"),
    "전통": ("한옥", "고궁", "전통", "국악", "한복", "종묘", "서원"),
    "현대": ("디자인", "현대미술", "복합문화", "플래그십", "갤러리"),
}


def tokens_of(place: Place, limit: int = 24) -> list[str]:
    """장소의 태그 벡터. 해시태그를 우선하고 모자라면 제목·요약에서 채운다."""
    c = place.content
    out = [t.strip() for t in c.tags if t and len(t.strip()) >= 2]
    if len(out) < 6:
        text = f"{c.title} {c.summary} {(c.category_path or c.category)}"
        out += [w for w in _TOKEN.findall(text)
                if w not in STOPWORDS and len(w) >= 2]
    return list(dict.fromkeys(out))[:limit]


@dataclass
class Taste:
    """사용자 취향 프로필. 화면이 들고 다니는 값이라 직렬화가 쉬워야 한다."""
    categories: dict[str, float] = field(default_factory=dict)
    tags: dict[str, float] = field(default_factory=dict)
    disliked: list[str] = field(default_factory=list)     # cid
    liked: list[str] = field(default_factory=list)        # cid

    def to_dict(self) -> dict:
        return {"categories": self.categories, "tags": self.tags,
                "disliked": self.disliked, "liked": self.liked}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Taste":
        d = d or {}
        return cls(
            categories={str(k): float(v)
                        for k, v in (d.get("categories") or {}).items()},
            tags={str(k): float(v) for k, v in (d.get("tags") or {}).items()},
            disliked=[str(x) for x in (d.get("disliked") or [])][-60:],
            liked=[str(x) for x in (d.get("liked") or [])][-60:],
        )

    @property
    def is_empty(self) -> bool:
        return not self.categories and not self.tags

    # ---------- 학습 ----------

    def declare(self, interests: list[str], weight: float = 1.0) -> None:
        """말로 밝힌 관심사. 가장 센 신호로 둔다."""
        for cat in interests or []:
            self.categories[cat] = self.categories.get(cat, 0.0) + weight

    def like(self, place: Place, weight: float = 1.0) -> None:
        self._absorb(place, weight)
        if place.cid not in self.liked:
            self.liked.append(place.cid)
        self.liked = self.liked[-60:]

    def dislike(self, place: Place, weight: float = 0.7) -> None:
        self._absorb(place, -weight)
        if place.cid not in self.disliked:
            self.disliked.append(place.cid)
        self.disliked = self.disliked[-60:]

    def view(self, place: Place) -> None:
        """상세를 열어 본 것. 약한 긍정 신호."""
        self._absorb(place, 0.25)

    def _absorb(self, place: Place, weight: float) -> None:
        cat = place.content.category
        self.categories[cat] = self.categories.get(cat, 0.0) + weight
        for t in tokens_of(place, limit=12):
            self.tags[t] = self.tags.get(t, 0.0) + weight * 0.5
        self._trim()

    def _trim(self, keep: int = 120) -> None:
        """오래 쓰면 태그가 무한정 늘어난다. 약한 것부터 잘라 낸다."""
        if len(self.tags) > keep:
            top = sorted(self.tags.items(), key=lambda kv: -abs(kv[1]))[:keep]
            self.tags = dict(top)

    # ---------- 적용 ----------

    def affinity(self, place: Place) -> float:
        """-1 ~ 1. 취향과 얼마나 맞는가."""
        if place.cid in self.disliked:
            return -1.0
        if self.is_empty:
            return 0.0

        cat = self.categories.get(place.content.category, 0.0)
        cat_max = max((abs(v) for v in self.categories.values()), default=1.0) or 1.0
        cat_score = cat / cat_max

        tag_score = 0.0
        if self.tags:
            toks = tokens_of(place, limit=16)
            if toks:
                hit = sum(self.tags.get(t, 0.0) for t in toks)
                norm = math.sqrt(len(toks)) * (
                    max((abs(v) for v in self.tags.values()), default=1.0) or 1.0)
                tag_score = max(-1.0, min(1.0, hit / (norm or 1.0)))

        return max(-1.0, min(1.0, cat_score * 0.6 + tag_score * 0.4))

    # ---------- 설명 ----------

    def describe(self) -> str:
        """사용자에게 보여줄 한 줄. 무엇을 학습했는지 숨기지 않는다."""
        if self.is_empty:
            return ""
        cats = [k for k, v in sorted(self.categories.items(), key=lambda kv: -kv[1])
                if v > 0][:2]
        tags = [k for k, v in sorted(self.tags.items(), key=lambda kv: -kv[1])
                if v > 0.4][:3]
        bits = []
        if cats:
            bits.append(" · ".join(cats))
        if tags:
            bits.append(" ".join("#" + t for t in tags))
        return " / ".join(bits)


def mood_interests(message: str) -> list[str]:
    """'조용한 데', '이색적인 곳' 같은 분위기 표현 → 태그 힌트."""
    out: list[str] = []
    for mood, tags in MOOD_TAGS.items():
        if mood in message:
            out.extend(tags)
    return out


def apply_taste(cands, taste: Taste, weight: float = 0.35):
    """후보 목록을 취향 순으로 재정렬한다. 싫다고 한 것은 뺀다."""
    if taste.is_empty:
        return [t for t in cands if t[0].cid not in taste.disliked]
    scored = []
    for item in cands:
        p = item[0]
        a = taste.affinity(p)
        if a <= -0.99:
            continue
        scored.append((a, item))
    scored.sort(key=lambda t: -t[0])
    return [t[1] for t in scored]
