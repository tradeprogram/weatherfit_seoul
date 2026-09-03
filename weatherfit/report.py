"""제안서에 쓸 근거 수치를 뽑는다.

    python -m weatherfit.report
    python -m weatherfit.report --rain          # 우천 시나리오
    python -m weatherfit.report --at "2026-09-05 14:00"

산출물: data/report.md
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from .models import Content
from .normalize import parse_hours, tag_environment
from .validate import Weather, check_period, evaluate

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "report.md"


def load(lang: str = "ko") -> list[Content]:
    """어권별 수집 파일을 읽는다. 한국어는 {분류}.jsonl, 나머지는 {분류}.{lang}.jsonl

    `.jsonl.gz`도 읽는다. 배포본에는 압축본만 넣는다 — 원본 19.2MB가
    5.0MB로 줄어 저장소에 넣을 만해지고, 그래야 서버가 뜰 때 콘텐츠가
    있다. 같은 분류에 둘 다 있으면 압축 안 된 쪽을 쓴다(새로 수집한 것).
    """
    import gzip

    suffix = ".jsonl" if lang == "ko" else f".{lang}.jsonl"
    found: dict[str, pathlib.Path] = {}
    for path in sorted(RAW.glob("*.jsonl")) + sorted(RAW.glob("*.jsonl.gz")):
        name = path.name[:-3] if path.name.endswith(".gz") else path.name
        if not name.endswith(suffix):
            continue
        stem = name[: -len(suffix)]
        if lang == "ko" and "." in stem:
            continue                      # 다른 어권 파일은 건너뛴다
        # 압축본이 먼저 잡혀도 원본이 있으면 원본을 쓴다
        if stem not in found or not path.name.endswith(".gz"):
            found[stem] = path

    items: list[Content] = []
    for path in sorted(found.values()):
        opener = (gzip.open if path.name.endswith(".gz") else open)
        with opener(path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(Content.from_dict(json.loads(line)))
    return items


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total else "—"


def _table(rows: list[tuple[str, int, str]], head: tuple[str, str, str]) -> list[str]:
    out = [f"| {head[0]} | {head[1]} | {head[2]} |", "|---|---:|---:|"]
    out += [f"| {a} | {b:,} | {c} |" for a, b, c in rows]
    return out


def build(items: list[Content], when: datetime, weather: Weather) -> str:
    total = len(items)
    by_cat = Counter(i.category for i in items)
    L: list[str] = []

    L += [
        "# 웨더핏 서울 — 근거 수치",
        "",
        f"- 생성: {datetime.now():%Y-%m-%d %H:%M}",
        f"- 판정 기준 시각: **{when:%Y-%m-%d(%a) %H:%M}**",
        f"- 기준 날씨: **{weather.describe()}**",
        f"- 대상: **{total:,}건** " + ", ".join(f"{k} {v:,}" for k, v in by_cat.most_common()),
        "",
    ]

    # ---------- 1. 운영정보 정규화 ----------
    L += ["## 1. 운영시간, 규칙만으로 어디까지 되나", ""]
    conf = Counter()
    reasons = Counter()
    no_info = 0
    for i in items:
        oh = parse_hours(i.use_time_raw, i.closed_days_raw)
        conf[oh.confidence] += 1
        reasons[oh.reason.split(" (")[0]] += 1
        if not i.use_time_raw.strip():
            no_info += 1

    L += _table(
        [
            ("`high` — 요일·시각이 모두 명시되어 그대로 판정 가능", conf["high"], _pct(conf["high"], total)),
            ("`low` — 파싱은 되지만 예외 단서나 가정이 섞임", conf["low"], _pct(conf["low"], total)),
            ("`none` — 시각 패턴 자체가 없어 판정 불가", conf["none"], _pct(conf["none"], total)),
        ],
        ("정규화 결과", "건수", "비율"),
    )
    machine_ready = conf["high"]
    L += [
        "",
        f"**규칙만으로 확정되는 건 {machine_ready:,}건({_pct(machine_ready, total)})뿐이다.** "
        f"나머지 {total - machine_ready:,}건({_pct(total - machine_ready, total)})은 "
        "예외 단서를 해석하거나 누락된 요일을 추론해야 하며, 이것이 LLM이 필요한 몫이다.",
        "",
        "### 신뢰도를 떨어뜨린 이유",
        "",
    ]
    L += _table(
        [(r or "—", c, _pct(c, total)) for r, c in reasons.most_common(8)],
        ("사유", "건수", "비율"),
    )
    L += ["", f"이용시간이 아예 비어 있는 콘텐츠: **{no_info:,}건** ({_pct(no_info, total)})", ""]

    # ---------- 2. 실내외 태깅 ----------
    L += ["## 2. 실내·실외, API에 없는 필드 만들기", ""]
    env = Counter()
    for i in items:
        label, _ = tag_environment(i.category, i.title, i.description, i.tags,
                                   i.category_path)
        env[label] += 1
    L += _table(
        [(k, env[k], _pct(env[k], total)) for k in ("indoor", "outdoor", "unknown")],
        ("판정", "건수", "비율"),
    )
    L += [
        "",
        f"규칙 기반으로 {env['unknown']:,}건({_pct(env['unknown'], total)})이 "
        "실내인지 실외인지 가려지지 않는다. 날씨 대응의 전제가 되는 속성이므로 "
        "이 구간이 곧 LLM 태깅이 담당해야 할 범위다.",
        "",
    ]

    # ---------- 3. 시의성 ----------
    dated = [i for i in items if i.is_dated_event]
    if dated:
        L += ["## 3. 기간이 있는 콘텐츠의 시의성", ""]
        ended = sum(1 for i in dated if check_period(i, when.date()).reason.endswith("종료"))
        upcoming = sum(1 for i in dated if check_period(i, when.date()).reason.endswith("예정"))
        live = sum(1 for i in dated if check_period(i, when.date()).ok is True)
        L += _table(
            [
                ("이미 종료", ended, _pct(ended, len(dated))),
                ("진행 중", live, _pct(live, len(dated))),
                ("시작 예정", upcoming, _pct(upcoming, len(dated))),
            ],
            ("상태", "건수", "비율"),
        )
        L += [
            "",
            f"기간이 지정된 콘텐츠 {len(dated):,}건 중 **{ended:,}건({_pct(ended, len(dated))})이 "
            "이미 끝난 행사다.** 콘텐츠 목록 조회 응답에는 기간 필드가 없으므로, "
            "상세 조회 없이는 이 구분이 불가능하다.",
            "",
        ]

    # ---------- 4. 유효성 필터 효과 ----------
    L += ["## 4. 필터를 걸면 후보가 얼마나 줄어드나", ""]
    stages = Counter()
    passed = 0
    unknown = 0
    for i in items:
        v, _ = evaluate(i, when, weather)
        if v.ok is True:
            passed += 1
        elif v.ok is None:
            unknown += 1
            stages[f"판정불가 · {v.stage}"] += 1
        else:
            stages[f"탈락 · {v.stage}"] += 1

    L += _table(
        [("**지금 갈 수 있음**", passed, _pct(passed, total))]
        + [(k, v, _pct(v, total)) for k, v in stages.most_common()],
        ("결과", "건수", "비율"),
    )
    L += [
        "",
        f"{total:,}건이 **{passed:,}건**으로 줄어든다. "
        f"판정 불가 {unknown:,}건({_pct(unknown, total)})은 정보 부족 때문이며, "
        "이 구간을 줄이는 것이 정규화 계층의 목표다.",
        "",
        "---",
        "",
        "*수치는 공개 카탈로그에서 수집한 실데이터를 그대로 집계한 것이다. "
        "공식 API 키 발급 후 `--source api`로 동일하게 재현된다.*",
    ]
    return "\n".join(L)


def main() -> None:
    # 윈도우 콘솔 기본 코드페이지(cp949)로는 '—' 같은 문자를 못 찍는다
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    p = argparse.ArgumentParser(description="근거 수치 리포트 생성")
    p.add_argument("--at", help='판정 기준 시각 "YYYY-MM-DD HH:MM" (기본: 현재)')
    p.add_argument("--rain", action="store_true", help="우천 시나리오로 판정")
    p.add_argument("--temp", type=float, default=22.0)
    args = p.parse_args()

    when = datetime.strptime(args.at, "%Y-%m-%d %H:%M") if args.at else datetime.now()
    weather = (Weather(temp_c=args.temp, precip_mm=3.0, pty="비", sky="흐림")
               if args.rain else Weather(temp_c=args.temp))

    items = load()
    if not items:
        raise SystemExit("data/raw 가 비어 있습니다. 먼저 python -m weatherfit.collect 를 실행하세요.")

    text = build(items, when, weather)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n→ {OUT}")


if __name__ == "__main__":
    main()
