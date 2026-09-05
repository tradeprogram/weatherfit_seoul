"""제안서 시각자료 — 주장마다 숫자를 붙인다.

제안서 2절(구상 및 제안 배경)의 네 가지 주장을 그림으로 옮긴다. 말로만
쓰면 심사자가 검증할 수 없고, 검증할 수 없는 주장은 배점에서 약하다.

    그림 1  왜 지금 이 문제가 있는가   콘텐츠 쏠림 · 판정 깔때기 · 운영정보 결손
    그림 2  왜 순위가 아니라 변화인가  수준↔변동 · 관심↔방문

모든 값은 실제 산출물에서 읽는다. 손으로 적은 수는 하나도 없다 —
자료가 바뀌면 그림도 바뀌어야 하고, 그러라고 스크립트로 둔다.

    python -m weatherfit.figures
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, ticker

ROOT = Path(__file__).resolve().parent.parent
OUT = Path.home() / "Desktop" / "하수범_공모전" / "서울관광재단" / "figure"

# 기존 논문 피규어에서 뽑은 색. 슬레이트 4단 + 러스트 강조 하나.
INK = "#1e2a33"
MID = "#7d8b96"
LIGHT = "#aebac4"
PALE = "#d5dce1"
RUST = "#b5442e"
GRID = "#e8ebee"


def _setup() -> None:
    have = {f.name for f in font_manager.fontManager.ttflist}
    serif = ("Noto Serif KR" if "Noto Serif KR" in have
             else "Batang" if "Batang" in have else "Malgun Gothic")
    plt.rcParams.update({
        "font.family": serif,
        "axes.unicode_minus": False,      # 한글 폰트엔 U+2212가 없어 네모로 나온다
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": INK,
        "axes.linewidth": 0.9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.labelsize": 12,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
        "xtick.color": INK, "ytick.color": INK,
        "text.color": INK, "axes.labelcolor": INK,
        "legend.frameon": False,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
    })


def _panel(ax, tag: str, title: str) -> None:
    ax.set_title(f"({tag}) {title}", fontsize=13, pad=14)


def _save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"{name}.{ext}")
    plt.close(fig)
    print(f"  {name}.png / .pdf")


# ------------------------------------------------------------------ 자료

def _stats() -> dict:
    """라이브 API의 근거 통계. 없으면 저장해 둔 사본."""
    import requests
    try:
        r = requests.get("https://weatherfit-seoul-api.onrender.com/api/stats",
                         timeout=120)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise SystemExit(f"근거 통계를 못 받았습니다: {e}")


def _footfall() -> list[tuple[str, float, float]]:
    """행정동별 (이름, 연 방문량, 전년동월비)."""
    from .momentum import excess, table
    return [(r["label"], r["axes"]["level"], excess(r["axes"]))
            for r in table("footfall")["series"].values() if r.get("axes")]


def _pairs() -> list[tuple[str, float, float]]:
    """장소별 (이름, 관심 전년비, 방문 전년비)."""
    from .momentum import crosswalk, excess, table
    cw = crosswalk("wikipedia", "footfall")
    ps, ds = table("wikipedia")["series"], table("footfall")["series"]
    out = []
    for cid, dong in cw["map"].items():
        a, b = ps.get(cid), ds.get(dong)
        if a and a.get("score") and b and b.get("score"):
            out.append((a["label"], excess(a["axes"]), excess(b["axes"])))
    return out


def _corr(xs, ys) -> float:
    mx, my = st.mean(xs), st.mean(ys)
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / len(xs)
    return cov / (st.pstdev(xs) * st.pstdev(ys))


# ---------------------------------------------------- 그림 1 · 문제의 크기

def figure1(s: dict) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.6))

    # (a) 자치구 쏠림
    ax = axes[0]
    dist = sorted(s["distribution"].items(), key=lambda t: -t[1])
    names = [k.replace("구", "") for k, _ in dist]
    vals = [v for _, v in dist]
    tot = sum(vals)
    cum = []
    acc = 0.0
    for v in vals:
        acc += v
        cum.append(acc / tot * 100)
    cols = [RUST if i < 3 else LIGHT for i in range(len(vals))]
    ax.bar(range(len(vals)), vals, color=cols, width=0.78, zorder=2)
    ax.set_ylabel("비짓서울 콘텐츠 (건)")
    ax.text(.97, .60, "좌표로 자치구가 확인된" + chr(10) + f"{tot:,}건 기준",
            transform=ax.transAxes, ha="right", va="top",
            fontsize=9.5, color=MID, linespacing=1.5)
    ax.set_xticks(range(len(vals)))
    ax.set_xticklabels(names, rotation=90, fontsize=9)
    ax.set_xlim(-0.8, len(vals) - 0.2)
    ax2 = ax.twinx()
    ax2.plot(range(len(vals)), cum, color=INK, lw=1.6, marker="o", ms=3,
             zorder=3)
    ax2.set_ylabel("누적 비율 (%)", color=INK)
    ax2.set_ylim(0, 105)
    ax2.spines["top"].set_visible(False)
    top3 = cum[2]
    ax2.annotate(f"상위 3개 구\n{top3:.1f}%", xy=(2, top3), xytext=(6.0, 46),
                 fontsize=11, color=RUST,
                 arrowprops=dict(arrowstyle="->", color=RUST, lw=1.2))
    _panel(ax, "a", "콘텐츠는 세 개 구에 몰려 있다")

    # (b) 판정 깔때기
    ax = axes[1]
    f = s["funnel"]
    d = f["dropped"]
    steps = [("전수", s["total"], PALE),
             ("기간 종료 제외", s["total"] - d["탈락·기간"], LIGHT),
             ("운영시간 제외", s["total"] - d["탈락·기간"] - d["탈락·운영"], MID),
             ("판정 가능", f["passed"], INK)]
    ys = list(range(len(steps)))[::-1]
    ax.barh(ys, [v for _, v, _ in steps],
            color=[c for _, _, c in steps], height=0.62, zorder=2)
    for y, (lb, v, _) in zip(ys, steps):
        ax.text(v + 60, y, f"{v:,}", va="center", fontsize=11, color=INK)
    ax.set_yticks(ys)
    ax.set_yticklabels([lb for lb, _, _ in steps], fontsize=11)
    ax.set_xlabel("콘텐츠 (건)")
    ax.set_xlim(0, s["total"] * 1.16)
    ax.annotate(f"{f['passed'] / s['total'] * 100:.1f}%만 남는다",
                xy=(f["passed"], 0), xytext=(1750, 0.62),
                fontsize=11, color=RUST,
                arrowprops=dict(arrowstyle="->", color=RUST, lw=1.2))
    _panel(ax, "b", "'지금 갈 수 있는' 것만 남기면")

    # (c) 운영정보 결손
    ax = axes[2]
    hc, ev, dt = s["hours_confidence"], s["environment"], s["dated"]
    t = s["total"]
    rows = [
        ("운영시간", [("규칙 확정", hc["high"] / t * 100, INK),
                   ("자유 문장", hc["low"] / t * 100, MID),
                   ("미표기", hc["none"] / t * 100, PALE)]),
        ("실내·실외", [("판별됨", (ev["indoor"] + ev["outdoor"]) / t * 100, INK),
                    ("불명", ev["unknown"] / t * 100, RUST)]),
        ("행사 기간", [("진행 중", (dt["total"] - dt["ended"]) / dt["total"] * 100, INK),
                    ("이미 종료", dt["ended"] / dt["total"] * 100, RUST)]),
    ]
    for i, (lb, parts) in enumerate(rows):
        left = 0.0
        for nm, pct, col in parts:
            ax.barh(i, pct, left=left, color=col, height=0.55, zorder=2)
            if pct >= 11:
                ax.text(left + pct / 2, i, f"{nm}\n{pct:.1f}%", ha="center",
                        va="center", fontsize=9.5,
                        color="white" if col in (INK, RUST, MID) else INK)
            elif col == RUST:
                # 실내외 불명 6.9%는 좁아서 막대 안에 안 들어간다.
                # 빼면 핵심 수치가 그림에서 사라지므로 위에 적고 선을 긋는다.
                cx = left + pct / 2
                ax.plot([cx, cx], [i - .30, i - .46], color=RUST, lw=.9,
                        clip_on=False, zorder=3)
                ax.text(cx, i - .50, nm + f" {pct:.1f}%", ha="center",
                        va="bottom", fontsize=9.5, color=RUST,
                        clip_on=False, zorder=3)
            left += pct
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([lb for lb, _ in rows], fontsize=11)
    ax.set_xlabel("비율 (%)")
    ax.set_xlim(0, 100)
    ax.invert_yaxis()
    _panel(ax, "c", "원문이 기계가 읽을 수 없는 상태다")

    fig.tight_layout(w_pad=2.6)
    _save(fig, "fig01_배경_문제의크기")


# ------------------------------------------------- 그림 2 · 왜 변화를 재는가

def figure2() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.2))

    # (a) 순위 ↔ 움직임
    #
    # 처음엔 '큰 곳일수록 변동이 작다'를 그리려 했는데 자료가 그렇게
    # 말하지 않았다. 십분위 중앙 변동폭이 단조롭지 않고(9구간 중 4구간만
    # 감소) 상관도 +0.04로 사실상 0이다. 최하위 1분위만 튄다.
    #
    # 대신 실제로 성립하는 것이 있고, 논지에는 그쪽이 더 맞다 — 방문량
    # 상위 20곳의 모멘텀이 -17.6%에서 +64.6%까지 흩어진다. 순위를 알아도
    # 움직임은 알 수 없다는 뜻이고, 그것이 '순위표에는 정보가 없다'다.
    ax = axes[0]
    ff = _footfall()
    lx = [math.log10(max(v, 1.0)) for _, v, _ in ff]
    r = _corr(lx, [m for _, _, m in ff])

    top = sorted(ff, key=lambda t: -t[1])[:16]
    ys = list(range(len(top)))[::-1]
    ms = [m * 100 for _, _, m in top]
    ax.axvline(0, color=INK, lw=0.9, zorder=1)
    for y, (nm, lv, mo) in zip(ys, top):
        v = mo * 100
        hot = abs(v) >= 15
        col = RUST if hot else MID
        ax.plot([0, v], [y, y], color=col, lw=2.2, alpha=.85, zorder=2)
        ax.scatter([v], [y], s=46, color=col, zorder=3,
                   edgecolors="white", linewidths=1.0)
        ax.text(v + (2.4 if v >= 0 else -2.4), y, f"{v:+.1f}%",
                va="center", ha="left" if v >= 0 else "right",
                fontsize=9.5, color=col)
    ax.set_yticks(ys)
    ax.set_yticklabels([f"{i + 1}. {nm.split()[-1]}"
                        for i, (nm, _, _) in enumerate(top)], fontsize=10)
    ax.set_xlabel("전년 동월비 (%)")
    ax.set_xlim(min(ms) - 16, max(ms) + 16)
    ax.text(.98, .04,
            f"방문량 순위 ↔ 모멘텀  r = {r:+.2f}" + chr(10) +
            f"상위 16곳 범위  {min(ms):+.1f}% ~ {max(ms):+.1f}%",
            transform=ax.transAxes, fontsize=10.5, color=INK,
            ha="right", va="bottom",
            bbox=dict(boxstyle="round,pad=0.5", fc="white", ec=GRID, lw=1))
    _panel(ax, "a", "방문량 상위 16개 행정동의 움직임")

    # (b) 관심 ↔ 방문
    ax = axes[1]
    pr = _pairs()
    wx = [w * 100 for _, w, _ in pr]
    vy = [v * 100 for _, _, v in pr]
    r2 = _corr(wx, vy)
    ax.axhline(0, color=GRID, lw=1.1, zorder=1)
    ax.axvline(0, color=GRID, lw=1.1, zorder=1)
    ax.scatter(wx, vy, s=24, color=MID, alpha=.62, edgecolors="none", zorder=2)

    show = {"효창공원": (16, -4), "흥인지문(동대문)": (14, -6),
            "광화문광장": (-64, -26), "문화비축기지": (-92, 12)}
    for nm, w, v in pr:
        if nm in show:
            ax.scatter([w * 100], [v * 100], s=52, color=RUST, zorder=4,
                       edgecolors="white", linewidths=1.1)
            ax.annotate(nm.replace("(동대문)", ""), xy=(w * 100, v * 100),
                        xytext=show[nm], textcoords="offset points",
                        fontsize=10.5, color=RUST, zorder=5,
                        arrowprops=dict(arrowstyle="-", color=RUST, lw=0.9))
    ax.set_xlabel("관심 · 위키백과 조회수 전년비 (%)")
    ax.set_ylabel("방문 · 외국인 유동인구 전년비 (%)", labelpad=10)
    # 청와대가 +416%라 나머지가 왼쪽에 뭉친다. 눈금을 잘라 본체를 읽히게
    # 하고, 잘려 나간 점은 화살표로 밖에 적는다 — 지우지는 않는다.
    hi = 215.0
    out = [(nm, w * 100, v * 100) for nm, w, v in pr if w * 100 > hi]
    ax.set_xlim(min(wx) - 22, hi)
    for nm, w, v in out:
        ax.annotate(f"{nm} {w:+.0f}%", xy=(hi, v), xytext=(-14, 26),
                    textcoords="offset points", ha="right", fontsize=10,
                    color=RUST,
                    arrowprops=dict(arrowstyle="->", color=RUST, lw=1.1))
    ax.text(.97, .04, f"상관계수  r = {r2:+.3f}   (n = {len(pr)})",
            transform=ax.transAxes, fontsize=11.5, color=RUST, ha="right",
            va="bottom",
            bbox=dict(boxstyle="round,pad=0.5", fc="white", ec=RUST, lw=1.1))
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    _panel(ax, "b", "관심과 방문은 다른 것을 말한다")

    fig.tight_layout(w_pad=3.0)
    _save(fig, "fig02_배경_순위가아니라변화")
    return r, r2, min(ms), max(ms), len(ff), len(pr)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    _setup()
    print("근거 통계를 받는 중…")
    s = _stats()
    print(f"저장 위치 {OUT}")
    figure1(s)
    got = figure2()
    print(f"\n검증용 수치")
    print(f"  수준↔모멘텀 r = {got[0]:+.3f}  ·  행정동 {got[4]}개")
    print(f"  상위 16곳 모멘텀 범위 {got[2]:+.1f}% ~ {got[3]:+.1f}%")
    print(f"  관심↔방문 r = {got[1]:+.3f}  ·  n = {got[5]}")


if __name__ == "__main__":
    main()
