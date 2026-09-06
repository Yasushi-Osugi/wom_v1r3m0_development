"""
Pareto Front Analyzer
======================

Merit Order（Phase 1）が算出する配分案（Cost, Quality, Lead Time）について、
複数目的の Pareto 最適解（トレードオフの前線）を計算する。

設計根拠: requests/Phase2_DesignMD_RegimeMap.md セクション 3

使用例:
    >>> pareto_analyzer = ParetoFrontAnalyzer(allocations_list)
    >>> front = pareto_analyzer.compute_pareto_front()
    >>> tradeoffs = pareto_analyzer.compute_tradeoff_ratios(front)
"""

import json
import os
from typing import Dict, List, Optional


class ParetoFrontAnalyzer:
    """
    複数目標（Cost, Quality, Lead Time）の Pareto 最適解を計算

    入力: Merit Order の recommended_allocation（複数サプライヤー案）
    出力: Pareto Front 上の解の集合 + トレードオフ分析

    最適性の定義（Section 3.2）:
        解 A が解 B より「Pareto 支配」される
         ⟺ すべての目的軸で A が B 以上に悪く、少なくとも1軸で厳密に悪い
        Pareto Front = 支配されない解の集合

    目的関数の向き:
        cost      : 小さいほど良い
        quality   : 大きいほど良い
        lead_time : 小さいほど良い
    """

    DEFAULT_OBJECTIVES = ["cost", "quality", "lead_time"]

    def __init__(self, allocations: List[Dict],
                 objectives: Optional[List[str]] = None):
        """
        初期化

        Args:
            allocations: [
                {
                    "supplier_id": "SUP_001",
                    "allocated_qty": 2000,
                    "unit_cost_usd": 10,
                    "quality_score": 95,
                    "lead_time_days": 14,
                },
                ...
            ]
            objectives: ["cost", "quality", "lead_time"] (デフォルト)
        """
        self.allocations = list(allocations) if allocations else []
        self.objectives = list(objectives) if objectives else list(self.DEFAULT_OBJECTIVES)

    def compute_allocation_objectives(self, allocation: Dict) -> Dict:
        """1 配分案の目的関数値を計算

        Args:
            allocation: サプライヤー配分 1 件
                {"allocated_qty": ..., "unit_cost_usd": ...,
                 "quality_score": ..., "lead_time_days": ...}

        Returns:
            {
                "cost": 20000,              # Total cost (USD) = allocated_qty * unit_cost_usd
                "quality": 92.5,            # Quality score (0-100)
                "lead_time": 16.2,          # Lead time (days)
            }
        """
        allocated_qty = allocation.get("allocated_qty", 0)
        unit_cost = allocation.get("unit_cost_usd", allocation.get("unit_cost", 0))
        quality = allocation.get("quality_score", 0)
        lead_time = allocation.get("lead_time_days", 0)

        cost = allocated_qty * unit_cost

        return {
            "cost": cost,
            "quality": quality,
            "lead_time": lead_time,
        }

    @staticmethod
    def _dominates(a: Dict, b: Dict) -> bool:
        """a が b を Pareto 支配するか判定

        全軸で a が b 以上に良い（cost/lead_timeは小さいほど良い、qualityは大きいほど良い）
        かつ、少なくとも1軸で厳密に良い場合に True。
        """
        at_least_as_good = (
            a["cost"] <= b["cost"]
            and a["quality"] >= b["quality"]
            and a["lead_time"] <= b["lead_time"]
        )
        strictly_better = (
            a["cost"] < b["cost"]
            or a["quality"] > b["quality"]
            or a["lead_time"] < b["lead_time"]
        )
        return at_least_as_good and strictly_better

    def compute_pareto_front(self) -> List[Dict]:
        """Pareto Front を計算（O(n²) 素朴実装、n=配分案数は通常10-100程度想定）

        Returns:
            [
                {
                    "rank": 1,
                    "cost": 20000,
                    "quality": 92.5,
                    "lead_time": 16.2,
                    "allocation": {...},  # 対応する配分
                    "dominated_count": 0,  # このソリューションが支配している他解の数
                },
                ...
            ]
            （cost 昇順ソート）
        """
        solutions = [
            {"objectives": self.compute_allocation_objectives(a), "allocation": a}
            for a in self.allocations
        ]

        front = []
        for i, sol in enumerate(solutions):
            dominated_by_someone = False
            dominated_count = 0
            for j, other in enumerate(solutions):
                if i == j:
                    continue
                if self._dominates(other["objectives"], sol["objectives"]):
                    dominated_by_someone = True
                if self._dominates(sol["objectives"], other["objectives"]):
                    dominated_count += 1

            if not dominated_by_someone:
                front.append({
                    "rank": None,  # cost 昇順ソート後に採番
                    "cost": sol["objectives"]["cost"],
                    "quality": sol["objectives"]["quality"],
                    "lead_time": sol["objectives"]["lead_time"],
                    "allocation": sol["allocation"],
                    "dominated_count": dominated_count,
                })

        front.sort(key=lambda x: x["cost"])
        for idx, item in enumerate(front, start=1):
            item["rank"] = idx

        return front

    def compute_tradeoff_ratios(self, front: List[Dict]) -> Dict:
        """Pareto Front 上でのトレードオフ比率を計算（cost 昇順の隣接ランク間）

        Args:
            front: compute_pareto_front() の戻り値

        Returns:
            {
                "cost_vs_quality": [
                    {
                        "from_rank": 1,
                        "to_rank": 2,
                        "cost_increase_pct": 5.0,
                        "quality_gain_points": 3.2,
                        "ratio": "5.0% cost increase per 3.2 quality point",
                    },
                    ...
                ],
                "cost_vs_lead_time": [...],
            }
        """
        sorted_front = sorted(front or [], key=lambda x: x["rank"])

        cost_vs_quality = []
        cost_vs_lead_time = []

        for i in range(len(sorted_front) - 1):
            a = sorted_front[i]
            b = sorted_front[i + 1]

            if a["cost"] != 0:
                cost_increase_pct = round((b["cost"] - a["cost"]) / a["cost"] * 100, 2)
            else:
                cost_increase_pct = 0.0

            quality_gain = round(b["quality"] - a["quality"], 2)
            lead_time_change = round(b["lead_time"] - a["lead_time"], 2)

            cost_vs_quality.append({
                "from_rank": a["rank"],
                "to_rank": b["rank"],
                "cost_increase_pct": cost_increase_pct,
                "quality_gain_points": quality_gain,
                "ratio": f"{cost_increase_pct}% cost increase per {quality_gain} quality point",
            })

            cost_vs_lead_time.append({
                "from_rank": a["rank"],
                "to_rank": b["rank"],
                "cost_increase_pct": cost_increase_pct,
                "lead_time_change_days": lead_time_change,
                "ratio": f"{cost_increase_pct}% cost increase for {lead_time_change} day lead-time change",
            })

        return {
            "cost_vs_quality": cost_vs_quality,
            "cost_vs_lead_time": cost_vs_lead_time,
        }

    def export_to_json(self, pareto_result: Dict, filepath: str) -> bool:
        """結果を JSON で保存

        Args:
            pareto_result: compute_pareto_front() / compute_tradeoff_ratios() 等の戻り値
                （dict または list を想定）
            filepath: 出力ファイルパス

        Returns:
            bool: 成功時 True、失敗時 False
        """
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(pareto_result, f, indent=2, ensure_ascii=False)

            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                print(f"✅ Pareto front exported to {filepath}")
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
