"""
Regime Map Analyzer
====================

Merit Order（Phase 1）の週次分析結果から、市場状況（需要レベル×供給タイト度）を
3×3 マトリクスに分類し、各セルに対応する推奨調達戦略を提示する。

設計根拠: requests/Phase2_DesignMD_RegimeMap.md セクション 2

使用例:
    >>> regime_analyzer = RegimeMapAnalyzer(merit_order_results)
    >>> regime = regime_analyzer.classify_single_week(merit_order_results[0])
    >>> horizon = regime_analyzer.classify_horizon(horizon_weeks=12)
"""

import json
import math
import os
from collections import Counter
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# 分類ロジック（モジュールレベル関数）
# ---------------------------------------------------------------------------

def classify_demand_level(fulfillment_rate: float) -> str:
    """
    fulfillment_rate に基づいて需要レベルを分類

    Args:
        fulfillment_rate: Merit Order 結果の fulfillment_rate (0.0 - 1.0)

    Returns:
        "Low"    : fulfillment_rate >= 0.95  (95%以上充足 = 供給過多)
        "Medium" : 0.75 <= fulfillment_rate < 0.95
        "High"   : fulfillment_rate < 0.75   (75%未満 = 需要が供給を超過)
    """
    if fulfillment_rate is None:
        raise ValueError("fulfillment_rate is required")

    if fulfillment_rate >= 0.95:
        return "Low"
    elif fulfillment_rate >= 0.75:
        return "Medium"
    else:
        return "High"


def classify_supply_tightness(average_lead_time: float,
                               demand_lead_time_threshold: float = 14,
                               tightness_range: float = 7) -> str:
    """
    平均リードタイムに基づいて供給タイト度を分類

    Args:
        average_lead_time: Merit Order 結果の average_lead_time (days)
        demand_lead_time_threshold: 需要計画のリードタイム閾値（デフォルト 14日）
        tightness_range: 閾値からの許容幅（デフォルト 7日）

    Returns:
        "Tight"    : average_lead_time > threshold + range   (21日超 = リードタイム長い)
        "Balanced" : threshold - range <= average_lead_time <= threshold + range
        "Surplus"  : average_lead_time < threshold - range   (7日未満 = 短い)
    """
    if average_lead_time is None:
        raise ValueError("average_lead_time is required")

    upper = demand_lead_time_threshold + tightness_range
    lower = demand_lead_time_threshold - tightness_range

    if average_lead_time > upper:
        return "Tight"
    elif average_lead_time < lower:
        return "Surplus"
    else:
        return "Balanced"


# (supply_tightness, demand_level) -> recommended strategy
REGIME_STRATEGIES = {
    # Supply Tight 行 (Risk Mode)
    ("Tight", "Low"):      "cost_lock",
    ("Tight", "Medium"):   "dual_source",
    ("Tight", "High"):     "safety_stock",

    # Supply Balanced 行
    ("Balanced", "Low"):    "lean",
    ("Balanced", "Medium"): "merit_order",
    ("Balanced", "High"):   "lead_time_hedge",

    # Supply Surplus 行 (Opportunity)
    ("Surplus", "Low"):    "consolidation",
    ("Surplus", "Medium"): "price_negotiation",
    ("Surplus", "High"):   "quality_upgrade",
}

# regime matrix の行・列インデックス（Section 2.1 の図と対応）
_SUPPLY_ROW_INDEX = {"Tight": 1, "Balanced": 2, "Surplus": 3}
_DEMAND_COL_INDEX = {"Low": 1, "Medium": 2, "High": 3}


class RegimeMapAnalyzer:
    """
    Merit Order 結果から Regime Map を分類し、推奨戦略を提示

    入力: Merit Order 分析結果（複数週の結果リスト）
    出力: 市場環境分類 + 推奨戦略 + KPI
    """

    # strategy -> {actions, kpi_targets}（Section 2.3 の表に基づく）
    STRATEGY_ACTIONS = {
        "cost_lock": {
            "actions": [
                {
                    "action": "lock_low_cost_supplier",
                    "parameter": "top_1_by_cost",
                    "impact": "Cost savings secured via fixed contract",
                },
            ],
            "kpi_targets": {
                "max_lead_time_days": 21,
                "min_quality_score": 85,
                "cost_tolerance_pct": 2,
            },
        },
        "dual_source": {
            "actions": [
                {
                    "action": "diversify_suppliers",
                    "parameter": "top_2_by_cost",
                    "impact": "Reduced single-source dependency risk",
                },
            ],
            "kpi_targets": {
                "max_lead_time_days": 21,
                "min_quality_score": 85,
                "cost_tolerance_pct": 5,
            },
        },
        "safety_stock": {
            "actions": [
                {
                    "action": "increase_safety_stock",
                    "parameter": 1.5,  # average_lead_time の何週分を確保するか
                    "impact": "Fulfillment rate improvement under tight supply",
                },
            ],
            "kpi_targets": {
                "max_lead_time_days": 21,
                "min_quality_score": 90,
                "cost_tolerance_pct": 5,
            },
        },
        "lean": {
            "actions": [
                {
                    "action": "just_in_time_allocation",
                    "parameter": "min_inventory",
                    "impact": "Lower carrying cost",
                },
            ],
            "kpi_targets": {
                "max_lead_time_days": 14,
                "min_quality_score": 85,
                "cost_tolerance_pct": 1,
            },
        },
        "merit_order": {
            "actions": [
                {
                    "action": "apply_default_merit_order",
                    "parameter": None,
                    "impact": "Standard least-cost allocation",
                },
            ],
            "kpi_targets": {
                "max_lead_time_days": 14,
                "min_quality_score": 85,
                "cost_tolerance_pct": 2,
            },
        },
        "lead_time_hedge": {
            "actions": [
                {
                    "action": "hedge_lead_time_variation",
                    "parameter": "buffer_stock",
                    "impact": "Protection against lead time variability",
                },
            ],
            "kpi_targets": {
                "max_lead_time_days": 21,
                "min_quality_score": 88,
                "cost_tolerance_pct": 5,
            },
        },
        "consolidation": {
            "actions": [
                {
                    "action": "consolidate_suppliers",
                    "parameter": "reduce_supplier_count",
                    "impact": "Lower administrative and transaction cost",
                },
            ],
            "kpi_targets": {
                "max_lead_time_days": 14,
                "min_quality_score": 85,
                "cost_tolerance_pct": 1,
            },
        },
        "price_negotiation": {
            "actions": [
                {
                    "action": "negotiate_price",
                    "parameter": "leverage_competition",
                    "impact": "Cost reduction via supplier competition",
                },
            ],
            "kpi_targets": {
                "max_lead_time_days": 14,
                "min_quality_score": 85,
                "cost_tolerance_pct": 3,
            },
        },
        "quality_upgrade": {
            "actions": [
                {
                    "action": "restrict_to_high_quality",
                    "parameter": 95,
                    "impact": "Improved quality at premium cost",
                },
            ],
            "kpi_targets": {
                "max_lead_time_days": 14,
                "min_quality_score": 95,
                "cost_tolerance_pct": 8,
            },
        },
    }

    def __init__(self, merit_order_results: List[Dict],
                 config: Optional[Dict] = None):
        """
        初期化

        Args:
            merit_order_results: [
                {
                    "week": "2026-W36",
                    "fulfillment_rate": 0.95,
                    "average_lead_time": 18.5,
                    ...
                },
                ...
            ]
            config: {
                "demand_lead_time_threshold": 14,  # デフォルト
                "supply_tightness_range": 7,       # デフォルト
            }
        """
        self.merit_order_results = list(merit_order_results) if merit_order_results else []
        config = config or {}
        self.demand_lead_time_threshold = config.get("demand_lead_time_threshold", 14)
        self.supply_tightness_range = config.get("supply_tightness_range", 7)

    def classify_single_week(self, merit_order_result: Dict) -> Dict:
        """単一週のレジーム分類

        Args:
            merit_order_result: 1週分の Merit Order 結果

        Returns:
            {
                "week": "2026-W36",
                "demand_level": "High",
                "supply_tightness": "Tight",
                "regime_cell": (1, 3),
                "recommended_strategy": "safety_stock",
                "regime_score": {  # 0-10 スコア
                    "demand_pressure": 8.2,
                    "supply_risk": 7.5,
                },
            }
        """
        if "fulfillment_rate" not in merit_order_result:
            raise ValueError(
                "merit_order_result is missing required field 'fulfillment_rate'"
            )
        if "average_lead_time" not in merit_order_result:
            raise ValueError(
                "merit_order_result is missing required field 'average_lead_time'"
            )

        fulfillment_rate = merit_order_result["fulfillment_rate"]
        average_lead_time = merit_order_result["average_lead_time"]

        demand_level = classify_demand_level(fulfillment_rate)
        supply_tightness = classify_supply_tightness(
            average_lead_time,
            self.demand_lead_time_threshold,
            self.supply_tightness_range,
        )

        regime_cell = (
            _SUPPLY_ROW_INDEX[supply_tightness],
            _DEMAND_COL_INDEX[demand_level],
        )
        recommended_strategy = REGIME_STRATEGIES[(supply_tightness, demand_level)]

        # regime_score: 0-10 スケールの連続指標（分類の閾値とは独立に算出）
        demand_pressure = round(
            min(10.0, max(0.0, 10.0 * (1.0 - fulfillment_rate))), 2
        )
        threshold = max(self.demand_lead_time_threshold, 1e-9)
        supply_risk = round(
            min(10.0, max(0.0, 10.0 * average_lead_time / (2.0 * threshold))), 2
        )

        return {
            "week": merit_order_result.get("week"),
            "demand_level": demand_level,
            "supply_tightness": supply_tightness,
            "regime_cell": regime_cell,
            "recommended_strategy": recommended_strategy,
            "regime_score": {
                "demand_pressure": demand_pressure,
                "supply_risk": supply_risk,
            },
        }

    def classify_horizon(self, horizon_weeks: int = 12) -> Dict:
        """複数週の Regime 遷移を分析

        Args:
            horizon_weeks: 分析対象とする週数
                （merit_order_results の先頭から最大 horizon_weeks 件を使用）

        Returns:
            {
                "summary": {
                    "dominant_regime": "Balanced/Medium",
                    "risk_weeks": [3, 7, 11],          # Supply=Tight の週（1始まりの位置）
                    "opportunity_weeks": [2, 5],        # Supply=Surplus の週（1始まりの位置）
                },
                "week_by_week": [
                    {...},  # each week's regime
                ],
                "transition_matrix": {
                    # (week i → week i+1) の遷移確率
                },
            }
        """
        if not self.merit_order_results:
            raise ValueError("No merit_order_results provided")

        window = self.merit_order_results[:horizon_weeks]
        week_by_week = [self.classify_single_week(r) for r in window]

        labels = [
            f"{w['supply_tightness']}/{w['demand_level']}" for w in week_by_week
        ]

        counts = Counter(labels)
        dominant_regime = counts.most_common(1)[0][0] if counts else None

        # Section 2.1 の図の行ラベル（Risk Mode = Tight行, Opportunity = Surplus行）に対応
        risk_weeks = [
            i + 1 for i, w in enumerate(week_by_week)
            if w["supply_tightness"] == "Tight"
        ]
        opportunity_weeks = [
            i + 1 for i, w in enumerate(week_by_week)
            if w["supply_tightness"] == "Surplus"
        ]

        transition_counts: Dict[str, Dict[str, int]] = {}
        for i in range(len(labels) - 1):
            src, dst = labels[i], labels[i + 1]
            transition_counts.setdefault(src, {})
            transition_counts[src][dst] = transition_counts[src].get(dst, 0) + 1

        transition_matrix: Dict[str, Dict[str, float]] = {}
        for src, dst_counts in transition_counts.items():
            total = sum(dst_counts.values())
            transition_matrix[src] = {
                dst: round(cnt / total, 4) for dst, cnt in dst_counts.items()
            }

        return {
            "summary": {
                "dominant_regime": dominant_regime,
                "risk_weeks": risk_weeks,
                "opportunity_weeks": opportunity_weeks,
            },
            "week_by_week": week_by_week,
            "transition_matrix": transition_matrix,
        }

    def get_strategy_actions(self, regime_dict: Dict) -> Dict:
        """Regime に対応する具体的なアクション提示

        Args:
            regime_dict: classify_single_week() の戻り値
                （少なくとも "recommended_strategy" キーを含む dict）

        Returns:
            {
                "strategy": "safety_stock",
                "actions": [
                    {
                        "action": "increase_safety_stock",
                        "parameter": 1.5,
                        "impact": "Fulfillment rate improvement under tight supply",
                    },
                    ...
                ],
                "kpi_targets": {
                    "max_lead_time_days": 21,
                    "min_quality_score": 90,
                    "cost_tolerance_pct": 5,
                },
            }

        Raises:
            ValueError: 未知の strategy が指定された場合
        """
        strategy = regime_dict.get("recommended_strategy")
        if strategy not in self.STRATEGY_ACTIONS:
            raise ValueError(f"Unknown strategy: {strategy}")

        spec = self.STRATEGY_ACTIONS[strategy]
        return {
            "strategy": strategy,
            "actions": [dict(a) for a in spec["actions"]],
            "kpi_targets": dict(spec["kpi_targets"]),
        }

    def export_to_json(self, regime_analysis: Dict, filepath: str) -> bool:
        """結果を JSON で保存

        Args:
            regime_analysis: classify_single_week() または classify_horizon() の戻り値
            filepath: 出力ファイルパス

        Returns:
            bool: 成功時 True、失敗時 False
        """
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(regime_analysis, f, indent=2, ensure_ascii=False)

            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                print(f"✅ Regime analysis exported to {filepath}")
                return True
            else:
                print(f"❌ Export failed: file not created or empty")
                return False

        except IOError as e:
            print(f"❌ IOError: Cannot write to {filepath}: {e}")
            return False

        except TypeError as e:
            print(f"❌ TypeError: Result contains non-serializable data: {e}")
            return False

        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return False
