"""외국인 유동인구 — 관심이 아니라 **방문**을 잰다.

지금까지 쓴 신호(위키백과 조회수)는 '새로 알아보는 사람'이었다. 가는
사람이 아니다. 그 한계를 계속 문서에 적어 두기만 했는데, 서울시가
행정동 단위 단기체류 외국인 생활인구를 2017년부터 공개하고 있었다.

    행정동 424개 × 시간 단위 × 일자 × 9년
    기준일ID · 시간대구분 · 행정동코드 · 총생활인구수
             · 중국인체류인구수 · 중국외외국인체류인구수
    공공누리 1유형(상업 이용·변경 가능)

이 자료로 비로소 '방문 모멘텀' 축이 선다. 관심 축과 어긋나는 지점이
곧 발견이다 — 조회수는 주는데 실제로는 가는 곳, 그 반대인 곳.

**밤에 붐비는 것은 관광이 아니다.** 처음 받아서 시간대별로 합쳐 보니
가장 붐비는 시각이 01·02·00시로 나왔다. 생활인구는 '지금 그 자리에
있는 사람'이라 새벽에는 숙소에 있는 사람이 잡힌다. 명동이 새벽에
최고치인 것은 호텔이지 관광 혼잡이 아니다. 그래서 낮 시간대만 세고,
새벽값은 숙박 기준선으로 따로 남겨 둔다.

    python -m weatherfit.footfall fetch     # 43개월 내려받아 집계 (약 10분)
    python -m weatherfit.footfall stats
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "footfall" / "dong_month.json"

LIST_URL = "https://data.seoul.go.kr/dataList/OA-14993/S/1/datasetView.do"
FILE_URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false"
INF_ID, INF_SEQ = "OA-14993", "3"
UA = {"User-Agent": "Mozilla/5.0 (weatherfit-seoul; tourism research)"}

# 관광객이 밖에 나와 있는 시간. 새벽을 넣으면 숙박 인구를 관광 혼잡으로
# 읽게 된다 — 실제로 그렇게 나왔다.
DAY_HOURS = tuple(f"{h:02d}" for h in range(11, 19))
NIGHT_HOURS = ("03", "04", "05")      # 숙박 기준선


def _file_list(session) -> list[tuple[str, str]]:
    """내려받기 가능한 (seq, 파일명) 목록. 페이지가 곧 명세다."""
    r = session.get(LIST_URL, timeout=60)
    r.raise_for_status()
    pairs = re.findall(
        r"downloadFile\('(\d+)'\)[^>]*>\s*(TEMP_FOREIGNER_DONG_[0-9]+\.zip)", r.text)
    seen, out = set(), []
    for seq, fn in pairs:
        if fn not in seen:
            seen.add(fn)
            out.append((seq, fn))
    return sorted(out, key=lambda t: t[1])


def _grab(session, seq: str) -> bytes:
    r = session.post(FILE_URL, timeout=180, headers={"Referer": LIST_URL},
                     data={"infId": INF_ID, "seq": seq, "infSeq": INF_SEQ})
    r.raise_for_status()
    return r.content


def _digest(raw: bytes) -> dict:
    """한 달치를 행정동별로 접는다. 원자료는 31만 행이라 들고 있을 수 없다."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    out: dict[str, dict] = {}
    for name in z.namelist():
        if not name.lower().endswith(".csv"):
            continue
        text = z.read(name).decode("utf-8-sig", errors="replace")
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 6 or not row[0].isdigit():
                continue
            ym, hour, dong = row[0][:6], row[1], row[2]
            try:
                tot, cn = float(row[3] or 0), float(row[4] or 0)
            except ValueError:
                continue
            key = f"{dong}|{ym}"
            a = out.setdefault(key, {"day": 0.0, "night": 0.0,
                                     "cn": 0.0, "days": set()})
            if hour in DAY_HOURS:
                a["day"] += tot
                a["cn"] += cn
                a["days"].add(row[0])
            elif hour in NIGHT_HOURS:
                a["night"] += tot
    # 달마다 날 수가 달라 합계로 두면 2월이 늘 낮게 나온다. 하루 평균으로.
    for a in out.values():
        n = max(len(a["days"]), 1)
        a["day"] = round(a["day"] / n, 2)
        a["night"] = round(a["night"] / n, 2)
        a["cn"] = round(a["cn"] / n, 2)
        a.pop("days")
    return out


def fetch(since: str = "202301", verbose: bool = True) -> Path:
    """월 단위 파일을 받아 행정동×월로 접어 저장한다.

    연 단위 묶음(2017~2022)은 건드리지 않는다. 한 파일에 1년이 들어 있어
    무겁고, 전년 동월비에는 2023년 이후 43개월로 이미 충분하다.
    """
    se = requests.Session()
    se.headers.update(UA)
    files = [(s, f) for s, f in _file_list(se)
             if re.search(r"_(\d{6})\.zip$", f)
             and re.search(r"_(\d{6})\.zip$", f).group(1) >= since]
    if verbose:
        print(f"월 단위 {len(files)}개 · {files[0][1]} ~ {files[-1][1]}")

    series: dict[str, dict] = {}
    for i, (seq, fn) in enumerate(files, 1):
        try:
            got = _digest(_grab(se, seq))
        except Exception as e:
            print(f"  {fn} 실패: {e}", flush=True)
            continue
        for key, a in got.items():
            dong, ym = key.split("|")
            series.setdefault(dong, {})[ym] = a
        if verbose:
            print(f"  {i:>3}/{len(files)} {fn} · 행정동 {len(got)}", flush=True)
        time.sleep(0.3)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    months = sorted({m for d in series.values() for m in d})
    CACHE.write_text(json.dumps({
        "meta": {"source": "서울 열린데이터광장 행정동 단위 서울 생활인구"
                           "(단기체류 외국인) OA-14993",
                 "license": "공공누리 1유형",
                 "hours_day": list(DAY_HOURS), "hours_night": list(NIGHT_HOURS),
                 "note": "낮 시간대(11~18시) 하루 평균. 새벽값은 숙박 기준선.",
                 "built_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                 "dong": len(series), "months": months},
        "series": series}, ensure_ascii=False), encoding="utf-8")
    if verbose:
        print(f"완료 · 행정동 {len(series)} × {len(months)}개월 → {CACHE}")
    return CACHE


_table: dict | None = None


def table() -> dict:
    global _table
    if _table is None:
        try:
            _table = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            _table = {"meta": {}, "series": {}}
    return _table


def reset() -> None:
    global _table
    _table = None


def monthly(dong: str, field: str = "day") -> list[float]:
    """한 행정동의 월별 계열. 달이 비면 그 달은 통째로 뺀다 — 0으로
    채우면 '아무도 안 왔다'가 되어 없는 급감을 만든다."""
    got = (table().get("series") or {}).get(dong) or {}
    return [got[m][field] for m in sorted(got) if got[m].get(field)]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    ap = argparse.ArgumentParser(description="외국인 유동인구")
    ap.add_argument("action", choices=["fetch", "stats"])
    ap.add_argument("--since", default="202301")
    a = ap.parse_args()

    if a.action == "fetch":
        fetch(since=a.since)
        return

    t = table()
    if not t.get("series"):
        raise SystemExit("먼저 받아야 합니다: python -m weatherfit.footfall fetch")
    m = t["meta"]
    print(f"{m['dong']}개 행정동 × {len(m['months'])}개월 "
          f"({m['months'][0]} ~ {m['months'][-1]})")
    print(f"낮 {m['hours_day'][0]}~{m['hours_day'][-1]}시 하루 평균 · "
          f"출처 {m['source']}")
    last = m["months"][-1]
    top = sorted(((v[last]["day"], d) for d, v in t["series"].items()
                  if last in v), reverse=True)[:8]
    print(f"\n{last} 낮 시간대 외국인 상위")
    for v, d in top:
        print(f"  {d}  {v:>12,.0f}명·시")


if __name__ == "__main__":
    main()
