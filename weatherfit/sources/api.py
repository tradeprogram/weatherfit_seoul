"""공식 비짓서울 API 소스.

VISITSEOUL_API_KEY 환경변수가 필요하다. 발급: https://api.visitseoul.net > API 키 발급 및 관리

목록 조회 응답에는 좌표·기간·운영시간이 없고 상세 조회에만 있다. 그래서
`list_ids`로 id를 모은 뒤 `fetch`로 건건이 상세를 받는 2단계가 불가피하며,
이것이 제안서에서 말하는 "사전 배치 수집이 필수 설계"의 근거다.
"""
from __future__ import annotations

import os
import time

import requests

from ..models import CATEGORIES, Content

BASE = "https://api-call.visitseoul.net/api/v1"


class ApiSource:
    name = "api"

    def __init__(self, api_key: str | None = None, delay: float = 0.2,
                 timeout: int = 30, retries: int = 3):
        self.api_key = api_key or os.environ.get("VISITSEOUL_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "VISITSEOUL_API_KEY가 없습니다. 비짓서울 API에서 키를 발급받아 "
                "환경변수로 지정하거나 ApiSource(api_key=...)로 넘기세요."
            )
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({
            "VISITSEOUL-API-KEY": self.api_key,
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json;charset=UTF-8",
        })

    def _post(self, path: str, payload: dict) -> dict:
        last = None
        for attempt in range(self.retries):
            try:
                r = self.session.post(BASE + path, json=payload, timeout=self.timeout)
                r.raise_for_status()
                body = r.json()
                if body.get("result_code") != 200:
                    raise RuntimeError(
                        f"API 오류 {body.get('result_code')}: {body.get('result_message')}"
                    )
                time.sleep(self.delay)
                return body
            except (requests.RequestException, ValueError) as e:
                last = e
                time.sleep(self.delay * (2**attempt))
        raise RuntimeError(f"요청 실패: {path} ({last})")

    def _list_page(self, category: str | None, lang: str, page: int) -> dict:
        payload: dict = {"lang_code_id": lang, "page_no": page, "sort_type": "latest"}
        if category:
            payload["com_ctgry_sn"] = CATEGORIES[category]
        return self._post("/contents/list", payload)

    def count(self, category: str | None = None, lang: str = "ko") -> int:
        body = self._list_page(category, lang, 1)
        return int(body.get("paging", {}).get("total_count", 0))

    def list_ids(self, category: str | None = None, lang: str = "ko",
                 max_pages: int | None = None) -> list[str]:
        ids: list[str] = []
        page = 1
        while True:
            body = self._list_page(category, lang, page)
            rows = body.get("data") or []
            if not rows:
                break
            ids.extend(row["cid"] for row in rows)

            paging = body.get("paging", {})
            total = int(paging.get("total_count", 0))
            size = int(paging.get("page_size", len(rows))) or len(rows)
            if len(ids) >= total or (max_pages and page >= max_pages):
                break
            page += 1
        return ids

    def fetch(self, cid: str, lang: str = "ko") -> Content:
        body = self._post("/contents/info", {"cid": cid})
        return self._to_content(body.get("data") or {}, lang)

    def _to_content(self, d: dict, lang: str) -> Content:
        extra = d.get("extra") or {}
        traffic = d.get("traffic") or {}

        def num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        path = d.get("cate_depth") or ""
        if isinstance(path, list):
            path = " > ".join(str(p) for p in path)

        return Content(
            cid=d.get("cid", ""),
            title=d.get("post_sj", ""),
            category=path.split(">")[0].strip(),
            category_path=path.strip(),
            summary=(d.get("sumry") or "").strip(),
            description=d.get("post_desc", ""),
            tags=list(d.get("tag") or []),
            schedule_start=d.get("schdul_info_bgnde", "") or "",
            schedule_end=d.get("schdul_info_endde", "") or "",
            use_time_raw=extra.get("cmmn_use_time", "") or "",
            closed_days_raw=extra.get("closed_days", "") or "",
            address=traffic.get("new_adres") or traffic.get("adres") or "",
            lon=num(traffic.get("map_position_x")),
            lat=num(traffic.get("map_position_y")),
            subway_raw=traffic.get("subway_info", "") or "",
            phone=extra.get("cmmn_telno", "") or "",
            homepage=extra.get("cmmn_hmpg_url", "") or "",
            fee_raw=extra.get("trrsrt_use_chrge_guidance")
                    or extra.get("trrsrt_use_chrge", "") or "",
            accessibility=list(extra.get("disabled_facility") or []),
            note=extra.get("cmmn_important", "") or "",
            lang=lang,
            source=self.name,
        )
