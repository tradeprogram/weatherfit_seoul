"""지금은 서울의 지금이다.

`datetime.now()`는 프로세스가 도는 기계의 지역시간을 준다. 배포처는
Render 싱가포르 리전이고 TZ를 주지 않아 UTC로 돈다. 그래서 서울이
오후 4시일 때 서버는 오전 7시로 판정했다.

    /api/stats  지금 갈 수 있는 곳  1,507건 → 215건
    운영시간 탈락                     562건 → 1,854건

이 서비스가 하는 말이 '지금 갈 수 있는가' 하나뿐인데 그 '지금'이
아홉 시간 어긋나 있었다. 화면은 브라우저가 시각을 실어 보내 멀쩡했고,
그래서 시각을 안 보내는 경로에서만 조용히 틀렸다 — 통계, 챗봇 인사,
직접 부르는 API.

한국은 1988년 이후 서머타임이 없어 UTC+9가 언제나 정확하다. tzdata를
깔지 않아도 되는 쪽을 고른다 — 윈도우에는 zoneinfo 자료가 없다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now() -> datetime:
    """서울의 지금. 나머지 코드가 순진한 datetime을 쓰므로 맞춰 준다."""
    return datetime.now(KST).replace(tzinfo=None)
