# -*- coding: utf-8 -*-
"""
tests/test_merit_order_plot.py — 可視化のスモークテスト（Phase 3）
================================================================
tools/plot_merit_order_suite の各描画関数が例外なく画像を生成することを固定する。
（matplotlib Agg。中身の見た目は人手 QA。ここは "コード経路が壊れていない" ことの網。）
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import matplotlib
matplotlib.use("Agg")

import pytest

from wom.visualization.merit_order import MeritOrderAnalyzer
from wom.visualization.regime_map import RegimeMapAnalyzer
from wom.visualization.pareto_front import ParetoFrontAnalyzer
from tools.plot_merit_order_suite import (
    DEMO_SUPPLIERS,
    DEMO_PLAN_SCENARIOS,
    _apply_shock,
    _horizon_required_qty,
    plot_merit_order_curve,
    plot_merit_order_shift,
    plot_regime_matrix,
    plot_regime_timeline,
    plot_pareto_scatter,
    plot_parallel_coordinates,
    build_supplier_share_axes,
)


def _nonempty(p):
    return os.path.exists(p) and os.path.getsize(p) > 1000


@pytest.fixture
def demo_analyzer():
    analyzer = MeritOrderAnalyzer(DEMO_SUPPLIERS)
    analyzer.validate_suppliers()
    return analyzer


@pytest.fixture
def demo_result(demo_analyzer):
    return demo_analyzer.calculate_merit_order({"week": "T0", "required_qty": 5000})


@pytest.fixture
def demo_horizon(demo_analyzer):
    horizon_results = [
        demo_analyzer.calculate_merit_order({
            "week": f"H{w + 1:02d}",
            "required_qty": _horizon_required_qty(w, 5000),
        })
        for w in range(12)
    ]
    regime_analyzer = RegimeMapAnalyzer(horizon_results)
    return regime_analyzer.classify_horizon(horizon_weeks=12)


@pytest.fixture
def demo_pareto(demo_analyzer):
    plan_results = [
        demo_analyzer.calculate_merit_order({"required_qty": 5000, **sc})
        for sc in DEMO_PLAN_SCENARIOS
    ]
    pa = ParetoFrontAnalyzer.from_merit_order_results(plan_results)
    front = pa.compute_pareto_front()
    all_solutions = pa.compute_all_solutions()
    tradeoffs = pa.compute_tradeoff_ratios(front)
    return front, all_solutions, tradeoffs


# ---------------------------------------------------------------------------
# スモークテスト（7件）
# ---------------------------------------------------------------------------

def test_plot_merit_order_curve(demo_result, tmp_path):
    out = str(tmp_path / "curve.png")
    assert _nonempty(plot_merit_order_curve(demo_result, out))


def test_plot_merit_order_curve_unmet_demand(demo_analyzer, tmp_path):
    """required_qty が総供給量を超えるケースで λ 注記なしでも例外にならないこと"""
    total_capacity = sum(s["max_supply"] for s in DEMO_SUPPLIERS)
    result = demo_analyzer.calculate_merit_order({
        "week": "OVER",
        "required_qty": total_capacity + 5000,
    })
    assert result["fulfillment_rate"] < 1.0

    out = str(tmp_path / "curve_unmet.png")
    assert _nonempty(plot_merit_order_curve(result, out))


def test_plot_merit_order_shift(demo_result, demo_analyzer, tmp_path):
    shocked = _apply_shock(DEMO_SUPPLIERS)
    after_analyzer = MeritOrderAnalyzer(shocked)
    after_analyzer.validate_suppliers()
    after_result = after_analyzer.calculate_merit_order({"week": "T1", "required_qty": 5000})

    # 順位入替が実際に起きていること（テストの前提条件）
    rank_before = {s["supplier_id"]: s["rank"] for s in demo_result["merit_order"]}
    rank_after = {s["supplier_id"]: s["rank"] for s in after_result["merit_order"]}
    assert any(rank_before[sid] != rank_after[sid] for sid in rank_after if sid in rank_before)

    out = str(tmp_path / "shift.png")
    assert _nonempty(plot_merit_order_shift(demo_result, after_result, out))


def test_plot_regime_matrix(demo_horizon, tmp_path):
    out = str(tmp_path / "regime_matrix.png")
    assert _nonempty(plot_regime_matrix(demo_horizon, out))


def test_plot_regime_timeline(demo_horizon, tmp_path):
    out = str(tmp_path / "regime_timeline.png")
    assert _nonempty(plot_regime_timeline(demo_horizon, out))


def test_plot_pareto_scatter(demo_pareto, tmp_path):
    front, all_solutions, tradeoffs = demo_pareto
    out = str(tmp_path / "pareto_scatter.png")
    assert _nonempty(plot_pareto_scatter(front, all_solutions, out, tradeoffs=tradeoffs))


def test_plot_parallel_coordinates(demo_pareto, tmp_path):
    front, all_solutions, _tradeoffs = demo_pareto
    out = str(tmp_path / "parallel_coordinates.png")
    assert _nonempty(
        plot_parallel_coordinates(front, all_solutions, out, include_supplier_share=True)
    )


# ---------------------------------------------------------------------------
# 軸構築ロジックテスト（2件）— 画像ではなく戻り値を検証
# ---------------------------------------------------------------------------

def test_build_supplier_share_axes_union_and_order(demo_pareto):
    """登場サプライヤーの和集合から、Merit Order順（単価昇順）の軸が作られ、
    ある案が使っていないサプライヤーの share が 0.0 であること"""
    _front, all_solutions, _tradeoffs = demo_pareto

    axis_labels, shares = build_supplier_share_axes(all_solutions, top_k=5)

    # 和集合であること: 登場した全サプライヤーが軸に含まれる（top_k=5で全部収まる想定）
    seen_suppliers = set()
    for sol in all_solutions:
        for item in sol["allocation"]["recommended_allocation"]:
            seen_suppliers.add(item["supplier_id"])
    assert seen_suppliers.issubset(set(axis_labels))

    # Merit Order順（単価昇順）に並んでいること
    supplier_axes = [a for a in axis_labels if a != "Others"]
    costs_by_axis = []
    for sid in supplier_axes:
        cost = min(
            item["unit_cost_usd"]
            for sol in all_solutions
            for item in sol["allocation"]["recommended_allocation"]
            if item["supplier_id"] == sid
        )
        costs_by_axis.append(cost)
    assert costs_by_axis == sorted(costs_by_axis)

    # 未使用サプライヤーの share が 0.0（キーが存在する）こと
    for sol, share in zip(all_solutions, shares):
        used = {item["supplier_id"] for item in sol["allocation"]["recommended_allocation"]}
        for label in axis_labels:
            if label != "Others" and label not in used:
                assert share[label] == 0.0


def test_build_supplier_share_axes_others_bucket(demo_pareto):
    """top_k を超えるサプライヤーが Others に集約され、各案の share 合計が1.0であること"""
    _front, all_solutions, _tradeoffs = demo_pareto

    axis_labels, shares = build_supplier_share_axes(all_solutions, top_k=2)

    assert "Others" in axis_labels
    assert len(axis_labels) == 3  # top 2 + Others

    for share in shares:
        assert sum(share.values()) == pytest.approx(1.0, abs=1e-6)


def test_weeks_label_singular_plural():
    """F3: 週数の単複表記"""
    from tools.plot_merit_order_suite import _weeks_label
    assert _weeks_label(0) == "0 weeks"
    assert _weeks_label(1) == "1 week"
    assert _weeks_label(2) == "2 weeks"
    assert _weeks_label(12) == "12 weeks"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
