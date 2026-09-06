"""
Unit Tests for Pareto Front Analyzer
======================================

Phase 2-B: 複数目標（Cost, Quality, Lead Time）の Pareto 最適解計算のテスト
"""

import json

import pytest

from wom.visualization.merit_order import MeritOrderAnalyzer
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


@pytest.fixture
def sample_suppliers():
    """V0 (Phase 3): plan粒度のテスト用サンプルサプライヤー

    required_qty=5000 に対し、以下3つの demand フィルタが互いに
    非支配（Pareto Front上で3点とも共存する）ように設計している：
      - {}                                    -> SUP_002+SUP_001混合（最安）
      - {"min_quality_acceptable": 95}        -> SUP_001+SUP_003混合（高品質）
      - {"max_lead_time_acceptable": 5}       -> SUP_004単独（最短納期）
    """
    return [
        {"supplier_id": "SUP_001", "supplier_name": "Alpha", "unit_cost": 50,
         "max_supply": 3000, "lead_time_days": 14, "quality_score": 95,
         "exchange_rate": 1.0},
        {"supplier_id": "SUP_002", "supplier_name": "Beta", "unit_cost": 48,
         "max_supply": 3000, "lead_time_days": 21, "quality_score": 94,
         "exchange_rate": 1.0},
        {"supplier_id": "SUP_003", "supplier_name": "Gamma", "unit_cost": 55,
         "max_supply": 2000, "lead_time_days": 7, "quality_score": 96,
         "exchange_rate": 1.0},
        {"supplier_id": "SUP_004", "supplier_name": "Delta", "unit_cost": 70,
         "max_supply": 5000, "lead_time_days": 5, "quality_score": 80,
         "exchange_rate": 1.0},
    ]


class TestPlanGranularity:
    """V0: 1配分案=1解の粒度（Phase 3-0）"""

    def test_plan_objectives_matches_merit_order_totals(self, sample_suppliers):
        """compute_plan_objectives() が merit_order result の
        total_cost / average_quality / average_lead_time と一致する"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({"required_qty": 5000})

        pa = ParetoFrontAnalyzer.from_merit_order_results([result])
        obj = pa.compute_plan_objectives(result)

        assert obj["cost"] == result["total_cost"]
        assert obj["quality"] == result["average_quality"]
        assert obj["lead_time"] == result["average_lead_time"]

    def test_pareto_front_from_plans_multiple_on_front(self, sample_suppliers):
        """異なる制約で生成した複数の配分案を与えたとき、
        Pareto Front が1点に収束しないこと（§V0.1 の症状に対する回帰テスト）

        注: merit_order.py の実装上、品質・リードタイムのフィルタは
        `constraints` ではなく `demand` 辞書のキー
        （min_quality_acceptable / max_lead_time_acceptable）で指定する。
        """
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        scenarios = [
            {},                                  # 制約なし（最安）
            {"min_quality_acceptable": 95},      # 高品質のみ
            {"max_lead_time_acceptable": 5},     # 短納期のみ
        ]
        results = [
            analyzer.calculate_merit_order({"required_qty": 5000, **sc})
            for sc in scenarios
        ]
        pa = ParetoFrontAnalyzer.from_merit_order_results(results)
        front = pa.compute_pareto_front()

        assert len(front) >= 2, (
            "Pareto Front collapsed to a single point — "
            "the plan-granularity fix is not working"
        )
        # このフィクスチャでは実際に3案とも互いに非支配であることまで確認する
        assert len(front) == 3

    def test_pareto_front_record_granularity_backward_compat(self, sample_suppliers):
        """従来の __init__(allocations) 経路が補正前と同じ結果を返すこと"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({"required_qty": 5000})
        allocations = result["recommended_allocation"]

        pa = ParetoFrontAnalyzer(allocations)
        assert pa._granularity == "record"

        front = pa.compute_pareto_front()
        # 各解の cost が allocated_qty * unit_cost_usd（総額）であること
        for item in front:
            a = item["allocation"]
            assert item["cost"] == a["allocated_qty"] * a["unit_cost_usd"]

    def test_lead_time_metric_max_option(self, sample_suppliers):
        """lead_time_metric='max' で最大リードタイムが採られること"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({"required_qty": 5000})

        pa_max = ParetoFrontAnalyzer.from_merit_order_results(
            [result], lead_time_metric="max"
        )
        expected = max(a["lead_time_days"]
                       for a in result["recommended_allocation"])
        assert pa_max.compute_plan_objectives(result)["lead_time"] == expected

        # 不正値は ValueError
        with pytest.raises(ValueError, match="lead_time_metric"):
            ParetoFrontAnalyzer.from_merit_order_results([result],
                                                         lead_time_metric="median")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
