"""
Merit Order Analyzer
====================

サプライヤーのコスト階層を分析し、需要レベル別の最適調達配分を計算

使用例:
    >>> analyzer = MeritOrderAnalyzer(suppliers_data)
    >>> result = analyzer.calculate_merit_order(demand_week)
    >>> analyzer.export_to_json(result, "merit_order_output.json")
"""

import json
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class SupplierInfo:
    """サプライヤー情報"""
    supplier_id: str
    supplier_name: str
    unit_cost: float  # 元の通貨単位
    max_supply: int
    lead_time_days: int
    quality_score: float  # 0-100
    currency: str = "USD"
    exchange_rate: float = 1.0


class MeritOrderAnalyzer:
    """
    サプライヤーのコスト階層を分析し、需要別の最適配分を計算するアナライザー
    
    メインメソッド:
    - validate_suppliers(): サプライヤーデータの検証
    - calculate_merit_order(): Merit Order曲線を計算
    - allocate_demand(): 需要配分を計算
    - export_to_json(): 結果をJSON出力
    """
    
    def __init__(self, suppliers: List[Dict]):
        """
        初期化
        
        Args:
            suppliers: サプライヤーマスターデータ
                [
                    {
                        "supplier_id": "SUP_001",
                        "supplier_name": "Samsung",
                        "unit_cost": 50,
                        "max_supply": 10000,
                        "lead_time_days": 14,
                        "quality_score": 95,
                        "currency": "KRW",
                        "exchange_rate": 0.00075,
                    },
                    ...
                ]
        """
        self.suppliers = suppliers
        self.validated = False
        self.validation_errors = []
        
    def validate_suppliers(self) -> bool:
        """
        サプライヤーデータの整合性をチェック
        
        Returns:
            bool: 全サプライヤーが有効な場合 True
        """
        self.validation_errors = []
        
        if not self.suppliers:
            self.validation_errors.append("No suppliers provided")
            return False
        
        supplier_ids = set()
        for idx, supplier in enumerate(self.suppliers):
            # 必須フィールドチェック
            required_fields = ["supplier_id", "unit_cost", "max_supply", 
                             "lead_time_days", "quality_score"]
            for field in required_fields:
                if field not in supplier:
                    self.validation_errors.append(
                        f"Supplier #{idx}: Missing field '{field}'"
                    )
            
            # 値の範囲チェック
            if supplier.get("unit_cost", 0) <= 0:
                self.validation_errors.append(
                    f"Supplier #{idx} ({supplier.get('supplier_id')}): "
                    f"unit_cost must be > 0"
                )
            
            if supplier.get("max_supply", 0) <= 0:
                self.validation_errors.append(
                    f"Supplier #{idx} ({supplier.get('supplier_id')}): "
                    f"max_supply must be > 0"
                )
            
            if not (0 <= supplier.get("quality_score", 0) <= 100):
                self.validation_errors.append(
                    f"Supplier #{idx} ({supplier.get('supplier_id')}): "
                    f"quality_score must be between 0 and 100"
                )
            
            if supplier.get("lead_time_days", 0) < 0:
                self.validation_errors.append(
                    f"Supplier #{idx} ({supplier.get('supplier_id')}): "
                    f"lead_time_days must be >= 0"
                )
            
            # supplier_id の一意性チェック
            supplier_id = supplier.get("supplier_id")
            if supplier_id in supplier_ids:
                self.validation_errors.append(
                    f"Duplicate supplier_id: {supplier_id}"
                )
            supplier_ids.add(supplier_id)
        
        self.validated = len(self.validation_errors) == 0
        return self.validated
    
    def calculate_merit_order(self, 
                            demand: Dict,
                            constraints: Optional[Dict] = None) -> Dict:
        """
        Merit Order曲線を計算し、需要配分を算出
        
        Args:
            demand: {
                "week": "2026-W36",
                "required_qty": 5000,
                "min_quality_acceptable": 85,
                "max_lead_time_acceptable": 21,
            }
            constraints: {
                "prefer_suppliers": ["SUP_001", "SUP_002"],
                "single_source_max": 0.7,  # 1社から最大70%
                "exclude_suppliers": ["SUP_999"],
            }
        
        Returns:
            merit_order_output: {
                "week": "2026-W36",
                "required_qty": 5000,
                "merit_order": [...],
                "recommended_allocation": [...],
                "total_cost": int,
                "average_quality": float,
                "average_lead_time": float,
                "fulfillment_rate": float,
            }
        """
        if not self.validated:
            self.validate_suppliers()
        
        if not self.validated:
            raise ValueError(
                f"Invalid supplier data: {'; '.join(self.validation_errors)}"
            )
        
        constraints = constraints or {}
        min_quality = demand.get("min_quality_acceptable", 0)
        max_lead_time = demand.get("max_lead_time_acceptable", float('inf'))
        required_qty = demand.get("required_qty", 0)
        
        # Step 1: 制約に基づいてフィルタリング
        filtered_suppliers = self._filter_suppliers(
            self.suppliers, min_quality, max_lead_time, constraints
        )
        
        if not filtered_suppliers:
            raise ValueError(
                f"No suppliers meet the constraints: "
                f"min_quality={min_quality}, max_lead_time={max_lead_time}"
            )
        
        # Step 2: 為替レート適用後、コスト順にソート
        suppliers_with_cost = self._calculate_cost_in_usd(filtered_suppliers)
        sorted_suppliers = sorted(
            suppliers_with_cost, key=lambda x: x["unit_cost_usd"]
        )
        
        # Step 3: Merit Order構造を生成
        merit_order = self._build_merit_order(sorted_suppliers)
        
        # Step 4: 需要を配分
        allocation = self.allocate_demand(
            sorted_suppliers, required_qty, constraints
        )
        
        # Step 5: 結果をまとめる
        result = {
            "analysis_timestamp": datetime.now().isoformat(),
            "week": demand.get("week"),
            "required_qty": required_qty,
            "merit_order": merit_order,
            "recommended_allocation": allocation["allocation"],
            "total_cost": allocation["total_cost"],
            "fulfillment_qty": allocation["fulfillment_qty"],
            "fulfillment_rate": (allocation["fulfillment_qty"] / required_qty 
                                if required_qty > 0 else 0.0),
            "average_quality": allocation["average_quality"],
            "average_lead_time": allocation["average_lead_time"],
        }
        
        return result
    
    def allocate_demand(self, 
                       sorted_suppliers: List[Dict],
                       required_qty: int,
                       constraints: Optional[Dict] = None) -> Dict:
        """
        需要をサプライヤーに配分（最小コスト戦略）
        
        Args:
            sorted_suppliers: コスト順にソートされたサプライヤーリスト
            required_qty: 必要数量
            constraints: {
                "single_source_max": 0.7,  # 1社からは最大70%まで
            }
        
        Returns:
            {
                "allocation": [...],
                "total_cost": int,
                "fulfillment_qty": int,
                "average_quality": float,
                "average_lead_time": float,
            }
        """
        constraints = constraints or {}
        single_source_max = constraints.get("single_source_max", 1.0)
        
        allocation = []
        remaining_qty = required_qty
        total_cost = 0
        total_quality = 0
        total_lead_time = 0
        total_allocated_qty = 0
        
        # 各サプライヤーから順に割り当て
        for supplier in sorted_suppliers:
            if remaining_qty <= 0:
                break
            
            # このサプライヤーから割り当てる数量
            max_from_this = min(
                supplier["max_supply"],
                int(required_qty * single_source_max),
                remaining_qty
            )
            
            if max_from_this > 0:
                allocation.append({
                    "supplier_id": supplier["supplier_id"],
                    "supplier_name": supplier.get("supplier_name", ""),
                    "allocated_qty": max_from_this,
                    "unit_cost_usd": supplier["unit_cost_usd"],
                    "subtotal_usd": max_from_this * supplier["unit_cost_usd"],
                    "quality_score": supplier["quality_score"],
                    "lead_time_days": supplier["lead_time_days"],
                })
                
                total_cost += max_from_this * supplier["unit_cost_usd"]
                total_quality += max_from_this * supplier["quality_score"]
                total_lead_time += max_from_this * supplier["lead_time_days"]
                total_allocated_qty += max_from_this
                remaining_qty -= max_from_this
        
        avg_quality = (total_quality / total_allocated_qty 
                      if total_allocated_qty > 0 else 0.0)
        avg_lead_time = (total_lead_time / total_allocated_qty 
                        if total_allocated_qty > 0 else 0.0)
        
        return {
            "allocation": allocation,
            "total_cost": total_cost,
            "fulfillment_qty": total_allocated_qty,
            "average_quality": round(avg_quality, 2),
            "average_lead_time": round(avg_lead_time, 2),
        }
    
    def _filter_suppliers(self, suppliers: List[Dict], 
                         min_quality: float, 
                         max_lead_time: int,
                         constraints: Dict) -> List[Dict]:
        """品質・リードタイム制約に基づいてサプライヤーをフィルタリング"""
        excluded = constraints.get("exclude_suppliers", [])
        filtered = []
        
        for supplier in suppliers:
            if supplier["supplier_id"] in excluded:
                continue
            if supplier["quality_score"] < min_quality:
                continue
            if supplier["lead_time_days"] > max_lead_time:
                continue
            filtered.append(supplier)
        
        return filtered
    
    def _calculate_cost_in_usd(self, suppliers: List[Dict]) -> List[Dict]:
        """為替レートを適用してUSD建てのコストを計算"""
        result = []
        for supplier in suppliers:
            supplier_copy = supplier.copy()
            exchange_rate = supplier.get("exchange_rate", 1.0)
            supplier_copy["unit_cost_usd"] = (
                supplier["unit_cost"] * exchange_rate
            )
            result.append(supplier_copy)
        return result
    
    def _build_merit_order(self, sorted_suppliers: List[Dict]) -> List[Dict]:
        """Merit Order構造を生成"""
        merit_order = []
        cumulative_supply = 0
        
        for rank, supplier in enumerate(sorted_suppliers, 1):
            cumulative_supply += supplier["max_supply"]
            merit_order.append({
                "rank": rank,
                "supplier_id": supplier["supplier_id"],
                "supplier_name": supplier.get("supplier_name", ""),
                "unit_cost_usd": supplier["unit_cost_usd"],
                "max_supply": supplier["max_supply"],
                "cumulative_supply_from_rank_1": cumulative_supply,
                "quality_score": supplier["quality_score"],
                "lead_time_days": supplier["lead_time_days"],
            })
        
        return merit_order
    
    def export_to_json(self, result: Dict, filepath: str):
        """結果をJSON形式で保存"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"✅ Results exported to {filepath}")


# ヘルパー関数
def load_suppliers_from_csv(filepath: str) -> List[Dict]:
    """
    CSVからサプライヤーデータを読み込み
    
    期待されるカラム:
    supplier_id, supplier_name, unit_cost, max_supply, 
    lead_time_days, quality_score, currency, exchange_rate
    """
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("pandas is required. Install with: pip install pandas")
    
    df = pd.read_csv(filepath)
    return df.to_dict(orient='records')


if __name__ == "__main__":
    # 簡易テスト
    sample_suppliers = [
        {
            "supplier_id": "SUP_001",
            "supplier_name": "Samsung",
            "unit_cost": 50,
            "max_supply": 5000,
            "lead_time_days": 14,
            "quality_score": 95,
            "exchange_rate": 1.0,
        },
        {
            "supplier_id": "SUP_002",
            "supplier_name": "TSMC",
            "unit_cost": 48,
            "max_supply": 3000,
            "lead_time_days": 21,
            "quality_score": 94,
            "exchange_rate": 1.0,
        },
    ]
    
    analyzer = MeritOrderAnalyzer(sample_suppliers)
    is_valid = analyzer.validate_suppliers()
    print(f"Supplier validation: {'✅ PASS' if is_valid else '❌ FAIL'}")
    
    if is_valid:
        result = analyzer.calculate_merit_order({
            "week": "2026-W36",
            "required_qty": 5000,
            "min_quality_acceptable": 85,
        })
        print(f"\nMerit Order Analysis for week {result['week']}:")
        print(f"  Required: {result['required_qty']} units")
        print(f"  Fulfillment: {result['fulfillment_qty']} units ({result['fulfillment_rate']*100:.1f}%)")
        print(f"  Total Cost: ${result['total_cost']:,}")
        print(f"  Avg Quality: {result['average_quality']:.1f}/100")
        print(f"  Avg Lead Time: {result['average_lead_time']:.1f} days")
