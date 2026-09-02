"""공개 카탈로그(api.visitseoul.net/contents/standard) 소스.

공식 API 키가 없을 때 쓴다. 같은 콘텐츠를 HTML로 노출하며 상세 페이지는
dl/dt/dd 구조라 필드 추출이 안정적이다. 공식 API로 갈아끼울 때를 대비해
`api.py`와 같은 메서드 이름을 쓴다.
"""
from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from ..models import CATEGORIES, CID_PREFIX, Content

BASE = "https://api.visitseoul.net"
LIST_URL = BASE + "/contents/standard/list"
VIEW_URL = BASE + "/contents/standard/view/{cid}"

# 상세 페이지 dt 라벨 → Content 필드
_FIELD_MAP = {
    "요약": "summary",
    "내용": "description",
    "장소": "place",
    "전화번호": "phone",
    "홈페이지": "homepage",
    "주소": "address",
    "교통정보": "subway_raw",
    "이용시간": "use_time_raw",
    "휴무일": "closed_days_raw",
    "이용요금": "fee_raw",
    "이것만은 꼭!": "note",
}

_DATE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")
# 어권에 따라 라벨이 한국어/영어로 나온다 (경도 · longitude)
_LON = re.compile(r"(?:경도|longitude)\s*[:：]?\s*([-\d.]+)", re.I)
_LAT = re.compile(r"(?:위도|latitude)\s*[:：]?\s*([-\d.]+)", re.I)
_CID_IN_LIST = r"goViewPage\('({prefix}[^']+)'\);\" class=\"board-link\""


class CatalogSource:
    name = "catalog"

    def __init__(self, delay: float = 0.35, timeout: int = 30, retries: int = 3):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "weatherfit-seoul/0.1 (contest research)"

    # ---------- HTTP ----------

    def _get(self, url: str, **params) -> str:
        last = None
        for attempt in range(self.retries):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                r.raise_for_status()
                time.sleep(self.delay)
                return r.text
            except requests.RequestException as e:  # 네트워크 흔들림은 재시도
                last = e
                time.sleep(self.delay * (2**attempt))
        raise RuntimeError(f"요청 실패: {url} ({last})")

    # ---------- 목록 ----------

    def count(self, category: str | None = None, lang: str = "ko") -> int:
        """카테고리(또는 전체)의 총 건수."""
        html = self._get(
            LIST_URL, lang="ko", lang_code=lang,
            com_ctgry=CATEGORIES.get(category, "") if category else "",
        )
        m = re.search(r"총<strong>(\d+)</strong>건", html)
        return int(m.group(1)) if m else 0

    def list_ids(self, category: str | None = None, lang: str = "ko",
                 max_pages: int | None = None) -> list[str]:
        """카테고리의 콘텐츠 id를 페이지 순회로 모은다."""
        total = self.count(category, lang)
        pages = (total + 19) // 20                      # 페이지당 20건
        if max_pages:
            pages = min(pages, max_pages)

        code = CATEGORIES.get(category, "") if category else ""
        ids: list[str] = []
        seen: set[str] = set()
        for page in range(1, pages + 1):
            html = self._get(LIST_URL, lang="ko", lang_code=lang,
                             com_ctgry=code, pageNo=page)
            found = re.findall(
                _CID_IN_LIST.format(prefix=CID_PREFIX.get(lang, "KO")), html)
            if not found:                                # 빈 페이지면 조기 종료
                break
            for cid in found:
                if cid not in seen:
                    seen.add(cid)
                    ids.append(cid)
        return ids

    # ---------- 상세 ----------

    def fetch(self, cid: str, lang: str = "ko") -> Content:
        html = self._get(VIEW_URL.format(cid=cid), lang="ko", pageNo=1)
        return self._parse_detail(cid, html, lang)

    def _parse_detail(self, cid: str, html: str, lang: str) -> Content:
        soup = BeautifulSoup(html, "lxml")
        for junk in soup(["script", "style"]):
            junk.decompose()
        main = soup.find("main") or soup.body

        item = Content(cid=cid, title="", lang=lang, source=self.name)

        # 첫 h2는 사이트 로고이므로 그다음 h2가 콘텐츠 제목이다
        for h in main.find_all("h2"):
            if "logo" not in (h.get("class") or []):
                item.title = h.get_text(strip=True)
                break

        for dt in main.select("dl dt"):
            label = dt.get_text(strip=True)
            # dt 하나에 dd가 여러 개 붙는다 (태그는 dd마다 하나, 좌표는 경도/위도가 따로)
            dds = []
            sib = dt.find_next_sibling()
            while sib is not None and sib.name == "dd":
                dds.append(sib)
                sib = sib.find_next_sibling()
            if not dds:
                continue

            texts = [dd.get_text("\n", strip=True) for dd in dds]
            joined = " ".join(t.replace("\n", " ") for t in texts)

            if label == "태그":
                item.tags = [t.lstrip("#").strip() for t in texts if t.strip()]
                continue

            if label == "장애인 편의시설":
                item.accessibility = [
                    s.strip() for t in texts for s in t.split(",") if s.strip()
                ]
                continue

            if label == "일정정보":
                dates = _DATE.findall(joined)
                if dates:
                    item.schedule_start = ".".join(dates[0])
                    item.schedule_end = ".".join(dates[-1])
                continue

            if label == "지도 좌표":
                if (m := _LON.search(joined)):
                    item.lon = float(m.group(1))
                if (m := _LAT.search(joined)):
                    item.lat = float(m.group(1))
                continue

            attr = _FIELD_MAP.get(label)
            if attr:
                # 줄바꿈을 살려 원문 형태를 보존한다 (정규화 난이도 측정에 필요)
                setattr(item, attr, "\n".join(t for t in texts if t))

        crumb = main.select_one(".path, .cate")
        if crumb:
            # 중첩 태그 때문에 구분자가 겹쳐 나온다: "문화관광 > > > 전시시설"
            parts = [p.strip() for p in crumb.get_text(">", strip=True).split(">")]
            parts = [p for p in parts if p]
            item.category_path = " > ".join(parts)
            item.category = parts[0] if parts else ""

        return item
