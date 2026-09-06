#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/plot_merit_order_suite.py — Merit Order / Regime Map / Pareto Front 可視化層（Phase 3）
================================================================================
`wom/visualization/` の3モジュール（headless な Dict ベースのロジック層）に対する
matplotlib 静止画描画層。

設計正典: requests/Phase3_RequestLetter_to_CodeKun.md / requests/Phase3_DesignMD_Visualization.md
参照実装: tools/plot_allocation_map.py（matplotlib の書き方・慣行）

制約（Request Letter §C1-C7）:
    - matplotlib のみ（plotly/bokeh/dash/streamlit/seaborn 禁止、スタンドアロンWindows PC運用のため）
    - 新規依存パッケージなし
    - matplotlib.use("Agg") は pyplot import の前
    - 図中のテキストは全て英語（日本語フォント未導入環境での豆腐化を防ぐ）
    - 各描画関数は生成した出力パスを返す（plt.close(fig) を必ず呼ぶ）
    - 禁足コア（backward_planner.py 等）には一切触れない
    - 乱数は使わない（--demo も含め全て決定的）

使い方（リポジトリ直下）:
    python -m tools.plot_merit_order_suite --suppliers data/sample/<case>/supplier_master.csv \\
        --required-qty 5000 --out output/visualization/
    python -m tools.plot_merit_order_suite --demo
"""
from __future__ import annotations

import argparse
import os
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")          # 必ず pyplot より前
import matplotlib.pyplot as plt
import numpy as np

from wom.visualization.merit_order import MeritOrderAnalyzer, load_suppliers_from_csv
from wom.visualization.regime_map import RegimeMapAnalyzer, REGIME_STRATEGIES
from wom.visualization.pareto_front import ParetoFrontAnalyzer


# ---------------------------------------------------------------------------
# 内蔵デモデータ（--demo。すべて決定的、乱数不使用）
# ---------------------------------------------------------------------------

DEMO_SUPPLIERS: List[Dict] = [
    {"supplier_id": "SUP_001", "supplier_name": "Alpha Semi", "unit_cost": 50,
     "max_supply": 3000, "lead_time_days": 14, "quality_score": 95,
     "currency": "USD", "exchange_rate": 1.0},
    {"supplier_id": "SUP_002", "supplier_name": "Beta Foundry", "unit_cost": 48,
     "max_supply": 3000, "lead_time_days": 21, "quality_score": 94,
     "currency": "USD", "exchange_rate": 1.0},
    {"supplier_id": "SUP_003", "supplier_name": "Gamma Fab", "unit_cost": 55,
     "max_supply": 2000, "lead_time_days": 7, "quality_score": 96,
     "currency": "USD", "exchange_rate": 1.0},
    {"supplier_id": "SUP_004", "supplier_name": "Delta Rush", "unit_cost": 70,
     "max_supply": 5000, "lead_time_days": 5, "quality_score": 80,
     "currency": "USD", "exchange_rate": 1.0},
    {"supplier_id": "SUP_005", "supplier_name": "Epsilon Emergency", "unit_cost": 90,
     "max_supply": 5000, "lead_time_days": 70, "quality_score": 75,
     "currency": "USD", "exchange_rate": 1.0},
]

# 12週分の需要倍率パターン（決定的・乱数不使用）。base_qty に掛けて使う。
# DEMO_SUPPLIERS の総供給能力は18,000units。振れ幅を意図的に大きく取り、
# Regime Map の Low/Medium/High × Tight/Balanced のバリエーション
# （Surplus は本データセットの構造上到達不能——最安サプライヤーのlead_timeが
# 下限に近いため）が実際に出現するようにしている。
_HORIZON_DEMAND_PATTERN = [0.70, 1.00, 1.80, 1.20, 3.20, 0.90,
                           2.00, 0.60, 1.60, 3.90, 1.30, 5.00]


def _horizon_required_qty(week_idx: int, base_qty: float) -> float:
    """決定的な週次需要変動を返す（乱数不使用、Request Letter §C7）。"""
    factor = _HORIZON_DEMAND_PATTERN[week_idx % len(_HORIZON_DEMAND_PATTERN)]
    return round(base_qty * factor, 0)


def _apply_shock(suppliers: List[Dict], factor: float = 1.30) -> List[Dict]:
    """最安サプライヤーのコストを factor 倍にする決定的な擾乱
    （為替・関税ショックの簡易シミュレーション。merit_order_shift のBefore/After生成に使う）。
    """
    if not suppliers:
        return list(suppliers)

    tmp = MeritOrderAnalyzer(suppliers)
    priced = tmp._calculate_cost_in_usd(suppliers)
    cheapest = min(priced, key=lambda s: s["unit_cost_usd"])
    cheapest_id = cheapest["supplier_id"]

    shocked = []
    for s in suppliers:
        s2 = dict(s)
        if s2.get("supplier_id") == cheapest_id:
            s2["unit_cost"] = s2.get("unit_cost", 0) * factor
        shocked.append(s2)
    return shocked


def _plan_scenarios(suppliers: List[Dict]) -> List[Dict]:
    """データから自動導出した、汎用的な demand フィルタシナリオ（決定的）。

    quality/lead_time の中央値を閾値に使うことで、特定データセットに
    ハードコードせず、任意のサプライヤー CSV に対しても機能するようにする
    （--suppliers 経路用のフォールバック。データセット次第では複数シナリオが
    同一の配分案に収束することがあるが、それ自体はバグではない——
    §V0.1 のとおり真のバグは「record 粒度のまま複数週を集約すること」であり、
    本関数は既に plan 粒度で呼び出される前提のため対象外）。
    """
    qualities = sorted(s.get("quality_score", 0) for s in suppliers)
    lead_times = sorted(s.get("lead_time_days", 0) for s in suppliers)
    med_quality = qualities[len(qualities) // 2] if qualities else 0
    med_lead_time = lead_times[len(lead_times) // 2] if lead_times else 0
    return [
        {},
        {"min_quality_acceptable": med_quality},
        {"max_lead_time_acceptable": med_lead_time},
    ]


# --demo 専用の決定的シナリオ（DEMO_SUPPLIERS で3案とも相互に非支配になるよう
# 手動で選定済み。_plan_scenarios() の中央値ベースの汎用ロジックだと、
# このデータセットでは偶然ベースラインと同一の配分に収束してしまうため）。
DEMO_PLAN_SCENARIOS: List[Dict] = [
    {},
    {"min_quality_acceptable": 95},
    {"max_lead_time_acceptable": 5},
]


# ---------------------------------------------------------------------------
# V1: Merit Order 曲線
# ---------------------------------------------------------------------------

_DARK = "#2E6DB5"
_LIGHT = "#BFD3EA"


def _draw_block(ax, x_left: float, width: float, height: float, color: str) -> None:
    if width <= 0:
        return
    ax.bar(x_left, height, width=width, align="edge", color=color,
           edgecolor="white", linewidth=0.8, zorder=2)


def plot_merit_order_curve(
    result: Dict,
    out: str,
    *,
    title: Optional[str] = None,
    annotate_lambda: bool = True,
) -> str:
    """Merit Order 曲線を描画し、保存先パスを返す。

    Args:
        result: calculate_merit_order() の戻り値
        out:    出力 PNG パス
        title:  図タイトル（None なら week と required_qty から自動生成）
        annotate_lambda: 需要線との交点にシャドープライス λ を注記するか
    """
    merit_order = result.get("merit_order", [])
    req = result.get("required_qty", 0) or 0

    fig, ax = plt.subplots(figsize=(9, 5.5))

    x_left = 0.0
    lam = None
    lam_name = None
    for s in merit_order:
        w = s.get("max_supply", 0)
        h = s.get("unit_cost_usd", 0)
        x_right = x_left + w
        cum = s.get("cumulative_supply_from_rank_1", x_right)

        if lam is None and req > 0 and cum >= req:
            lam = h
            lam_name = s.get("supplier_name") or s.get("supplier_id")

        if x_right <= req:
            _draw_block(ax, x_left, w, h, _DARK)
        elif x_left >= req:
            _draw_block(ax, x_left, w, h, _LIGHT)
        else:
            # 需要線をまたぐブロック: 2分割
            _draw_block(ax, x_left, req - x_left, h, _DARK)
            _draw_block(ax, req, x_right - req, h, _LIGHT)

        label = s.get("supplier_name") or s.get("supplier_id") or ""
        short_label = s.get("supplier_id") or ""
        total_span = max(x_right, req, 1.0)
        if w / total_span > 0.06 and label:
            ax.text(x_left + w / 2, h / 2, label, ha="center", va="center",
                    fontsize=8, color="white", zorder=3)
        elif w / total_span > 0.03 and short_label:
            ax.text(x_left + w / 2, h / 2, short_label, ha="center", va="center",
                    fontsize=7, color="white", zorder=3, rotation=90)

        x_left = x_right

    total_supply = x_left

    if req > 0:
        ax.axvline(req, color="black", linestyle="--", linewidth=1.4, zorder=4)

    if lam is not None and annotate_lambda:
        ax.axhline(lam, color="crimson", linestyle=":", linewidth=1.3, zorder=4)
        ax.text(
            0.02, 0.96,
            f"lambda = {lam:.2f} USD/unit (marginal supplier: {lam_name})",
            transform=ax.transAxes, ha="left", va="top", fontsize=9,
            color="crimson",
        )
    elif req > total_supply:
        unmet = req - total_supply
        rate = result.get("fulfillment_rate", 0.0) or 0.0
        ax.text(
            0.02, 0.96,
            f"Unmet demand: {unmet:.0f} units (fulfillment {rate:.1%})",
            transform=ax.transAxes, ha="left", va="top", fontsize=9,
            color="darkred",
        )

    ax.set_xlabel("Cumulative supply (units)")
    ax.set_ylabel("Unit cost (USD/unit)")
    week = result.get("week") or ""
    ax.set_title(title or f"Merit Order Curve — {week}  (required = {req:.0f} units)")
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def plot_merit_order_shift(
    before: Dict,
    after: Dict,
    out: str,
    *,
    labels: Tuple[str, str] = ("Before", "After"),
    title: Optional[str] = None,
) -> str:
    """為替・関税の変化でサプライヤー順位が入れ替わる様子を、2本の階段で重ねる。"""
    fig, ax = plt.subplots(figsize=(9, 5.5))

    def _step_arrays(result: Dict) -> Tuple[np.ndarray, np.ndarray]:
        xs = [0.0]
        ys = []
        x = 0.0
        for s in result.get("merit_order", []):
            ys.append(s.get("unit_cost_usd", 0))
            x += s.get("max_supply", 0)
            xs.append(x)
        if ys:
            ys.append(ys[-1])
        return np.array(xs), np.array(ys)

    x_before, y_before = _step_arrays(before)
    x_after, y_after = _step_arrays(after)

    ax.step(x_before, y_before, where="post", color="#888888", linestyle="--",
            linewidth=1.4, label=labels[0])
    ax.step(x_after, y_after, where="post", color="#C1432B", linestyle="-",
            linewidth=1.8, label=labels[1])

    def _lambda_of(result: Dict) -> Optional[float]:
        req = result.get("required_qty", 0) or 0
        for s in result.get("merit_order", []):
            if req > 0 and s.get("cumulative_supply_from_rank_1", 0) >= req:
                return s.get("unit_cost_usd")
        return None

    lam_before = _lambda_of(before)
    lam_after = _lambda_of(after)
    legend_extra = []
    if lam_before is not None:
        ax.axhline(lam_before, color="#888888", linestyle=":", linewidth=1.0)
        legend_extra.append(f"lambda_before = {lam_before:.2f}")
    if lam_after is not None:
        ax.axhline(lam_after, color="#C1432B", linestyle=":", linewidth=1.0)
        legend_extra.append(f"lambda_after = {lam_after:.2f}")

    # F1: 需要線（required_qty）を引く。λは「需要線と階段の交点の高さ」であることを
    # 図から読み取れるようにするため（設計書§3.2の記述漏れ、rev.4で追記）。
    req_before = before.get("required_qty", 0) or 0
    req_after = after.get("required_qty", 0) or 0

    if req_before and req_after and abs(req_before - req_after) < 1e-9:
        # 通常ケース：同じ需要量で before/after を比較している
        ax.axvline(req_before, color="black", linestyle="--", linewidth=1.4,
                   label=f"required = {req_before:,.0f} units")
    else:
        # 需要量が異なる場合はそれぞれの色で引く
        if req_before:
            ax.axvline(req_before, color="#888888", linestyle="--", linewidth=1.2,
                       label=f"required ({labels[0]}) = {req_before:,.0f}")
        if req_after:
            ax.axvline(req_after, color="#C1432B", linestyle="--", linewidth=1.2,
                       label=f"required ({labels[1]}) = {req_after:,.0f}")

    # F1-2: 交点 (required_qty, lambda) に丸マーカー（凡例には載せない）
    if lam_before is not None and req_before:
        ax.plot([req_before], [lam_before], marker="o", markersize=6,
                color="#888888", zorder=5)
    if lam_after is not None and req_after:
        ax.plot([req_after], [lam_after], marker="o", markersize=6,
                color="#C1432B", zorder=5)

    rank_before = {s["supplier_id"]: s["rank"] for s in before.get("merit_order", [])}
    rank_after = {s["supplier_id"]: s["rank"] for s in after.get("merit_order", [])}

    swapped = [sid for sid in rank_after
               if sid in rank_before and rank_before[sid] != rank_after[sid]]
    new_suppliers = [sid for sid in rank_after if sid not in rank_before]
    dropped_suppliers = [sid for sid in rank_before if sid not in rank_after]

    notes = []
    if swapped:
        parts = [f"{sid} (#{rank_before[sid]}->#{rank_after[sid]})" for sid in swapped]
        notes.append("Rank changes: " + ", ".join(parts))
    if new_suppliers:
        notes.append("New: " + ", ".join(f"{sid} (new)" for sid in new_suppliers))
    if dropped_suppliers:
        notes.append("Dropped: " + ", ".join(f"{sid} (dropped)" for sid in dropped_suppliers))

    if notes:
        ax.text(0.02, -0.16, "\n".join(notes), transform=ax.transAxes,
                ha="left", va="top", fontsize=8)

    handles, labels_ = ax.get_legend_handles_labels()
    if legend_extra:
        for txt in legend_extra:
            handles.append(plt.Line2D([], [], color="none"))
            labels_.append(txt)
    # F2-1: 階段曲線は左端が最安（yが最も低い）ため左上は構造的に必ず空く
    ax.legend(handles, labels_, fontsize=8, loc="upper left", framealpha=0.9)

    ax.set_xlabel("Cumulative supply (units)")
    ax.set_ylabel("Unit cost (USD/unit)")
    ax.set_title(title or "Merit Order Shift — Before vs After")
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# V2: Regime Map
# ---------------------------------------------------------------------------

_SUPPLY_ORDER = ["Tight", "Balanced", "Surplus"]     # 上から
_DEMAND_ORDER = ["Low", "Medium", "High"]            # 左から
_ROW_TAG = {"Tight": "Risk Mode", "Balanced": "Balanced", "Surplus": "Opportunity"}


def _weeks_label(n: int) -> str:
    """週数を単複を考慮した英語表記にする（例: 0 weeks / 1 week / 9 weeks）"""
    return f"{n} week" if n == 1 else f"{n} weeks"


def plot_regime_matrix(horizon: Dict, out: str, *, title: Optional[str] = None) -> str:
    """3×3 の Regime Map ヒートマップを描画する。"""
    week_by_week = horizon.get("week_by_week", [])

    counts = np.zeros((3, 3), dtype=int)
    for w in week_by_week:
        r = _SUPPLY_ORDER.index(w["supply_tightness"])
        c = _DEMAND_ORDER.index(w["demand_level"])
        counts[r, c] += 1

    dominant = horizon.get("summary", {}).get("dominant_regime")

    fig, ax = plt.subplots(figsize=(7.5, 6))
    im = ax.imshow(counts, cmap="YlOrRd", vmin=0)

    for r in range(3):
        for c in range(3):
            supply = _SUPPLY_ORDER[r]
            demand = _DEMAND_ORDER[c]
            strategy = REGIME_STRATEGIES.get((supply, demand), "?")
            n = counts[r, c]
            label = f"{strategy}\n({_weeks_label(n)})"
            cell_label = f"{supply}/{demand}"
            color = "white" if n > counts.max() * 0.55 and counts.max() > 0 else "black"
            ax.text(c, r, label, ha="center", va="center", fontsize=8.5, color=color)

            if cell_label == dominant:
                rect = plt.Rectangle((c - 0.5, r - 0.5), 1, 1, fill=False,
                                      edgecolor="blue", linewidth=3.0, zorder=5)
                ax.add_patch(rect)

    ax.set_xticks(range(3))
    ax.set_xticklabels(_DEMAND_ORDER)
    ax.set_xlabel("Demand Level")
    ax.set_yticks(range(3))
    ax.set_yticklabels(_SUPPLY_ORDER)
    ax.set_ylabel("Supply Tightness")

    for r, supply in enumerate(_SUPPLY_ORDER):
        ax.text(2.65, r, _ROW_TAG[supply], ha="left", va="center",
                fontsize=8, style="italic", color="dimgray")
    ax.set_xlim(-0.5, 3.3)

    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.10, label="Weeks in this regime")
    ax.set_title(title or "Regime Map (3x3 matrix)")
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def plot_regime_timeline(horizon: Dict, out: str, *, title: Optional[str] = None) -> str:
    """Regime score の時系列 + 推奨戦略の帯を描画する。"""
    week_by_week = horizon.get("week_by_week", [])
    n = len(week_by_week)
    x = list(range(n))
    week_labels = [w.get("week") or f"W{i + 1}" for i, w in enumerate(week_by_week)]

    demand_pressure = [w["regime_score"]["demand_pressure"] for w in week_by_week]
    supply_risk = [w["regime_score"]["supply_risk"] for w in week_by_week]
    strategies = [w["recommended_strategy"] for w in week_by_week]

    fig, (ax_top, ax_bottom) = plt.subplots(
        2, 1, figsize=(max(9, n * 0.7), 6.5),
        gridspec_kw={"height_ratios": [3, 1]}, sharex=True,
    )

    # 注意: risk_weeks / opportunity_weeks は「1始まりの位置」。X座標は -1 する。
    for pos in horizon.get("summary", {}).get("risk_weeks", []):
        ax_top.axvspan(pos - 1 - 0.5, pos - 1 + 0.5, color="red", alpha=0.15)
    for pos in horizon.get("summary", {}).get("opportunity_weeks", []):
        ax_top.axvspan(pos - 1 - 0.5, pos - 1 + 0.5, color="green", alpha=0.15)

    ax_top.plot(x, demand_pressure, marker="o", color="#C1432B", label="demand_pressure")
    ax_top.plot(x, supply_risk, marker="s", color="#2E6DB5", label="supply_risk")
    ax_top.set_ylim(0, 10)
    ax_top.set_ylabel("Regime score (0-10)")
    # F2-2: Y軸[0,10]固定は維持しつつ（週間比較のため軸内に余白を作らない）、
    # supply_risk のピーク（9-10）と重ならないよう凡例を軸の外側・上・横並びに出す。
    # タイトルは凡例と同じ領域を取り合うため fig.suptitle() 側へ移す。
    ax_top.legend(fontsize=8, loc="lower left", bbox_to_anchor=(0.0, 1.01),
                  ncol=2, frameon=False)
    fig.suptitle(title or "Regime Timeline", y=0.995)

    unique_strategies = sorted(set(strategies))
    cmap = plt.get_cmap("tab10")
    strategy_color = {s: cmap(i % 10) for i, s in enumerate(unique_strategies)}

    for i, s in enumerate(strategies):
        ax_bottom.axvspan(i - 0.5, i + 0.5, color=strategy_color[s], alpha=0.85)

    ax_bottom.set_yticks([])
    ax_bottom.set_xticks(x)
    ax_bottom.set_xticklabels(week_labels, rotation=45, ha="right", fontsize=7)
    ax_bottom.set_xlabel("Week")
    ax_bottom.set_xlim(-0.5, max(n - 0.5, 0.5))

    handles = [plt.Rectangle((0, 0), 1, 1, color=strategy_color[s]) for s in unique_strategies]
    ax_bottom.legend(handles, unique_strategies, fontsize=6.5, loc="upper center",
                      bbox_to_anchor=(0.5, -0.55), ncol=min(len(unique_strategies), 4))

    # suptitle + 軸外凡例のぶんの余白を上部に確保する
    fig.tight_layout(rect=(0, 0, 1, 0.99))
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# V3: Pareto Front + 平行座標
# ---------------------------------------------------------------------------

def plot_pareto_scatter(
    front: List[Dict],
    all_solutions: List[Dict],
    out: str,
    *,
    tradeoffs: Optional[Dict] = None,
    title: Optional[str] = None,
) -> str:
    """目的空間の散布図（2パネル）を描画し、保存先パスを返す。"""
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(13, 5.5))

    dominated = [s for s in all_solutions if not s.get("on_front")]
    front_sorted = sorted(front, key=lambda f: f["rank"]) if front else []

    # --- 左パネル: Cost vs Quality（色=Lead Time） ---
    if dominated:
        axL.scatter([s["cost"] for s in dominated], [s["quality"] for s in dominated],
                    color="lightgray", s=22, zorder=1, label="dominated")
    if front_sorted:
        f_cost = [f["cost"] for f in front_sorted]
        f_qual = [f["quality"] for f in front_sorted]
        f_lt = [f["lead_time"] for f in front_sorted]
        scL = axL.scatter(f_cost, f_qual, c=f_lt, cmap="viridis", s=170, marker="*",
                           edgecolor="black", linewidth=0.8, zorder=3, label="Pareto front")
        if len(front_sorted) > 1:
            axL.plot(f_cost, f_qual, color="black", linewidth=1.0, alpha=0.6, zorder=2)
        for f in front_sorted:
            axL.annotate(f"#{f['rank']}", (f["cost"], f["quality"]),
                         textcoords="offset points", xytext=(6, 6), fontsize=8)
        fig.colorbar(scL, ax=axL, fraction=0.046, pad=0.04, label="Lead time (days)")

        if tradeoffs:
            for t in tradeoffs.get("cost_vs_quality", []):
                a = next((f for f in front_sorted if f["rank"] == t["from_rank"]), None)
                b = next((f for f in front_sorted if f["rank"] == t["to_rank"]), None)
                if a and b:
                    mx, my = (a["cost"] + b["cost"]) / 2, (a["quality"] + b["quality"]) / 2
                    axL.annotate(
                        f"+{t['cost_increase_pct']:.1f}% cost / "
                        f"{t['quality_gain_points']:+.1f} quality",
                        (mx, my), fontsize=7, color="dimgray",
                    )
    else:
        axL.text(0.5, 0.5, "No solutions", transform=axL.transAxes, ha="center", va="center")

    axL.set_xlabel("Total cost (USD)")
    axL.set_ylabel("Quality score")
    axL.set_title("Cost vs Quality")
    if dominated or front_sorted:
        axL.legend(fontsize=8, loc="best")

    # --- 右パネル: Cost vs Lead Time（色=Quality） ---
    if dominated:
        axR.scatter([s["cost"] for s in dominated], [s["lead_time"] for s in dominated],
                    color="lightgray", s=22, zorder=1, label="dominated")
    if front_sorted:
        f_cost = [f["cost"] for f in front_sorted]
        f_lt = [f["lead_time"] for f in front_sorted]
        f_qual = [f["quality"] for f in front_sorted]
        scR = axR.scatter(f_cost, f_lt, c=f_qual, cmap="viridis", s=170, marker="*",
                           edgecolor="black", linewidth=0.8, zorder=3, label="Pareto front")
        if len(front_sorted) > 1:
            axR.plot(f_cost, f_lt, color="black", linewidth=1.0, alpha=0.6, zorder=2)
        for f in front_sorted:
            axR.annotate(f"#{f['rank']}", (f["cost"], f["lead_time"]),
                         textcoords="offset points", xytext=(6, 6), fontsize=8)
        fig.colorbar(scR, ax=axR, fraction=0.046, pad=0.04, label="Quality score")

        if tradeoffs:
            for t in tradeoffs.get("cost_vs_lead_time", []):
                a = next((f for f in front_sorted if f["rank"] == t["from_rank"]), None)
                b = next((f for f in front_sorted if f["rank"] == t["to_rank"]), None)
                if a and b:
                    mx, my = (a["cost"] + b["cost"]) / 2, (a["lead_time"] + b["lead_time"]) / 2
                    axR.annotate(
                        f"+{t['cost_increase_pct']:.1f}% cost / "
                        f"{t['lead_time_change_days']:+.1f}d lead time",
                        (mx, my), fontsize=7, color="dimgray",
                    )
    else:
        axR.text(0.5, 0.5, "No solutions", transform=axR.transAxes, ha="center", va="center")

    axR.set_xlabel("Total cost (USD)")
    axR.set_ylabel("Lead time (days)")
    axR.set_title("Cost vs Lead Time")
    if dominated or front_sorted:
        axR.legend(fontsize=8, loc="best")

    fig.suptitle(title or "Pareto Front — objective space")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def build_supplier_share_axes(
    solutions: List[Dict],
    top_k: int = 5,
) -> Tuple[List[str], List[Dict[str, float]]]:
    """全配分案の和集合から Merit Order 順に上位 K 社の軸を決め、
    各案の配分比率を返す。

    Args:
        solutions: compute_all_solutions() の戻り値（**plan 粒度であること**）
                   各要素の ["allocation"] が merit_order result で、
                   その ["recommended_allocation"] にサプライヤー配分が入る
        top_k: 軸として採用するサプライヤー数（デフォルト 5）

    Returns:
        (axis_labels, shares)
        axis_labels: ["SUP_002", "SUP_001", ..., "Others"]
                     Merit Order 順（unit_cost_usd 昇順）。
                     top_k に収まらないサプライヤーが1社以上いる場合のみ
                     末尾に "Others" が付く
        shares: [{axis_label: 比率}, ...]（solutions と同順。各案の合計 1.0）

    Raises:
        ValueError: solutions が plan 粒度でない（recommended_allocation を持たない）とき
    """
    if not solutions:
        return [], []

    supplier_cost: Dict[str, float] = {}
    for sol in solutions:
        alloc = sol.get("allocation")
        if not isinstance(alloc, dict) or "recommended_allocation" not in alloc:
            raise ValueError(
                "solutions are not plan-granularity (missing 'recommended_allocation')"
            )
        for item in alloc["recommended_allocation"]:
            sid = item["supplier_id"]
            cost = item.get("unit_cost_usd", 0)
            if sid not in supplier_cost or cost < supplier_cost[sid]:
                supplier_cost[sid] = cost

    # Merit Order順（unit_cost_usd昇順）。タイブレークは supplier_id 辞書順。
    ordered = sorted(supplier_cost.keys(), key=lambda sid: (supplier_cost[sid], sid))

    top = ordered[:top_k]
    top_set = set(top)
    has_others = len(ordered) > top_k

    axis_labels = list(top)
    if has_others:
        axis_labels.append("Others")

    shares: List[Dict[str, float]] = []
    for sol in solutions:
        alloc = sol["allocation"]
        qty_by_supplier: Dict[str, float] = {}
        for item in alloc["recommended_allocation"]:
            sid = item["supplier_id"]
            qty_by_supplier[sid] = qty_by_supplier.get(sid, 0) + item.get("allocated_qty", 0)

        total_qty = sum(qty_by_supplier.values())
        share = {label: 0.0 for label in axis_labels}

        if total_qty > 0:
            for sid, qty in qty_by_supplier.items():
                pct = qty / total_qty
                if sid in top_set:
                    share[sid] = share.get(sid, 0.0) + pct
                elif has_others:
                    share["Others"] = share.get("Others", 0.0) + pct

        shares.append(share)

    return axis_labels, shares


def plot_parallel_coordinates(
    front: List[Dict],
    all_solutions: List[Dict],
    out: str,
    *,
    include_supplier_share: bool = True,
    top_k_suppliers: int = 5,
    title: Optional[str] = None,
) -> str:
    """目的軸3本（+ サプライヤー share 軸）の平行座標図を描画する。"""

    def _norm(values: List[float], invert: bool) -> List[float]:
        lo, hi = min(values), max(values)
        if hi - lo == 0:
            return [0.5] * len(values)
        if invert:
            return [1 - (v - lo) / (hi - lo) for v in values]
        return [(v - lo) / (hi - lo) for v in values]

    costs = [s["cost"] for s in all_solutions]
    quals = [s["quality"] for s in all_solutions]
    lts = [s["lead_time"] for s in all_solutions]

    axis_labels = ["Cost", "Quality", "Lead Time"]
    # 目的軸は「上ほど良い」に統一: cost/lead_timeは反転、qualityはそのまま
    axis_values: List[List[float]] = [
        _norm(costs, invert=True),
        _norm(quals, invert=False),
        _norm(lts, invert=True),
    ]
    # (top_val, bottom_val)。cost/lead_timeは小さい方が上(良い)、qualityは大きい方が上
    axis_range_labels: List[Tuple[float, float]] = [
        (min(costs), max(costs)),
        (max(quals), min(quals)),
        (min(lts), max(lts)),
    ]

    n_obj_axes = 3
    share_axis_labels: List[str] = []
    if include_supplier_share:
        share_axis_labels, shares = build_supplier_share_axes(all_solutions, top_k=top_k_suppliers)
        for label in share_axis_labels:
            axis_labels.append(label)
            axis_values.append([sh.get(label, 0.0) for sh in shares])
            axis_range_labels.append((1.0, 0.0))

    n_axes = len(axis_labels)
    x_positions = list(range(n_axes))

    fig, ax = plt.subplots(figsize=(max(8.5, n_axes * 1.7), 6.2))

    front_ranks = [s["rank"] for s in all_solutions if s.get("on_front")]
    max_rank = max(front_ranks) if front_ranks else 1
    cmap = plt.get_cmap("viridis")

    handles = []
    seen_labels = set()
    for i, sol in enumerate(all_solutions):
        ys = [axis_values[a][i] for a in range(n_axes)]
        if sol.get("on_front"):
            rank = sol["rank"]
            color = cmap((rank - 1) / max(1, (max_rank - 1))) if max_rank > 1 else cmap(0.0)
            line, = ax.plot(x_positions, ys, color=color, linewidth=2.3, alpha=0.95, zorder=3)
            lab = f"#{rank}"
            if lab not in seen_labels:
                handles.append(line)
                seen_labels.add(lab)
                line.set_label(lab)
        else:
            ax.plot(x_positions, ys, color="lightgray", linewidth=1.0, alpha=0.4, zorder=1)

    for x in x_positions:
        ax.axvline(x, color="#E0E0E0", linewidth=0.7, zorder=0)

    if include_supplier_share and share_axis_labels:
        sep_x = n_obj_axes - 0.5
        ax.axvline(sep_x, color="#555555", linewidth=2.2, zorder=2)

    # 既定の x tick ラベルは軸線のすぐ下（ポイント単位オフセット）に固定されて
    # 実値ラベル（データ座標）と重なるため無効化し、軸名は自前で十分下に描く。
    ax.set_xticks(x_positions)
    ax.set_xticklabels([])
    ax.tick_params(axis="x", length=0)
    ax.set_ylim(-0.30, 1.10)
    ax.set_yticks([])

    for a in range(n_axes):
        top_val, bottom_val = axis_range_labels[a]
        ax.text(x_positions[a], 1.03, f"{top_val:.2g}", ha="center", va="bottom", fontsize=7)
        ax.text(x_positions[a], -0.03, f"{bottom_val:.2g}", ha="center", va="top", fontsize=7)
        ax.text(x_positions[a], -0.16, axis_labels[a], ha="center", va="top",
                fontsize=9, fontweight="bold")

    if handles:
        # rank順に並べ替えて凡例表示
        handles_labels = sorted(zip(handles, [h.get_label() for h in handles]),
                                 key=lambda hl: int(hl[1].lstrip("#")))
        h2 = [h for h, _ in handles_labels]
        l2 = [l for _, l in handles_labels]
        ax.legend(h2, l2, fontsize=7, loc="upper left", bbox_to_anchor=(1.01, 1.0),
                  title="Pareto rank")

    ax.set_title(
        title or "Parallel Coordinates — Pareto front vs dominated solutions\n"
                  "(objective axes: higher = better; share axes: fixed 0-1 scale)",
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# V4: CLI
# ---------------------------------------------------------------------------

_OUTPUT_FILES = {
    "curve": "merit_order_curve.png",
    "shift": "merit_order_shift.png",
    "regime_matrix": "regime_matrix.png",
    "regime_timeline": "regime_timeline.png",
    "pareto_scatter": "pareto_scatter.png",
    "parallel_coordinates": "parallel_coordinates.png",
}


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Merit Order / Regime Map / Pareto Front visualization suite (Phase 3)."
    )
    ap.add_argument("--suppliers", default=None,
                    help="Supplier CSV path (see load_suppliers_from_csv()).")
    ap.add_argument("--required-qty", type=float, default=5000.0)
    ap.add_argument("--out", default="output/visualization")
    ap.add_argument("--demo", action="store_true",
                    help="Use built-in deterministic demo data instead of --suppliers.")
    ap.add_argument("--horizon-weeks", type=int, default=12)
    return ap


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    return _build_arg_parser().parse_args(argv)


def run(args: argparse.Namespace) -> List[str]:
    """引数に従って6枚のPNGを生成し、生成したパスのリストを返す。"""
    if not args.demo and not args.suppliers:
        raise ValueError("Either --suppliers or --demo must be given.")

    os.makedirs(args.out, exist_ok=True)

    suppliers = DEMO_SUPPLIERS if args.demo else load_suppliers_from_csv(args.suppliers)
    required_qty = args.required_qty

    analyzer = MeritOrderAnalyzer(suppliers)
    analyzer.validate_suppliers()

    made: List[str] = []

    # --- V1 ---
    base_result = analyzer.calculate_merit_order(
        {"week": "T0", "required_qty": required_qty}
    )
    made.append(plot_merit_order_curve(
        base_result, os.path.join(args.out, _OUTPUT_FILES["curve"])
    ))

    shocked_suppliers = _apply_shock(suppliers)
    after_analyzer = MeritOrderAnalyzer(shocked_suppliers)
    after_analyzer.validate_suppliers()
    after_result = after_analyzer.calculate_merit_order(
        {"week": "T1", "required_qty": required_qty}
    )
    made.append(plot_merit_order_shift(
        base_result, after_result, os.path.join(args.out, _OUTPUT_FILES["shift"])
    ))

    # --- V2 ---
    horizon_results = [
        analyzer.calculate_merit_order({
            "week": f"H{w + 1:02d}",
            "required_qty": _horizon_required_qty(w, required_qty),
        })
        for w in range(args.horizon_weeks)
    ]
    regime_analyzer = RegimeMapAnalyzer(horizon_results)
    horizon = regime_analyzer.classify_horizon(horizon_weeks=args.horizon_weeks)

    made.append(plot_regime_matrix(
        horizon, os.path.join(args.out, _OUTPUT_FILES["regime_matrix"])
    ))
    made.append(plot_regime_timeline(
        horizon, os.path.join(args.out, _OUTPUT_FILES["regime_timeline"])
    ))

    # --- V3 ---
    scenarios = DEMO_PLAN_SCENARIOS if args.demo else _plan_scenarios(suppliers)
    plan_results = [
        analyzer.calculate_merit_order({"required_qty": required_qty, **sc})
        for sc in scenarios
    ]
    pareto_analyzer = ParetoFrontAnalyzer.from_merit_order_results(plan_results)
    front = pareto_analyzer.compute_pareto_front()
    all_solutions = pareto_analyzer.compute_all_solutions()
    tradeoffs = pareto_analyzer.compute_tradeoff_ratios(front)

    made.append(plot_pareto_scatter(
        front, all_solutions, os.path.join(args.out, _OUTPUT_FILES["pareto_scatter"]),
        tradeoffs=tradeoffs,
    ))
    made.append(plot_parallel_coordinates(
        front, all_solutions, os.path.join(args.out, _OUTPUT_FILES["parallel_coordinates"]),
        include_supplier_share=True,
    ))

    return made


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    made = run(args)
    for m in made:
        print(f"[plot] wrote {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
