"""
Unit Tests for Regime Map Analyzer
====================================

Phase 2-A: 需要レベル×供給タイト度の 3×3 マトリクス分類のテスト
"""

import json

import pytest

from wom.visualization.regime_map import (
    RegimeMapAnalyzer,
    REGIME_STRATEGIES,
    classify_demand_level,
    classify_supply_tightness,
)


# 9セル全ての代表値（Section 2.2 の閾値に基づく）
DEMAND_LEVEL_SAMPLES = {
    "Low": 0.98,      # >= 0.95
    "Medium": 0.85,   # 0.75 <= x < 0.95
    "High": 0.60,     # < 0.75
}
SUPPLY_TIGHTNESS_SAMPLES = {
    "Tight": 25,      # > 14+7=21
    "Balanced": 14,   # within [7, 21]
    "Surplus": 5,     # < 14-7=7
}
EXPECTED_CELL = {
    "Tight": 1, "Balanced": 2, "Surplus": 3,
}
EXPECTED_COL = {
    "Low": 1, "Medium": 2, "High": 3,
}


def make_merit_order_result(week, fulfillment_rate, average_lead_time):
    return {
        "week": week,
        "fulfillment_rate": fulfillment_rate,
        "average_lead_time": average_lead_time,
    }


class TestClassificationFunctions:
    """分類関数（モジュールレベル）のテスト（6個）"""

    def test_classify_demand_level_low(self):
        assert classify_demand_level(0.98) == "Low"

    def test_classify_demand_level_medium(self):
        assert classify_demand_level(0.85) == "Medium"

    def test_classify_demand_level_high(self):
        assert classify_demand_level(0.60) == "High"

    def test_classify_supply_tightness_tight(self):
        assert classify_supply_tightness(25) == "Tight"

    def test_classify_supply_tightness_balanced(self):
        assert classify_supply_tightness(14) == "Balanced"

    def test_classify_supply_tightness_surplus(self):
        assert classify_supply_tightness(5) == "Surplus"


class TestRegimeMapping:
    """マッピングテスト（3個）"""

    def test_regime_cell_mapping(self):
        """9 セル全て正しくマッピングされることを確認"""
        analyzer = RegimeMapAnalyzer([])
        for supply_tightness, lead_time in SUPPLY_TIGHTNESS_SAMPLES.items():
            for demand_level, fulfillment in DEMAND_LEVEL_SAMPLES.items():
                result = analyzer.classify_single_week(
                    make_merit_order_result("W1", fulfillment, lead_time)
                )
                expected_cell = (
                    EXPECTED_CELL[supply_tightness],
                    EXPECTED_COL[demand_level],
                )
                assert result["regime_cell"] == expected_cell, (
                    f"{supply_tightness}/{demand_level} -> "
                    f"expected {expected_cell}, got {result['regime_cell']}"
                )

    def test_recommended_strategy_lookup(self):
        """全 9 戦略が正しく取得されることを確認"""
        analyzer = RegimeMapAnalyzer([])
        for supply_tightness, lead_time in SUPPLY_TIGHTNESS_SAMPLES.items():
            for demand_level, fulfillment in DEMAND_LEVEL_SAMPLES.items():
                result = analyzer.classify_single_week(
                    make_merit_order_result("W1", fulfillment, lead_time)
                )
                expected_strategy = REGIME_STRATEGIES[(supply_tightness, demand_level)]
                assert result["recommended_strategy"] == expected_strategy

        # 9通り全部で戦略が異なる（辞書としての一意性）ことも確認
        assert len(set(REGIME_STRATEGIES.values())) == 9

    def test_strategy_actions_consistency(self):
        """推奨戦略とアクション内容に矛盾がないことを確認"""
        analyzer = RegimeMapAnalyzer([])
        for strategy in REGIME_STRATEGIES.values():
            regime_dict = {"recommended_strategy": strategy}
            actions_result = analyzer.get_strategy_actions(regime_dict)

            assert actions_result["strategy"] == strategy
            assert len(actions_result["actions"]) > 0
            for action in actions_result["actions"]:
                assert "action" in action
                assert "impact" in action

            kpi_targets = actions_result["kpi_targets"]
            assert "max_lead_time_days" in kpi_targets
            assert "min_quality_score" in kpi_targets
            assert "cost_tolerance_pct" in kpi_targets

        # 未知の戦略は ValueError
        with pytest.raises(ValueError):
            analyzer.get_strategy_actions({"recommended_strategy": "unknown_strategy"})


class TestIntegration:
    """統合テスト（3個）"""

    def test_classify_single_week(self):
        """Merit Order 結果から regime 出力まで一連処理"""
        analyzer = RegimeMapAnalyzer([])
        mo_result = make_merit_order_result("2026-W36", 0.60, 25)  # High demand, Tight supply
        regime = analyzer.classify_single_week(mo_result)

        assert regime["week"] == "2026-W36"
        assert regime["demand_level"] == "High"
        assert regime["supply_tightness"] == "Tight"
        assert regime["regime_cell"] == (1, 3)
        assert regime["recommended_strategy"] == "safety_stock"
        assert 0 <= regime["regime_score"]["demand_pressure"] <= 10
        assert 0 <= regime["regime_score"]["supply_risk"] <= 10

        # 必須フィールド欠落時は ValueError
        with pytest.raises(ValueError):
            analyzer.classify_single_week({"week": "2026-W37"})

    def test_classify_horizon_12weeks(self):
        """12 週のホライズン分析"""
        merit_order_results = []
        for w in range(1, 13):
            # 週によって demand/supply の組み合わせを変化させる
            if w % 4 == 0:
                fulfillment, lead_time = 0.60, 25  # High demand, Tight supply (risk)
            elif w % 4 == 1:
                fulfillment, lead_time = 0.98, 5   # Low demand, Surplus supply (opportunity)
            else:
                fulfillment, lead_time = 0.85, 14  # Medium demand, Balanced supply

            merit_order_results.append(
                make_merit_order_result(f"2026-W{35+w:02d}", fulfillment, lead_time)
            )

        analyzer = RegimeMapAnalyzer(merit_order_results)
        horizon = analyzer.classify_horizon(horizon_weeks=12)

        assert len(horizon["week_by_week"]) == 12
        assert isinstance(horizon["summary"]["dominant_regime"], str)
        assert len(horizon["summary"]["risk_weeks"]) > 0
        assert len(horizon["summary"]["opportunity_weeks"]) > 0
        # risk_weeks は Tight 週 (4, 8, 12) と一致
        assert horizon["summary"]["risk_weeks"] == [4, 8, 12]
        # opportunity_weeks は Surplus 週 (1, 5, 9) と一致
        assert horizon["summary"]["opportunity_weeks"] == [1, 5, 9]
        assert isinstance(horizon["transition_matrix"], dict)

        # horizon_weeks より少ないデータの場合は、あるだけ使う
        analyzer_short = RegimeMapAnalyzer(merit_order_results[:3])
        horizon_short = analyzer_short.classify_horizon(horizon_weeks=12)
        assert len(horizon_short["week_by_week"]) == 3

        # データが1件もない場合は ValueError
        with pytest.raises(ValueError):
            RegimeMapAnalyzer([]).classify_horizon()

    def test_export_to_json_regime(self, tmp_path):
        """JSON エクスポート・フォーマット確認"""
        analyzer = RegimeMapAnalyzer([])
        regime = analyzer.classify_single_week(
            make_merit_order_result("2026-W36", 0.85, 14)
        )

        output_file = tmp_path / "regime_output.json"
        success = analyzer.export_to_json(regime, str(output_file))

        assert success is True
        assert output_file.exists()

        with open(output_file) as f:
            saved = json.load(f)
        assert saved["week"] == "2026-W36"
        assert saved["recommended_strategy"] == "merit_order"
        # JSON ではタプルはリストとしてシリアライズされる
        assert saved["regime_cell"] == [2, 2]

        # 不正パスでは False
        success_invalid = analyzer.export_to_json(
            regime, "/invalid/nonexistent/path/output.json"
        )
        assert success_invalid is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
