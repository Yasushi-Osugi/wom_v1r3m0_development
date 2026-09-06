# Phase 2 実装設計 - Regime Map & Pareto Front
**作成日**: 2026年9月6日  
**設計責任**: Claude君  
**対象**: 大杉さんレビュー → Code君実装  
**優先度**: MEDIUM（2-4週間）

---

## 1. Phase 2 全体像

### 1.1 目的

Merit Order（Phase 1）の単一週の最適調達分析から、**市場状況に応じた戦略的な調達方針選択** へ拡張する。

### 1.2 構成

```
Merit Order 分析（Phase 1）
    ↓ 週次 PSI データ
    ↓ 平均品質・リードタイム等の指標
    ↓
Regime Map 分析（Phase 2-A）
    ├── 需要レベル分類（Low/Medium/High）
    ├── 供給タイト度分類（Tight/Balanced/Surplus）
    └── 3×3 マトリクス → 推奨戦略マッピング
    ↓
Pareto Front 分析（Phase 2-B）
    ├── 複数目標最適化（Cost, Quality, Lead Time）
    ├── Pareto 最適解の列挙
    └── トレードオフ可視化
```

### 1.3 Week 単位の納期

| Week | Task | Deliverable |
|------|------|-------------|
| **Week 3** | Regime Map 設計・実装・テスト | `RegimeMapAnalyzer` クラス + 12 テスト |
| **Week 4** | Pareto Front 設計・実装・テスト | `ParetoFrontAnalyzer` クラス + 8 テスト |
| **Week 5** | 統合テスト・ドキュメント・チューニング | E2E テスト + 仕様書 |

---

## 2. Regime Map 分析（Phase 2-A）

### 2.1 背景・設計思想

#### 現状の課題

Merit Order は **「今週、この需要を満たすのに最適な調達は？」** という静的な質問に答える。

しかし実運用では、**「この市場環境では、どの調達戦略を採るべき？」** という動的な判断が必要：
- 需要が高い → 早めに確保（Safety Stock）
- 供給がタイトな週 → 複数ソース化
- 供給に余裕 → コスト最適化重視

#### Regime Map の定義

**3×3 マトリクス** で市場状況を分類：

```
                Demand Level
                Low    Med   High
Supply      ┌─────────────────────┐
Tight       │ (1,1)│ (1,2)│ (1,3)│  Risk Mode
Tightness ├─────────────────────┤
Balanced    │ (2,1)│ (2,2)│ (2,3)│  Balanced
            ├─────────────────────┤
Surplus     │ (3,1)│ (3,2)│ (3,3)│  Opportunity
            └─────────────────────┘
```

各セルに対応する **推奨戦略** をマッピング。

### 2.2 指標の定義

#### 2.2.1 需要レベル（Demand Level）

Merit Order の **fulfillment_rate** を使用：

```python
def classify_demand_level(fulfillment_rate: float) -> str:
    """
    fulfillment_rate に基づいて需要レベルを分類
    
    Args:
        fulfillment_rate: Merit Order 結果の fulfillment_rate (0.0 - 1.0)
    
    Returns:
        "Low"      : fulfillment_rate >= 0.95  (95%以上充足 = 供給過多)
        "Medium"   : 0.75 <= fulfillment_rate < 0.95
        "High"     : fulfillment_rate < 0.75   (75%未満 = 需要が供給を超過)
    """
```

**論理的根拠**:
- fulfillment_rate = 充足率 = (実配分量 / 必要量)
- 高い = 供給に余裕 → 需要レベル「Low」
- 低い = 供給不足 → 需要レベル「High」

#### 2.2.2 供給タイト度（Supply Tightness）

Merit Order の **average_lead_time** を使用：

```python
def classify_supply_tightness(average_lead_time: float, 
                              demand_lead_time_threshold: float = 14) -> str:
    """
    平均リードタイムに基づいて供給タイト度を分類
    
    Args:
        average_lead_time: Merit Order 結果の average_lead_time (days)
        demand_lead_time_threshold: 需要計画のリードタイム閾値（デフォルト 14日）
    
    Returns:
        "Tight"    : average_lead_time > threshold + 7   (21日以上 = リードタイム長い)
        "Balanced" : threshold - 7 <= average_lead_time <= threshold + 7  (7-21日)
        "Surplus"  : average_lead_time < threshold - 7   (7日未満 = 短い)
    """
```

**論理的根拠**:
- average_lead_time が長い = サプライヤーの対応時間が長い = 供給タイト
- リードタイム短い = 需要に素早く対応可能 = 供給余裕

### 2.3 推奨戦略マッピング

各セル (i, j) に対応する戦略を定義：

```python
REGIME_STRATEGIES = {
    # (supply_tightness, demand_level) : strategy
    
    # Supply Tight 行
    ("Tight", "Low"):      "cost_lock",        # 供給タイト・需要低 → コスト確保優先
    ("Tight", "Medium"):   "dual_source",      # 供給タイト・需要中 → 複数ソース化
    ("Tight", "High"):     "safety_stock",     # 供給タイト・需要高 → 安全在庫確保
    
    # Supply Balanced 行
    ("Balanced", "Low"):   "lean",             # バランス・需要低 → リーン運用
    ("Balanced", "Medium"): "merit_order",     # バランス・需要中 → Merit Order そのまま
    ("Balanced", "High"):  "lead_time_hedge",  # バランス・需要高 → リードタイムヘッジ
    
    # Supply Surplus 行
    ("Surplus", "Low"):    "consolidation",    # 供給余裕・需要低 → サプライヤー統廃
    ("Surplus", "Medium"): "price_negotiation",# 供給余裕・需要中 → 価格交渉
    ("Surplus", "High"):   "quality_upgrade",  # 供給余裕・需要高 → 品質重視
}
```

**各戦略の概要**:

| Strategy | 特徴 | 実装アクション |
|----------|------|-------------|
| `cost_lock` | コスト確保 | Unit cost が安いサプライヤーを固定契約 |
| `dual_source` | 複数ソース化 | Top 2 サプライヤーで供給分散 |
| `safety_stock` | 安全在庫 | Average lead time × 1.5 週分を確保 |
| `lean` | リーン運用 | Just-in-time 配分、在庫最小化 |
| `merit_order` | 最適配分 | Phase 1 Merit Order 既定方針 |
| `lead_time_hedge` | リードタイムヘッジ | Lead time variation に対応する在庫 |
| `consolidation` | サプライヤー統廃 | サプライヤー数削減 |
| `price_negotiation` | 価格交渉 | 複数サプライヤー競争環境を活用 |
| `quality_upgrade` | 品質重視 | Quality score >= 95 のみ使用 |

### 2.4 API 設計

#### 2.4.1 RegimeMapAnalyzer クラス

```python
class RegimeMapAnalyzer:
    """
    Merit Order 結果から Regime Map を分類し、推奨戦略を提示
    
    入力: Merit Order 分析結果（複数週の結果リスト）
    出力: 市場環境分類 + 推奨戦略 + KPI
    """
    
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
    
    def classify_horizon(self, horizon_weeks: int = 12) -> Dict:
        """複数週の Regime 遷移を分析
        
        Returns:
            {
                "summary": {
                    "dominant_regime": "Balanced/Medium",
                    "risk_weeks": [3, 7, 11],
                    "opportunity_weeks": [2, 5],
                },
                "week_by_week": [
                    {...},  # each week's regime
                ],
                "transition_matrix": {
                    # (week i → week i+1) の遷移確率
                },
            }
        """
    
    def get_strategy_actions(self, regime_dict: Dict) -> Dict:
        """Regime に対応する具体的なアクション提示
        
        Returns:
            {
                "strategy": "safety_stock",
                "actions": [
                    {
                        "action": "increase_safety_stock",
                        "parameter": 3,  # 週数
                        "impact": "Fulfillment rate +5%",
                    },
                    ...
                ],
                "kpi_targets": {
                    "max_lead_time_days": 21,
                    "min_quality_score": 90,
                    "cost_tolerance_pct": 5,
                },
            }
        """
    
    def export_to_json(self, regime_analysis: Dict, filepath: str) -> bool:
        """結果を JSON で保存"""
```

#### 2.4.2 使用例

```python
# Merit Order 結果が複数週ある想定
merit_order_results = [
    analyzer.calculate_merit_order({
        "week": f"2026-W{w:02d}",
        "required_qty": 5000 + random.randint(-500, 500),
    })
    for w in range(36, 48)
]

# Regime Map 分析
regime_analyzer = RegimeMapAnalyzer(merit_order_results)

# 各週の分類
for mo_result in merit_order_results:
    regime = regime_analyzer.classify_single_week(mo_result)
    print(f"{regime['week']}: {regime['demand_level']}/{regime['supply_tightness']} "
          f"→ {regime['recommended_strategy']}")

# 全体の Horizon 分析
horizon = regime_analyzer.classify_horizon(horizon_weeks=12)
print(f"最も高リスク: W{horizon['summary']['risk_weeks']}")
```

### 2.5 テスト計画

#### 2.5.1 テストケース（計 12 個）

**分類テスト（6 個）**:
1. `test_classify_demand_level_low` - fulfillment_rate = 0.98 → "Low"
2. `test_classify_demand_level_medium` - fulfillment_rate = 0.85 → "Medium"
3. `test_classify_demand_level_high` - fulfillment_rate = 0.60 → "High"
4. `test_classify_supply_tightness_tight` - average_lead_time = 25 → "Tight"
5. `test_classify_supply_tightness_balanced` - average_lead_time = 14 → "Balanced"
6. `test_classify_supply_tightness_surplus` - average_lead_time = 5 → "Surplus"

**マッピングテスト（3 個）**:
7. `test_regime_cell_mapping` - 9 セル全て正しくマッピング
8. `test_recommended_strategy_lookup` - 全 9 戦略が正しく取得される
9. `test_strategy_actions_consistency` - 推奨戦略とアクション内容に矛盾がない

**統合テスト（3 個）**:
10. `test_classify_single_week` - Merit Order 結果から regime 出力まで一連処理
11. `test_classify_horizon_12weeks` - 12 週のホライズン分析
12. `test_export_to_json_regime` - JSON エクスポート・フォーマット確認

---

## 3. Pareto Front 分析（Phase 2-B）

### 3.1 背景

Merit Order は **コスト最小化** のみを目的とする単一軸最適化。

しかし実運用では、複数の目的（Cost, Quality, Lead Time）のトレードオフを理解する必要がある。

**例**:
- 最安ソース: Unit cost $10, Quality 80/100, Lead Time 28 days
- 高品質ソース: Unit cost $15, Quality 98/100, Lead Time 7 days
- どちらを選ぶ？ → **Pareto Front** で両立可能なポイントを可視化

### 3.2 Pareto 最適性の定義

```
解 A が解 B より「Pareto 支配」される
 ⟺ すべての目的軸で A が B 以上に悪く、少なくとも1軸で厳密に悪い

Pareto Front = 支配されない解の集合
```

**具体例**:
```
Cost = $10, Quality = 80, Lead Time = 28  ← 品質が悪い
Cost = $15, Quality = 98, Lead Time = 7   ← 支配関係なし（Cost は高いが Quality/LT が良い）
Cost = $12, Quality = 90, Lead Time = 14  ← 両者に支配されない可能性あり

→ これら 3 つが Pareto Front 上にある可能性がある
```

### 3.3 API 設計

#### 3.3.1 ParetoFrontAnalyzer クラス

```python
class ParetoFrontAnalyzer:
    """
    複数目標（Cost, Quality, Lead Time）の Pareto 最適解を計算
    
    入力: Merit Order の recommended_allocation（複数サプライヤー案）
    出力: Pareto Front 上の解の集合 + トレードオフ分析
    """
    
    def __init__(self, allocations: List[Dict],
                 objectives: List[str] = None):
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
    
    def compute_allocation_objectives(self, allocation: Dict) -> Dict:
        """1 配分案の目的関数値を計算
        
        Args:
            allocation: サプライヤー配分 1 件
        
        Returns:
            {
                "cost": 20000,              # Total cost (USD)
                "quality": 92.5,            # Weighted average quality (0-100)
                "lead_time": 16.2,          # Weighted average lead time (days)
            }
        """
    
    def compute_pareto_front(self) -> List[Dict]:
        """Pareto Front を計算
        
        Returns:
            [
                {
                    "rank": 1,
                    "cost": 20000,
                    "quality": 92.5,
                    "lead_time": 16.2,
                    "allocation": {...},  # 対応する配分
                    "dominated_count": 0,  # 支配している解の数
                },
                ...
            ]
            （cost 昇順ソート）
        """
    
    def compute_tradeoff_ratios(self, front: List[Dict]) -> Dict:
        """Pareto Front 上でのトレードオフ比率を計算
        
        Returns:
            {
                "cost_vs_quality": [
                    {
                        "from_rank": 1,
                        "to_rank": 2,
                        "cost_increase_pct": 5.0,
                        "quality_gain_points": 3.2,
                        "ratio": "5% cost increase per 1 quality point",
                    },
                    ...
                ],
                "cost_vs_lead_time": [...],
            }
        """
    
    def export_to_json(self, pareto_result: Dict, filepath: str) -> bool:
        """結果を JSON で保存"""
```

#### 3.3.2 使用例

```python
# Merit Order で複数の配分案を生成（異なる制約条件）
scenarios = [
    {"preferred_suppliers": ["SUP_001"], "single_source_max": 0.9},
    {"quality_threshold": 95},
    {"lead_time_max": 14},
]

allocations_list = []
for scenario in scenarios:
    result = analyzer.calculate_merit_order(
        {"required_qty": 5000},
        constraints=scenario
    )
    allocations_list.extend(result["recommended_allocation"])

# Pareto Front 計算
pareto_analyzer = ParetoFrontAnalyzer(allocations_list)
front = pareto_analyzer.compute_pareto_front()

# トレードオフ分析
tradeoffs = pareto_analyzer.compute_tradeoff_ratios(front)

print(f"Pareto Front 上の解: {len(front)} 個")
for solution in front:
    print(f"  Cost: ${solution['cost']}, Quality: {solution['quality']}, LT: {solution['lead_time']} days")
```

### 3.4 テスト計画

#### 3.4.1 テストケース（計 8 個）

**目的関数テスト（2 個）**:
1. `test_compute_allocation_objectives_simple` - 単一サプライヤー配分
2. `test_compute_allocation_objectives_mixed` - 複数サプライヤー混合配分

**Pareto Front テスト（3 個）**:
3. `test_pareto_front_single_solution` - 配分案が 1 つのみ
4. `test_pareto_front_all_on_front` - 全案が Pareto Front 上
5. `test_pareto_front_dominated_solutions` - 支配関係がある配分

**トレードオフテスト（2 個）**:
6. `test_tradeoff_ratios_cost_vs_quality`
7. `test_tradeoff_ratios_cost_vs_lead_time`

**統合テスト（1 個）**:
8. `test_pareto_export_to_json`

---

## 4. 実装スケジュール

| Timeline | Task | Owner | Deliverable |
|----------|------|-------|-------------|
| **Week 3-1** | Regime Map 実装・テスト | Code君 | `RegimeMapAnalyzer` + 12 テスト |
| **Week 3-2** | 大杉さんレビュー | 大杉さん | レビュー・フィードバック |
| **Week 4-1** | Pareto Front 実装・テスト | Code君 | `ParetoFrontAnalyzer` + 8 テスト |
| **Week 4-2** | 大杉さんレビュー | 大杉さん | レビュー・フィードバック |
| **Week 5** | 統合テスト・ドキュメント | 両者 | E2E テスト + 日本語仕様書 |

---

## 5. 技術的な検討事項

### 5.1 Pareto Front 計算のアルゴリズム

現在計画している方法: **O(n²) 素朴実装**

```python
def is_dominated(sol_a, sol_b):
    # sol_a が sol_b に支配されるか判定
    # 全軸で sol_b >= sol_a かつ，少なくとも 1 軸で sol_b > sol_a
```

**理由**: 配分案数は通常 10-100 程度（n は小さい）

**将来の最適化**: 大規模データセット（n > 1000）の場合は、Kung-Luccio アルゴリズムへの移行を検討

### 5.2 目的関数の正規化

Cost, Quality, Lead Time の単位が異なるため、スケーリングが必要な場合がある。

現在はそのまま（単位をユーザーが理解しやすく）、フェーズ 3 で必要に応じて正規化層を追加。

### 5.3 CLAUDE.md への記録

Phase 2 実装完了後、CLAUDE.md に以下を追記：
```
## v1r4m1: Regime Map + Pareto Front 分析（完了、2026-09-XX）
### 新ファイル
- wom/visualization/regime_map.py
- wom/visualization/pareto_front.py

### テスト
- tests/test_regime_map.py（12 テスト）
- tests/test_pareto_front.py（8 テスト）

### 統合
- Merit Order 結果を Regime Map 入力に
- Regime Map から Pareto Front への流れ
```

---

## 6. 成功基準

Phase 2 完成時の確認項目：

- ✅ `RegimeMapAnalyzer` 実装 + 12 テスト PASS
- ✅ `ParetoFrontAnalyzer` 実装 + 8 テスト PASS
- ✅ 既存テスト 28 個全て PASS（リグレッション無し）
- ✅ 全リポジトリテスト 305+ PASS
- ✅ 日本語ドキュメント完備
- ✅ 大杉さんの最終確認

---

**次のステップ**: 大杉さんのレビュー → Code君への Request Letter 作成 → 実装開始

