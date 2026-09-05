# wom-v1r4m0: 利益ランドスケープ・N市場拡張 開発ガイド

## 概要

`wom-v1r4m0`ブランチは、WOM v1r3m1をベースに、**利益ランドスケープの可視化と多市場展開**機能を実装する開発ブランチです。

### 開発テーマ
- **利益ランドスケープの多次元可視化**：Merit Order曲線、Regime map、Pareto frontの実装
- **N市場拡張ロジック**：複数市場間のPSI（Planning & Supply Integration）粒度での連携
- **マルチシナリオ分析**：市場条件の変化に応じた利益構造の動的可視化

---

## 1. アーキテクチャ概要

### 1.1 モジュール構成

```
wom/
├── visualization/          # 新規：可視化エンジン
│   ├── __init__.py
│   ├── merit_order.py      # Merit Order曲線生成
│   ├── regime_map.py       # Regime map可視化
│   ├── pareto_front.py     # Pareto front分析
│   └── hierarchical_triangulation.py  # 階層的三角測量
├── ppc/                    # Profit Planning & Control（拡張）
│   ├── ppc_profit_zone.py  # 利益ゾーン定義（拡張）
│   ├── ppc_landscape.py    # 新規：利益ランドスケープモデル
│   └── multi_market_bridge.py  # 新規：複数市場間連携
├── engine/                 # プランニングエンジン（拡張）
│   └── multi_market_planner.py  # 新規：複数市場統合プランナー
└── gui/                    # UI（拡張）
    └── profit_landscape_viewer.py  # 新規：ランドスケープビューア
```

### 1.2 データフロー

```
[Input Data]
   ├─ capacity_plan.csv (各市場別)
   ├─ demand_forecast.csv (各市場別)
   ├─ ppc_node_profit_zone.csv (拡張)
   └─ ppc_market_condition.csv (新規)
          ↓
[Multi-Market Engine]
   ├─ Multi-market PSI planning
   ├─ Market-level allocation
   └─ Cross-market optimization
          ↓
[Profit Landscape Model]
   ├─ Merit Order生成
   ├─ Regime map分類
   ├─ Pareto front計算
   └─ Hierarchical triangulation
          ↓
[Visualization Layer]
   ├─ Interactive charts
   ├─ Real-time updates
   └─ Scenario comparison
          ↓
[Output / Reports]
   ├─ profit_landscape.json
   ├─ merit_order_analysis.csv
   └─ regime_transition_log.json
```

---

## 2. 主要機能仕様

### 2.1 Merit Order曲線 (merit_order.py)

**目的**：サプライヤー/製造拠点のコスト階層を可視化し、最適な調達戦略を決定

**入力データ**
- 各サプライヤーの単価 (price per unit)
- 最大供給量 (max_supply)
- リードタイム (lead_time)
- 品質スコア (quality_score, 0-100)

**処理ロジック**
1. コスト順に供給者をソート
2. 累積供給量に対するコスト曲線を生成
3. 需要レベル別の最適ミックスを計算
4. 市場条件（為替、関税）に応じた動的更新

**出力フォーマット**
```json
{
  "merit_order": [
    {
      "rank": 1,
      "supplier_id": "SUP_001",
      "unit_cost": 50,
      "cumulative_supply": [0, 1000],
      "quality_score": 95,
      "lead_time_days": 14
    },
    ...
  ],
  "demand_point": {
    "week": "2026-W36",
    "required_qty": 5000,
    "recommended_mix": [
      {"supplier_id": "SUP_001", "qty": 1000},
      {"supplier_id": "SUP_002", "qty": 3000},
      {"supplier_id": "SUP_003", "qty": 1000}
    ],
    "total_cost": 265000
  }
}
```

### 2.2 Regime Map（regime_map.py）

**目的**：市場環境（需要・供給・コスト）の組み合わせを分類し、各レジーム下での最適戦略を提示

**市場レジーム分類**（3×3マトリクス）

| 需要 ↓ / 供給 → | 供給余剰 | 供給均衡 | 供給不足 |
|---|---|---|---|
| 需要弱 | **レジームA** (過剰在庫リスク) | **レジームB** (安定) | **レジームC** (低稼働) |
| 需要均衡 | **レジームD** (価格下げ余地) | **レジームE** (最適状態) | **レジームF** (価格上げ圧力) |
| 需要強 | **レジームG** (コスト削減) | **レジームH** (リード時間短縮) | **レジームI** (危機対応) |

**入力データ**
- 過去12週の需要実績と予測
- 過去12週のサプライヤー供給実績
- 現在の在庫レベル

**処理ロジック**
1. 需要レベルを「弱(CV>0.3)」「均衡」「強」に分類
2. 供給状況を「余剰」「均衡」「不足」に分類
3. 各市場・各商品カテゴリについて現在のレジームを判定
4. レジーム遷移リスク（今後4週で移行するリスク）を計算

**出力フォーマット**
```json
{
  "regime_analysis": {
    "current_regime": "E",
    "regime_name": "最適状態",
    "characteristics": {
      "demand_level": "均衡",
      "supply_situation": "供給均衡",
      "inventory_status": "適正在庫"
    },
    "transition_risk": {
      "next_week_regime": "F",
      "probability": 0.25,
      "recommended_action": "リード時間削減、サプライヤー多源化検討"
    },
    "key_kpi": {
      "inventory_days": 14,
      "stockout_risk_pct": 0.02,
      "cost_opportunity": -5000
    }
  }
}
```

### 2.3 Pareto Front（pareto_front.py）

**目的**：複数の目的関数（コスト・リードタイム・品質・柔軟性）の間のトレードオフを可視化し、選択肢を提示

**目的関数**
1. **総コスト最小化** (C)：調達・製造・在庫・流通コスト
2. **リードタイム最小化** (LT)：サプライ～販売までの期間
3. **品質最大化** (Q)：不良率、返品率、顧客満足度
4. **柔軟性最大化** (F)：需要変動への対応能力

**処理ロジック**
1. 全可能な供給チェーン構成を生成（制約内で）
2. 各構成について4つの目的関数値を計算
3. Pareto優位性を判定（すべての目的で劣るものを除外）
4. 残されたPareto最適解をクラスタリング

**出力フォーマット**
```json
{
  "pareto_frontier": [
    {
      "solution_id": "OPT_001",
      "name": "低コスト・短リード重視",
      "objectives": {
        "total_cost": 1000000,
        "lead_time_days": 21,
        "quality_score": 88,
        "flexibility_score": 0.7
      },
      "configuration": {
        "primary_suppliers": ["SUP_002", "SUP_003"],
        "safety_stock_days": 7,
        "buffer_location": "Regional DC"
      },
      "tradeoff_note": "品質面でやや劣る。単価下げの効果大"
    },
    {
      "solution_id": "OPT_002",
      "name": "バランス型",
      "objectives": {
        "total_cost": 1080000,
        "lead_time_days": 18,
        "quality_score": 94,
        "flexibility_score": 0.85
      },
      "configuration": {
        "primary_suppliers": ["SUP_001", "SUP_002"],
        "safety_stock_days": 10,
        "buffer_location": "Factory + Regional DC"
      },
      "tradeoff_note": "4つの目的のバランスが取れた解"
    },
    ...
  ],
  "dominated_solutions": 47
}
```

### 2.4 Hierarchical Triangulation（hierarchical_triangulation.py）

**目的**：4次元以上のPareto frontを低次元に射影し、直感的に理解できる形で可視化

**処理ロジック**
1. 4つの目的関数値を正規化 (0-1スケール)
2. 「戦略的重要度」の重み付けを適用（ユーザー入力）
3. 階層的な三角形分割：
   - L1：コスト vs. リードタイム (基本的なトレードオフ)
   - L2：L1結果 vs. 品質 (製品戦略を加える)
   - L3：L2結果 vs. 柔軟性 (レジリエンス軸を加える)
4. 各層で三角形の頂点（extremal solution）を抽出

**出力フォーマット**
```json
{
  "triangulation": {
    "level_1": {
      "dimension": "Cost vs. Lead Time",
      "vertices": [
        {"id": "COST_MIN", "name": "低コスト戦略", "position": [0.0, 0.8]},
        {"id": "LT_MIN", "name": "短リード戦略", "position": [0.8, 0.0]},
        {"id": "BALANCE_1", "name": "基本バランス", "position": [0.4, 0.4]}
      ]
    },
    "level_2": {
      "dimension": "L1 vs. Quality",
      "vertices": [
        {"id": "QUALITY_MAX", "name": "高品質戦略", "position": [0.4, 0.9]},
        ...
      ]
    },
    "level_3": {
      "dimension": "L2 vs. Flexibility",
      "vertices": [
        {"id": "RESILIENCE", "name": "レジリエンス重視", "position": [0.5, 0.7]},
        ...
      ]
    }
  },
  "user_weights": {
    "cost_importance": 0.3,
    "lead_time_importance": 0.2,
    "quality_importance": 0.3,
    "flexibility_importance": 0.2
  }
}
```

---

## 3. N市場拡張ロジック

### 3.1 複数市場の定義

各市場は以下の属性で定義：
```
Market = {
  "market_id": "APAC_JP",
  "market_name": "日本市場（アジア太平洋）",
  "currency": "JPY",
  "demand_forecast": [...],        # 週次需要予測
  "capacity_constraints": [...],   # 拠点ごとの生産能力
  "lead_time_matrix": [...],       # 拠点→市場のリードタイム
  "regulatory_constraints": [...]  # 関税、現地調達義務など
}
```

### 3.2 PSI粒度での複数市場連携

```
Week W における各市場の PSI：

┌─────────────────────────────────────────┐
│ Demand Forecast (D)                     │
│  = Sum(Market[i].demand[W])            │
└─────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────┐
│ Supply Planning (S)                     │
│  = 各拠点の生産計画                      │
│  - グローバル制約（全体能力上限）       │
│  - 市場別制約（現地規制、リードタイム） │
└─────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────┐
│ Inventory Reconciliation (I)            │
│  各市場のI[i][W] = I[i][W-1] + S[i][W] │
│                    - D[i][W]            │
│ グローバル最適化：                      │
│  minimize(sum(holding_cost[i]*I[i]))   │
│  subject to(I[i][W] >= safety_stock[i])│
└─────────────────────────────────────────┘
            ↓
┌─────────────────────────────────────────┐
│ Profit Calculation (P)                  │
│  = Revenue[i][W] - Cost[i][W]          │
│  - Inventory_Cost[i][W]                │
│  複数市場の P[i] を集約                 │
└─────────────────────────────────────────┘
```

### 3.3 市場間の需給バランシング

複数市場が同一工場をシェアする場合：

```
Factory[j] の生産能力 C[j] に対して：

min(sum(cost[j][i][W] * x[j][i][W]))
subject to:
  sum(x[j][i][W]) <= C[j]        # 工場能力制約
  x[j][i][W] >= min_order[j][i]  # 最小ロット制約
  stock_after[i][W] >= ss[i]     # 安全在庫制約
  
Solve using:
  - Linear Programming (基本解)
  - Heuristic allocation (実行可能解の高速取得)
  - Simulation (各シナリオの結果確認)
```

---

## 4. データモデル拡張

### 4.1 新規CSVスキーマ

#### ppc_market_condition.csv
```csv
week,market_id,market_name,demand_level,supply_ratio,currency_rate,tariff_rate,notes
2026-W36,APAC_JP,日本市場,1.0,1.05,149.5,0.0,Normal condition
2026-W37,APAC_JP,日本市場,0.95,1.02,148.3,0.0,Weather impact forecast
```

#### ppc_profit_zone_extended.csv
```csv
market_id,market_name,node_id,sku_id,profit_zone,zone_threshold_min,zone_threshold_max,strategic_priority,notes
APAC_JP,日本市場,MFG_JP,SKU_001,High,500000,999999,1,プリミアム商品
APAC_JP,日本市場,MFG_JP,SKU_002,Medium,100000,499999,2,標準商品
APAC_JP,日本市場,MFG_JP,SKU_003,Low,0,99999,3,ロスリーダー商品
```

### 4.2 出力JSONスキーマ拡張

#### profit_landscape_summary.json
```json
{
  "analysis_date": "2026-09-05",
  "planning_horizon_weeks": 12,
  "markets_included": ["APAC_JP", "EMEA_EU", "AMER_US"],
  "global_kpi": {
    "total_revenue": 50000000,
    "total_cost": 42000000,
    "global_gross_profit": 8000000,
    "gp_margin": 0.16,
    "weighted_avg_lead_time": 18.5
  },
  "merit_order": {...},
  "regime_analysis": {...},
  "pareto_frontier": {...},
  "hierarchical_triangulation": {...},
  "market_details": [
    {
      "market_id": "APAC_JP",
      "regional_kpi": {...},
      "regime_current": "E",
      "profit_contribution": 0.35
    },
    ...
  ]
}
```

---

## 5. 実装ロードマップ

### Phase 1: 基盤構築 (Weeks 1-2)
- [ ] `visualization/`モジュール骨組み
- [ ] Merit Order曲線の基本実装
- [ ] テストデータセット準備
- [ ] CI/CD パイプライン確認

### Phase 2: コア機能 (Weeks 3-5)
- [ ] Regime map分類ロジック
- [ ] Pareto front計算
- [ ] 複数市場データモデル
- [ ] Golden test更新

### Phase 3: 高度な機能 (Weeks 6-8)
- [ ] Hierarchical triangulation
- [ ] マルチマーケットプランナー
- [ ] GUI統合

### Phase 4: 検証・最適化 (Weeks 9-12)
- [ ] 実データでの検証
- [ ] パフォーマンス最適化
- [ ] ドキュメント整備
- [ ] v1r4m0 リリース準備

---

## 6. 開発環境セットアップ

### 6.1 必要パッケージ
```bash
# 数値計算・最適化
pip install numpy scipy scikit-learn

# 可視化
pip install matplotlib plotly seaborn

# データ処理
pip install pandas

# 線形計画法
pip install pulp

# テスト
pip install pytest pytest-cov
```

### 6.2 ブランチワークフロー
```bash
# フィーチャーブランチを作成
git checkout -b feature/merit-order-v1 wom-v1r4m0

# 開発・コミット
git add ...
git commit -m "feat(visualization): implement merit order curve generation"

# テスト実行
pytest tests/test_merit_order.py

# プッシュ・プルリクエスト
git push origin feature/merit-order-v1
# GitHub上でPRを作成 → wom-v1r4m0 へのマージを指定

# レビュー後、マージ
```

---

## 7. テスト戦略

### 7.1 ユニットテスト
- `test_merit_order.py`：Merit Order生成ロジック
- `test_regime_map.py`：レジーム判定ロジック
- `test_pareto_front.py`：Pareto最適性判定
- `test_multi_market_planner.py`：複数市場統合

### 7.2 統合テスト
- サンプルCSV → JSON出力全体フロー
- 既存の golden test との互換性

### 7.3 ビジュアルテスト
- Matplotlibで生成した図が期待値通りか
- Plotlyインタラクティブチャートの動作確認

---

## 8. 参考資料・理論背景

### Merit Order曲線
- **参考論文**：「電力市場のメリットオーダーモデル」
- **応用**：サプライチェーンのサプライヤー選定，生産能力配分

### Regime Switching Model
- **理論**：Hamilton (1989) "A New Approach to the Economic Analysis of Nonstationary Time Series"
- **応用**：市場環境の離散的な状態転移を捉える

### Pareto最適性 & 多目的最適化
- **参考文献**：Marler & Arora (2004) "Survey of multi-objective optimization in engineering"
- **応用**：サプライチェーン設計における複数KPIのバランス

### Hierarchical Triangulation
- **数学的基礎**：Simplicial complexes，Delaunay triangulation
- **応用**：高次元目的関数の低次元表現と可視化

---

## 9. よくある質問 (FAQ)

**Q1: Regime mapと従来の「好況・不況」の分類の違いは？**

A: Regime mapは、需要と供給の**両軸**で環境を分類します。たとえば「供給不足」でも、それが「需要強」による場合と「需要弱」による場合では戦略が異なります。前者は価格上げ・リード短縮、後者は生産調整・コスト削減が適切です。

**Q2: Pareto frontで「複数の最適解」が出るのは、不確定ではないか？**

A: 複数の目的が本質的に矛盾している場合、「唯一の最適解」は存在せず、トレードオフを含む複数の選択肢が存在するのが正常です。これは不確定ではなく、**意思決定者が価値観（重み付け）を明確にする**必要があることを示しています。

**Q3: 既存のwom-v1r3m0のテストとの互換性は？**

A: v1r4m0は、既存のレイアウトと機能を**破壊しません**。新規モジュール（visualization/、ppc/の拡張）として追加され、既存の golden test は変わりません。

---

## 10. 連絡先・サポート

開発中の質問やIssueは、本ブランチの GitHub Issues で報告してください。
大杉（Ohsugi）にはClaude Code経由で相談してください。

**最終更新**: 2026-09-05
**ブランチ**: `wom-v1r4m0`
**ステータス**: アクティブ開発中
