# wom-v1r4m0 Phase 1: Merit Order曲線実装ガイド

## 概要

本ドキュメントは、`wom-v1r4m0`のPhase 1（Weeks 1-2）における**Merit Order曲線実装**の詳細ガイドです。

---

## 1. 実装スコープ

### 1.1 目標

サプライヤーのコスト階層を可視化し、需要レベル別の最適な調達ミックスを提案するモジュールを実装。

### 1.2 入出力仕様

**入力**
```python
# ローカルデータ
suppliers: List[Dict] = [
    {
        "supplier_id": "SUP_001",
        "supplier_name": "Samsung Electronics",
        "unit_cost": 50,                    # 単価（USD）
        "max_supply": 10000,                # 最大供給量/週
        "lead_time_days": 14,
        "quality_score": 95,                # 不良率ベース（0-100）
        "currency": "KRW",
        "exchange_rate": 0.00075,           # to USD
    },
    ...
]

demand_week: Dict = {
    "week": "2026-W36",
    "required_qty": 5000,
    "min_quality_acceptable": 85,
    "max_lead_time_acceptable": 21,
}
```

**出力**
```python
merit_order_output: Dict = {
    "week": "2026-W36",
    "required_qty": 5000,
    "merit_order": [
        {
            "rank": 1,
            "supplier_id": "SUP_001",
            "supplier_name": "Samsung Electronics",
            "unit_cost_usd": 50,
            "cumulative_supply": (0, 10000),
            "quality_score": 95,
            "lead_time_days": 14,
            "fulfillment_pct": 1.0,  # 100% fulfillment
        },
        ...
    ],
    "recommended_allocation": [
        {
            "rank": 1,
            "supplier_id": "SUP_001",
            "allocated_qty": 5000,
            "unit_cost": 50,
            "subtotal": 250000,
        },
    ],
    "total_cost": 250000,
    "average_quality": 95,
    "average_lead_time": 14,
    "fulfillment_rate": 1.0,  # 100% demand満足度
}
```

---

## 2. モジュール設計

### 2.1 ファイル構造

```
wom/
└── visualization/
    ├── __init__.py
    └── merit_order.py           # メインモジュール
        ├── MeritOrderAnalyzer   # クラス
        ├── calculate_cost       # ヘルパー関数
        └── allocate_demand      # ヘルパー関数

tests/
└── test_merit_order.py           # ユニットテスト
```

### 2.2 クラス設計（merit_order.py）

```python
class MeritOrderAnalyzer:
    """サプライヤーのコスト階層を分析し、需要配分を最適化"""
    
    def __init__(self, suppliers: List[Dict]):
        """
        Args:
            suppliers: サプライヤーのマスターデータ
        """
        self.suppliers = suppliers
        self.validated = False
        
    def validate_suppliers(self) -> bool:
        """サプライヤーデータの整合性チェック"""
        # - unit_cost > 0 か？
        # - max_supply > 0 か？
        # - quality_score は 0-100 の範囲か？
        # - lead_time_days >= 0 か？
        # - supplier_id は一意か？
        pass
    
    def calculate_merit_order(self, 
                            demand: Dict,
                            constraints: Dict = None) -> Dict:
        """
        Merit Order曲線を計算
        
        Args:
            demand: {
                "week": "2026-W36",
                "required_qty": 5000,
                "min_quality_acceptable": 85,
                "max_lead_time_acceptable": 21,
            }
            constraints: {
                "prefer_suppliers": ["SUP_001", "SUP_002"],  # 優先サプライヤー
                "single_source_max": 0.7,  # 1社からの調達は70%まで
            }
        
        Returns:
            merit_order_output (as above)
        """
        # 1. サプライヤーをコスト順にソート
        # 2. 制約を適用（品質、リードタイム）
        # 3. 需要を満たすアロケーション計算
        pass
    
    def allocate_demand(self, 
                       sorted_suppliers: List[Dict],
                       required_qty: int,
                       constraints: Dict = None) -> Dict:
        """
        需要をサプライヤーに配分
        
        戦略：
        - 最も安いサプライヤーから順に割り当て
        - ただし単一サプライヤーの依存度を制限
        - リード時間の満足度も考慮
        
        Returns:
            {
                "recommended_allocation": [...],
                "total_cost": int,
                "fulfillment_rate": float,
            }
        """
        pass
    
    def calculate_cost_with_exchange_rates(self, 
                                          supplier: Dict) -> float:
        """
        為替レートを考慮した実コストを計算
        
        実コスト = unit_cost * exchange_rate
        """
        cost = supplier["unit_cost"]
        exchange_rate = supplier.get("exchange_rate", 1.0)
        return cost * exchange_rate
    
    def export_to_json(self, result: Dict, filepath: str):
        """結果をJSON形式で保存"""
        pass
```

---

## 3. 実装手順（Week-by-Week）

### Week 1

#### Day 1-2: 基本骨組み
- [ ] `wom/visualization/__init__.py` 作成
- [ ] `wom/visualization/merit_order.py` スケルトン
- [ ] クラス定義とメソッドシグネチャ
- [ ] Docstring記述

#### Day 3-4: ユニットテスト設計
- [ ] `tests/test_merit_order.py` 作成
- [ ] テストケース設計（5-10個）
- [ ] テストデータセット準備

#### Day 5: 実装開始
- [ ] `validate_suppliers()` 実装・テスト
- [ ] `calculate_merit_order()` の基本フレーム

### Week 2

#### Day 1-3: コア実装
- [ ] `calculate_merit_order()` 完全実装
- [ ] ソート・フィルタリングロジック
- [ ] 為替レート対応

#### Day 4: アロケーション
- [ ] `allocate_demand()` 実装
- [ ] 制約処理

#### Day 5: テスト・ドキュメント
- [ ] 全テストパス
- [ ] README記述
- [ ] コードコメント

---

## 4. テストケース例

### TC-001: 基本的なMerit Order生成

```python
def test_basic_merit_order():
    suppliers = [
        {"supplier_id": "SUP_A", "unit_cost": 50, "max_supply": 5000, 
         "quality_score": 95, "lead_time_days": 14, "exchange_rate": 1.0},
        {"supplier_id": "SUP_B", "unit_cost": 45, "max_supply": 3000, 
         "quality_score": 88, "lead_time_days": 21, "exchange_rate": 1.0},
    ]
    
    analyzer = MeritOrderAnalyzer(suppliers)
    result = analyzer.calculate_merit_order({
        "week": "2026-W36",
        "required_qty": 5000,
        "min_quality_acceptable": 85,
        "max_lead_time_acceptable": 21,
    })
    
    # 予想：SUP_B（45USD）が最初、次SUP_A（50USD）
    assert result["merit_order"][0]["supplier_id"] == "SUP_B"
    assert result["recommended_allocation"][0]["supplier_id"] == "SUP_B"
    assert result["recommended_allocation"][0]["allocated_qty"] == 3000
    assert result["recommended_allocation"][1]["allocated_qty"] == 2000
```

### TC-002: 品質制約

```python
def test_quality_constraint():
    suppliers = [
        {"supplier_id": "SUP_C", "unit_cost": 30, "max_supply": 10000,
         "quality_score": 75, "lead_time_days": 14, "exchange_rate": 1.0},  # 品質不足
        {"supplier_id": "SUP_D", "unit_cost": 55, "max_supply": 10000,
         "quality_score": 95, "lead_time_days": 14, "exchange_rate": 1.0},
    ]
    
    analyzer = MeritOrderAnalyzer(suppliers)
    result = analyzer.calculate_merit_order({
        "required_qty": 5000,
        "min_quality_acceptable": 85,
    })
    
    # 予想：SUP_C は品質不足なので除外
    assert all(s["supplier_id"] != "SUP_C" for s in result["merit_order"])
    assert result["recommended_allocation"][0]["supplier_id"] == "SUP_D"
```

### TC-003: 為替レート対応

```python
def test_exchange_rate_calculation():
    suppliers = [
        {"supplier_id": "SUP_JP", "unit_cost": 5000, "max_supply": 1000,
         "quality_score": 90, "lead_time_days": 7, "exchange_rate": 0.0067},  # JPY to USD
        {"supplier_id": "SUP_US", "unit_cost": 35, "max_supply": 1000,
         "quality_score": 90, "lead_time_days": 7, "exchange_rate": 1.0},
    ]
    
    analyzer = MeritOrderAnalyzer(suppliers)
    result = analyzer.calculate_merit_order({"required_qty": 1000})
    
    # JPY: 5000 * 0.0067 = 33.5 USD
    # → SUP_JP が最安
    assert result["merit_order"][0]["supplier_id"] == "SUP_JP"
```

---

## 5. データソース・連携

### 5.1 サプライヤーマスターデータの取得元

v1r3m1では、サプライヤー情報が以下の場所に分散：

```
data/sample/*/
├── sku_master.csv
├── node_master.csv（製造拠点）
└── lane_assignment.csv（拠点↔拠点のリードタイム）
```

Merit Order v1では、**CSVから直接読み込む簡易版**を実装：

```python
# merit_order.py
def load_suppliers_from_csv(filepath: str) -> List[Dict]:
    """
    suppliers_master.csv から読み込み
    
    期待されるCSVカラム：
    supplier_id, supplier_name, unit_cost, max_supply,
    lead_time_days, quality_score, currency, exchange_rate
    """
    import pandas as pd
    df = pd.read_csv(filepath)
    return df.to_dict(orient='records')
```

### 5.2 サンプルCSVの作成

テスト用サンプル：

```
# tests/fixtures/suppliers_master.csv
supplier_id,supplier_name,unit_cost,max_supply,lead_time_days,quality_score,currency,exchange_rate
SUP_001,Samsung Electronics,50,10000,14,95,KRW,0.00075
SUP_002,TSMC,48,8000,21,94,TWD,0.031
SUP_003,MediaTek,52,5000,14,92,TWD,0.031
SUP_004,Qualcomm,55,3000,7,96,USD,1.0
```

---

## 6. 開発環境チェックリスト

- [ ] Python 3.9+ インストール済み
- [ ] 必要パッケージ：`pandas`, `numpy`, `pytest`
- [ ] IDE設定：VSCode / PyCharm でWOM プロジェクトを開く
- [ ] Git ブランチ確認：`git branch`で`* wom-v1r4m0` であることを確認
- [ ] テストフレームワーク：`pytest` で実行確認

---

## 7. コミットメッセージテンプレート

```
feat(visualization): Implement Merit Order analyzer for supplier cost hierarchy

- Add MeritOrderAnalyzer class with supplier validation
- Calculate cost-optimized allocation respecting quality & lead time constraints
- Support multi-currency suppliers with exchange rate conversion
- Export results to JSON format
- Add comprehensive unit tests (TC-001 through TC-005)

Fixes: Issue #X
Related: wom-v1r4m0 Phase 1 roadmap
```

---

## 8. よくある落とし穴

### ❌ 落とし穴1: 為替レート計算のタイミング

**間違い**：
```python
# ❌ ソート後に為替計算
sorted_suppliers = sorted(suppliers, key=lambda x: x["unit_cost"])
# unit_cost は元の通貨での値
```

**正解**：
```python
# ✅ ソート前に為替を適用してから計算
suppliers_in_usd = [
    {**s, "unit_cost_usd": s["unit_cost"] * s.get("exchange_rate", 1.0)}
    for s in suppliers
]
sorted_suppliers = sorted(suppliers_in_usd, key=lambda x: x["unit_cost_usd"])
```

### ❌ 落とし穴2: 需要を超過する配分

```python
# ❌ 各サプライヤーの max_supply を合計が required_qty を超えないことを確認していない
for supplier in sorted_suppliers:
    allocated += supplier["max_supply"]
    # → allocated が required_qty を超える可能性

# ✅ 各ステップで remaining_qty をチェック
remaining_qty = required_qty
for supplier in sorted_suppliers:
    to_allocate = min(supplier["max_supply"], remaining_qty)
    allocated.append(to_allocate)
    remaining_qty -= to_allocate
    if remaining_qty == 0:
        break
```

### ❌ 落とし穴3: テストデータの一貫性

```python
# ❌ テストで異なるスキーマ
def test_case_1():
    supplier = {"supplier_id": "SUP_A", "unit_cost": 50}
    # lead_time_days が無い

# ✅ 共有のテストフィクスチャを使用
@pytest.fixture
def sample_suppliers():
    return [
        {
            "supplier_id": "SUP_A",
            "unit_cost": 50,
            "max_supply": 5000,
            "quality_score": 95,
            "lead_time_days": 14,
            "exchange_rate": 1.0,
        },
        ...
    ]
```

---

## 9. 次フェーズへの引き継ぎ

Phase 1完了時点で、以下をPhase 2に引き継ぎ：
- Merit Order クラスの完全なドキュメント
- 全テストケースの成功ログ
- JSON出力スキーマの確定
- Regime Map実装のための基盤データ構造

---

**最終更新**: 2026-09-05  
**対象ブランチ**: `wom-v1r4m0`  
**ステータス**: 実装準備完了
