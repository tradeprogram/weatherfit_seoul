"""콘텐츠 수집 CLI.

    python -m weatherfit.collect --category 축제 --limit 200
    python -m weatherfit.collect --all
    VISITSEOUL_API_KEY=... python -m weatherfit.collect --source api --all

수집 결과는 data/raw/{category}.jsonl 에 한 줄 한 건으로 쌓인다.
이미 받은 cid는 건너뛰므로 중단 후 재실행해도 이어서 받는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import CATEGORIES, Content
from .sources import get_source

DATA = Path(__file__).resolve().parent.parent / "data" / "raw"


def _load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    done.add(json.loads(line)["cid"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return done


def collect(category: str, source_name: str = "catalog", lang: str = "ko",
            limit: int | None = None) -> int:
    src = get_source(source_name)
    DATA.mkdir(parents=True, exist_ok=True)
    # 어권별로 파일을 나눈다. 한 파일에 섞이면 언어 전환이 불가능해진다.
    out = DATA / (f"{category}.jsonl" if lang == "ko"
                  else f"{category}.{lang}.jsonl")

    done = _load_done(out)
    ids = src.list_ids(category, lang=lang)
    todo = [c for c in ids if c not in done]
    if limit:
        todo = todo[:limit]

    print(f"[{category}] 전체 {len(ids)}건 · 기수집 {len(done)}건 · 이번에 {len(todo)}건")

    written = 0
    with out.open("a", encoding="utf-8") as f:
        for i, cid in enumerate(todo, 1):
            try:
                item: Content = src.fetch(cid, lang=lang)
            except Exception as e:                      # 한 건 실패로 전체를 멈추지 않는다
                print(f"  ! {cid} 실패: {e}", file=sys.stderr)
                continue
            if not item.category:
                item.category = category
            f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
            f.flush()
            written += 1
            if i % 25 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}")
    print(f"[{category}] {written}건 저장 → {out}")
    return written


def main() -> None:
    p = argparse.ArgumentParser(description="비짓서울 콘텐츠 수집")
    p.add_argument("--category", choices=sorted(CATEGORIES), help="수집할 카테고리")
    p.add_argument("--all", action="store_true", help="전체 카테고리 수집")
    p.add_argument("--source", default="catalog", choices=["catalog", "api"])
    p.add_argument("--lang", default="ko")
    p.add_argument("--limit", type=int, help="카테고리당 최대 건수 (시험용)")
    args = p.parse_args()

    if not args.category and not args.all:
        p.error("--category 또는 --all 중 하나가 필요합니다")

    targets = sorted(CATEGORIES) if args.all else [args.category]
    total = sum(collect(c, args.source, args.lang, args.limit) for c in targets)
    print(f"\n합계 {total}건")


if __name__ == "__main__":
    main()
