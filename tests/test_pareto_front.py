"""
Unit Tests for Pareto Front Analyzer
======================================

Phase 2-B: 複数目標（Cost, Quality, Lead Time）の Pareto 最適解計算のテスト
"""

import json

import pytest

from wom.visualization.pareto_front import ParetoFrontAnalyzer


class TestObjectiveFunctions:
    """目的関数テスト（2個）"""

    def test_compute_allocation_objectives_simple(self):
        """単一サプライヤー配分"""
        allocation = {
            "supplier_id": "SUP_001",
            "allocated_qty": 2000,
            "unit_cost_usd": 10,
            "quality_score": 95,
            "lead_time_days": 14,
        }
        analyzer = ParetoFrontAnalyzer([allocation])
        objectives = analyzer.compute_allocation_objectives(allocation)

        assert objectives["cost"] == 20000
        assert objectives["quality"] == 95
        assert objectives["lead_time"] == 14

    def test_compute_allocation_objectives_mixed(self):
        """複数サプライヤー混合配分（異なる規模・単価の配分案）"""
        allocation = {
            "supplier_id": "SUP_MIX",
            "allocated_qty": 750,
            "unit_cost_usd": 15.5,
            "quality_score": 88.5,
            "lead_time_days": 21,
        }
        analyzer = ParetoFrontAnalyzer([allocation])
        objectives = analyzer.compute_allocation_objectives(allocation)

        assert objectives["cost"] == pytest.approx(750 * 15.5)
        assert objectives["quality"] == 88.5
        assert objectives["lead_time"] == 21


class TestParetoFront:
    """Pareto Front テスト（3個）"""

    def test_pareto_front_single_solution(self):
        """配分案が 1 つのみ"""
        allocation = {
            "supplier_id": "SUP_001",
            "allocated_qty": 1000,
            "unit_cost_usd": 10,
            "quality_score": 90,
            "lead_time_days": 14,
        }
        analyzer = ParetoFrontAnalyzer([allocation])
        front = analyzer.compute_pareto_front()

        assert len(front) == 1
        assert front[0]["rank"] == 1
        assert front[0]["dominated_count"] == 0
        assert front[0]["cost"] == 10000

    def test_pareto_front_all_on_front(self):
        """全案が Pareto Front 上（互いにトレードオフの関係）"""
        allocations = [
            # 安いがLTが長く品質が低い
            {"supplier_id": "SUP_CHEAP", "allocated_qty": 1000,
             "unit_cost_usd": 10, "quality_score": 80, "lead_time_days": 28},
            # 中間
            {"supplier_id": "SUP_MID", "allocated_qty": 1000,
             "unit_cost_usd": 12, "quality_score": 90, "lead_time_days": 14},
            # 高いが品質が良くLTが短い
            {"supplier_id": "SUP_PREMIUM", "allocated_qty": 1000,
             "unit_cost_usd": 15, "quality_score": 98, "lead_time_days": 7},
        ]
        analyzer = ParetoFrontAnalyzer(allocations)
        front = analyzer.compute_pareto_front()

        assert len(front) == 3
        # cost 昇順
        assert [f["rank"] for f in front] == [1, 2, 3]
        assert front[0]["cost"] <= front[1]["cost"] <= front[2]["cost"]
        for f in front:
            assert f["dominated_count"] == 0

    def test_pareto_front_dominated_solutions(self):
        """支配関係がある配分（1件は他に完全に劣後）"""
        allocations = [
            # 優れた解: 安くて高品質・短LT
            {"supplier_id": "SUP_GOOD", "allocated_qty": 1000,
             "unit_cost_usd": 10, "quality_score": 95, "lead_time_days": 7},
            # 劣後する解: 全軸で SUP_GOOD より悪い
            {"supplier_id": "SUP_BAD", "allocated_qty": 1000,
             "unit_cost_usd": 12, "quality_score": 85, "lead_time_days": 21},
        ]
        analyzer = ParetoFrontAnalyzer(allocations)
        front = analyzer.compute_pareto_front()

        assert len(front) == 1
        assert front[0]["allocation"]["supplier_id"] == "SUP_GOOD"
        assert front[0]["dominated_count"] == 1


class TestTradeoffRatios:
    """トレードオフテスト（2個）"""

    def test_tradeoff_ratios_cost_vs_quality(self):
        allocations = [
            {"supplier_id": "SUP_A", "allocated_qty": 1000,
             "unit_cost_usd": 10, "quality_score": 90, "lead_time_days": 14},
            {"supplier_id": "SUP_B", "allocated_qty": 1000,
             "unit_cost_usd": 10.5, "quality_score": 93.2, "lead_time_days": 14},
        ]
        analyzer = ParetoFrontAnalyzer(allocations)
        front = analyzer.compute_pareto_front()
        tradeoffs = analyzer.compute_tradeoff_ratios(front)

        assert len(tradeoffs["cost_vs_quality"]) == 1
        entry = tradeoffs["cost_vs_quality"][0]
        assert entry["from_rank"] == 1
        assert entry["to_rank"] == 2
        assert entry["cost_increase_pct"] == pytest.approx(5.0, abs=0.01)
        assert entry["quality_gain_points"] == pytest.approx(3.2, abs=0.01)

    def test_tradeoff_ratios_cost_vs_lead_time(self):
        allocations = [
            {"supplier_id": "SUP_A", "allocated_qty": 1000,
             "unit_cost_usd": 10, "quality_score": 90, "lead_time_days": 21},
            {"supplier_id": "SUP_B", "allocated_qty": 1000,
             "unit_cost_usd": 12, "quality_score": 90, "lead_time_days": 7},
        ]
        analyzer = ParetoFrontAnalyzer(allocations)
        front = analyzer.compute_pareto_front()
        tradeoffs = analyzer.compute_tradeoff_ratios(front)

        assert len(tradeoffs["cost_vs_lead_time"]) == 1
        entry = tradeoffs["cost_vs_lead_time"][0]
        assert entry["from_rank"] == 1
        assert entry["to_rank"] == 2
        assert entry["cost_increase_pct"] == pytest.approx(20.0, abs=0.01)
        assert entry["lead_time_change_days"] == pytest.approx(-14.0, abs=0.01)


class TestEndToEnd:
    """統合テスト（1個）"""

    def test_pareto_export_to_json(self, tmp_path):
        allocations = [
            {"supplier_id": "SUP_A", "allocated_qty": 1000,
             "unit_cost_usd": 10, "quality_score": 90, "lead_time_days": 14},
            {"supplier_id": "SUP_B", "allocated_qty": 1000,
             "unit_cost_usd": 15, "quality_score": 98, "lead_time_days": 7},
        ]
        analyzer = ParetoFrontAnalyzer(allocations)
        front = analyzer.compute_pareto_front()

        output_file = tmp_path / "pareto_output.json"
        success = analyzer.export_to_json(front, str(output_file))

        assert success is True
        assert output_file.exists()

        with open(output_file) as f:
            saved = json.load(f)
        assert len(saved) == 2
        assert saved[0]["rank"] == 1

        # 不正パスでは False
        success_invalid = analyzer.export_to_json(
            front, "/invalid/nonexistent/path/output.json"
        )
        assert success_invalid is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
