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
from typing import Dict, List, Optional, Tuple


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

    粒度（Phase 3 / V0 で追加）:
        "record"（既定）: allocations の1要素 = サプライヤー1社分の配分レコード。
                          コンストラクタを直接呼んだ場合の既定・従来通りの挙動。
        "plan"          : allocations の1要素 = calculate_merit_order() の結果1件
                          （＝ required_qty を満たす配分案セット全体）。
                          from_merit_order_results() 経由でのみ設定される。
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
        self._granularity = "record"            # "record" | "plan"
        self._lead_time_metric = "weighted_avg"  # "weighted_avg" | "max"（plan粒度のみ使用）

    @classmethod
    def from_merit_order_results(
        cls,
        results: List[Dict],
        *,
        objectives: Optional[List[str]] = None,
        lead_time_metric: str = "weighted_avg",
    ) -> "ParetoFrontAnalyzer":
        """calculate_merit_order() の結果リストから、
        「1配分案 = 1解」の粒度で Analyzer を構築する。

        Args:
            results: [calculate_merit_order(...), ...]
                     各要素が1つの配分案（同じ required_qty を満たす想定）
            objectives: 目的軸（デフォルト ["cost", "quality", "lead_time"]）
            lead_time_metric: "weighted_avg"（既定） | "max"

        Returns:
            _granularity="plan" が設定された ParetoFrontAnalyzer

        Raises:
            ValueError: lead_time_metric が上記2値以外のとき
        """
        if lead_time_metric not in ("weighted_avg", "max"):
            raise ValueError(
                f"lead_time_metric must be 'weighted_avg' or 'max', "
                f"got {lead_time_metric!r}"
            )
        obj = cls(results, objectives)
        obj._granularity = "plan"
        obj._lead_time_metric = lead_time_metric
        return obj

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

    def compute_plan_objectives(self, result: Dict) -> Dict:
        """merit_order result 1件 → 目的関数値

        Args:
            result: calculate_merit_order() の戻り値

        Returns:
            {"cost": float, "quality": float, "lead_time": float}

        Note:
            cost / quality / lead_time は merit_order result が既に
            算出済みの total_cost / average_quality / average_lead_time を
            そのまま使う（再計算しない）。
            lead_time_metric="max" のときのみ recommended_allocation から
            lead_time_days の最大値を採る。
        """
        cost = result.get("total_cost", 0)
        quality = result.get("average_quality", 0)

        if self._lead_time_metric == "max":
            alloc = result.get("recommended_allocation") or []
            lead_time = max((a.get("lead_time_days", 0) for a in alloc), default=0)
        else:
            lead_time = result.get("average_lead_time", 0)

        return {"cost": cost, "quality": quality, "lead_time": lead_time}

    def _objective_fn(self):
        """現在の粒度に応じた目的関数を返す（内部ヘルパー）"""
        if self._granularity == "plan":
            return self.compute_plan_objectives
        return self.compute_allocation_objectives

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

    @classmethod
    def _rank_front(cls, objs: List[Dict]) -> Tuple[List[int], Dict[int, int], List[int]]:
        """目的関数値のリストから Pareto Front を判定する（内部共通ロジック）。

        `compute_pareto_front()` と `compute_all_solutions()` の両方から使われる。

        Args:
            objs: [{"cost":..., "quality":..., "lead_time":...}, ...]

        Returns:
            (front_indices, rank_by_index, dominated_count_by_index)
            front_indices: Pareto Front 上にある解の入力インデックス（cost 昇順）
            rank_by_index: front_indices の各要素 -> 1始まりのランク
            dominated_count_by_index: 各インデックスが支配している他解の数
        """
        n = len(objs)
        dominated_by_someone = [False] * n
        dominated_count = [0] * n

        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                if cls._dominates(objs[j], objs[i]):
                    dominated_by_someone[i] = True
                if cls._dominates(objs[i], objs[j]):
                    dominated_count[i] += 1

        front_indices = sorted(
            (i for i in range(n) if not dominated_by_someone[i]),
            key=lambda i: objs[i]["cost"],
        )
        rank_by_index = {idx: rank for rank, idx in enumerate(front_indices, start=1)}

        return front_indices, rank_by_index, dominated_count

    def compute_pareto_front(self) -> List[Dict]:
        """Pareto Front を計算（O(n²) 素朴実装、n=配分案数は通常10-100程度想定）

        `_granularity` が "plan" のときは `compute_plan_objectives()`、
        "record"（既定）のときは `compute_allocation_objectives()` を使う。

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
        objective_fn = self._objective_fn()
        objs = [objective_fn(a) for a in self.allocations]

        front_indices, rank_by_index, dominated_count = self._rank_front(objs)

        front = []
        for idx in front_indices:
            front.append({
                "rank": rank_by_index[idx],
                "cost": objs[idx]["cost"],
                "quality": objs[idx]["quality"],
                "lead_time": objs[idx]["lead_time"],
                "allocation": self.allocations[idx],
                "dominated_count": dominated_count[idx],
            })

        return front

    def compute_all_solutions(self) -> List[Dict]:
        """全解（支配されているものも含む）を目的関数値つきで返す。

        散布図・平行座標は「支配されている解」も淡色で描く必要があるため、
        全解を目的関数値つきで取れるようにするメソッド。

        Returns:
            [
                {
                    "cost": float,
                    "quality": float,
                    "lead_time": float,
                    "allocation": {...},
                    "on_front": bool,      # Pareto Front 上か
                    "rank": int | None,    # Front 上のときのみ採番、他は None
                },
                ...
            ]
            （入力順。ソートしない）
        """
        objective_fn = self._objective_fn()
        objs = [objective_fn(a) for a in self.allocations]

        front_indices, rank_by_index, _dominated_count = self._rank_front(objs)
        front_set = set(front_indices)

        result = []
        for i, (obj, alloc) in enumerate(zip(objs, self.allocations)):
            result.append({
                "cost": obj["cost"],
                "quality": obj["quality"],
                "lead_time": obj["lead_time"],
                "allocation": alloc,
                "on_front": i in front_set,
                "rank": rank_by_index.get(i),
            })

        return result

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
