"""콘텐츠 소스.

`CatalogSource`(공개 카탈로그)와 `ApiSource`(공식 API)는 같은 인터페이스를 갖는다.

    count(category, lang) -> int
    list_ids(category, lang, max_pages) -> list[str]
    fetch(cid, lang) -> Content

키가 발급되면 `get_source("api")`로 바꾸기만 하면 나머지 파이프라인은 그대로 돈다.
"""
from __future__ import annotations

from .catalog import CatalogSource


def get_source(name: str = "catalog", **kwargs):
    if name == "catalog":
        return CatalogSource(**kwargs)
    if name == "api":
        from .api import ApiSource
        return ApiSource(**kwargs)
    raise ValueError(f"알 수 없는 소스: {name!r} (catalog | api)")


__all__ = ["CatalogSource", "get_source"]
