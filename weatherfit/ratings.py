"""평점 — **합성 데이터다. 실측이 아니다.**

이 파일이 만드는 값은 전부 지어낸 것이다. 구글 Places가 평점을 파는
유일한 창구인데 `rating` 필드가 Enterprise SKU라 월 1천 건까지만 무료고
그 위로는 1,000건당 $32~35다. 음식 1,259건을 한 번 받는 데 약 1만 원,
시계열을 쌓으려면 매달 그만큼이다.

공모전 산출물은 "이렇게 할 수 있다"를 보이는 자리고 실제 서비스 운영이
아니므로, 평점 자리에 합성값을 넣어 **파이프라인이 도는 것만** 보인다.

    지금            합성값으로 품질 하한이 작동하는 것을 보인다
    키를 받으면      fetch()를 실제 API로 바꾸면 나머지는 그대로 돈다

**숨기지 않는 장치를 함께 둔다.** 산출 파일에 `synthetic: true`가 박히고,
서버는 이 값을 화면에 내보내지 않으며, 아래 `is_real()`이 언제나 False를
돌려준다. 이 서비스는 무엇이 실측이고 무엇이 추정인지 밝히는 것을 원칙으로
삼았는데, 그 원칙을 지키는 코드가 정작 지어낸 값을 실측처럼 흘리면 원칙이
가장 아픈 자리에서 무너진다.

값은 난수가 아니라 cid에서 결정적으로 만든다. 돌릴 때마다 순위가 바뀌면
시연 중에 같은 화면이 두 번 안 나온다.

    python -m weatherfit.ratings build
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "ratings_mock.json"

# 국내 음식점 구글 평점은 대체로 3.6~4.7에 몰린다. 5.0이 흔하면 가짜 티가
# 나고, 3.0이 흔해도 그렇다. 가운데를 두껍게 만든다.
MID, SPREAD = 4.18, 0.34
LO, HI = 3.4, 4.9

FLOOR = 4.0        # 이 아래는 추천에서 뺀다 — 제안서가 말하는 '품질 하한'
# 리뷰가 이보다 적으면 평점을 믿을 수 없다. 30으로 뒀더니 리뷰 조건만으로
# 29.3%가 걸려 후보가 절반 아래로 떨어졌다. 하한의 뜻은 '못 믿을 것을
# 걸러내는 것'이지 '후보를 줄이는 것'이 아니다. 15면 평점 조건과 합쳐
# 65%가 남는다 — 걸러지는 것이 보이면서 시연할 것도 남는다.
MIN_REVIEWS = 15


def _unit(cid: str, salt: str) -> float:
    """cid에서 0~1을 결정적으로 뽑는다. 같은 가게는 늘 같은 값이다."""
    h = hashlib.sha256(f"{cid}|{salt}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64


def _gauss(u1: float, u2: float) -> float:
    """Box-Muller. 두 균등난수를 정규분포 하나로 바꾼다."""
    u1 = min(max(u1, 1e-9), 1 - 1e-9)
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def synth(cid: str) -> dict:
    """한 가게의 합성 평점과 리뷰 수. **지어낸 값이다.**"""
    z = _gauss(_unit(cid, "r1"), _unit(cid, "r2"))
    rating = round(min(max(MID + z * SPREAD, LO), HI), 1)

    # 리뷰 수는 로그정규를 흉내 낸다. 대부분 수십, 소수가 수천.
    lz = _gauss(_unit(cid, "n1"), _unit(cid, "n2"))
    reviews = int(min(max(math.exp(4.1 + lz * 1.25), 3), 9000))
    return {"rating": rating, "reviews": reviews, "synthetic": True}


def build(verbose: bool = True) -> Path:
    """음식·카페 전체에 합성 평점을 붙여 저장한다."""
    from .index import build_index
    from .report import load

    idx = build_index(load())
    want = [p for p in idx.places if p.content.category in ("음식",)]
    rows = {p.cid: synth(p.cid) for p in want}

    rs = [v["rating"] for v in rows.values()]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({
        "meta": {
            "synthetic": True,
            "warning": "이 파일의 평점과 리뷰 수는 전부 합성값입니다. "
                       "실측이 아니며 실제 가게의 평가와 무관합니다.",
            "why": "구글 Places의 rating 필드가 Enterprise SKU라 유료다. "
                   "공모전 산출물은 파이프라인 시연이 목적이므로 합성값을 쓴다.",
            "replace_with": "구글 Places Place Details (rating, userRatingCount)",
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "rows": len(rows), "floor": FLOOR, "min_reviews": MIN_REVIEWS,
            "median": round(st.median(rs), 2) if rs else 0.0,
        },
        "place": rows}, ensure_ascii=False), encoding="utf-8")
    if verbose:
        below = sum(1 for v in rows.values()
                    if v["rating"] < FLOOR or v["reviews"] < MIN_REVIEWS)
        print(f"합성 평점 {len(rows):,}건 → {CACHE}")
        print(f"  중앙값 {st.median(rs):.2f} · 하한({FLOOR}·리뷰{MIN_REVIEWS}) "
              f"미달 {below:,}건 ({below / len(rows) * 100:.1f}%)")
    return CACHE


_table: dict | None = None


def table() -> dict:
    global _table
    if _table is None:
        try:
            _table = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            _table = {"meta": {}, "place": {}}
    return _table


def reset() -> None:
    global _table
    _table = None


def is_real() -> bool:
    """이 평점이 실측인가. **합성인 동안은 언제나 False다.**

    화면과 API가 이 값을 보고 평점을 내보낼지 정한다. 실제 키를 붙일 때
    이 함수 하나만 바꾸면 나머지가 따라온다.
    """
    return not table().get("meta", {}).get("synthetic", True)


def of(cid: str) -> dict | None:
    row = (table().get("place") or {}).get(cid)
    return dict(row) if row else None


def passes(cid: str) -> bool:
    """품질 하한을 넘는가. 자료가 없으면 막지 않는다 — 모르는 것을
    '나쁘다'로 처리하면 멀쩡한 곳이 사라진다."""
    r = of(cid)
    if not r:
        return True
    return r["rating"] >= FLOOR and r["reviews"] >= MIN_REVIEWS


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    ap = argparse.ArgumentParser(description="평점 (합성)")
    ap.add_argument("action", choices=["build", "stats"])
    a = ap.parse_args()

    if a.action == "build":
        build()
        return
    t = table()
    if not t.get("place"):
        raise SystemExit("먼저 만들어야 합니다: python -m weatherfit.ratings build")
    m = t["meta"]
    print(f"⚠ {m['warning']}")
    rs = [v["rating"] for v in t["place"].values()]
    print(f"\n{m['rows']:,}건 · 중앙값 {m['median']} · "
          f"하한 {m['floor']} · 최소 리뷰 {m['min_reviews']}")
    import collections
    hist = collections.Counter(round(r * 2) / 2 for r in rs)
    for k in sorted(hist):
        print(f"  {k:4.1f}  {'█' * (hist[k] * 40 // max(hist.values()))} {hist[k]}")


if __name__ == "__main__":
    main()
