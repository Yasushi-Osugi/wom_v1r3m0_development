# wom-v1r4m0 Phase 2: Regime Map & Pareto Front 実装ガイド

**設計文書（正典）**: `requests/Phase2_DesignMD_RegimeMap.md`
**実装責任**: Code君　**設計責任**: Claude君　**レビュー**: 大杉さん（確認待ち）
**実装日**: 2026年9月6日

---

## 1. 概要

Merit Order（Phase 1、単一週の最適調達分析）を、複数週にまたがる**市場状況判断**（Regime Map）と、
**複数目的のトレードオフ可視化**（Pareto Front）へ拡張した。

- `wom/visualization/regime_map.py` — `RegimeMapAnalyzer`
- `wom/visualization/pareto_front.py` — `ParetoFrontAnalyzer`
- `tests/test_regime_map.py`（12テスト）
- `tests/test_pareto_front.py`（8テスト）

既存 Phase 1（28テスト）・リポジトリ全体（既存297テスト）に対する回帰は無し。
**全体 325 テスト PASS を確認済み**。

---

## 2. RegimeMapAnalyzer（Phase 2-A）

### 2.1 分類ロジック

設計文書 2.2 節の通り、Merit Order 結果の `fulfillment_rate` と `average_lead_time` から
需要レベル（Low/Medium/High）と供給タイト度（Tight/Balanced/Surplus）を判定する。

```python
classify_demand_level(fulfillment_rate)
    fulfillment_rate >= 0.95        -> "Low"
    0.75 <= fulfillment_rate < 0.95 -> "Medium"
    fulfillment_rate < 0.75         -> "High"

classify_supply_tightness(average_lead_time, threshold=14, range=7)
    average_lead_time > threshold+range  -> "Tight"     (> 21日)
    threshold-range <= x <= threshold+range -> "Balanced" (7-21日)
    average_lead_time < threshold-range  -> "Surplus"   (< 7日)
```

`RegimeMapAnalyzer(merit_order_results, config)` の `config` で
`demand_lead_time_threshold`（既定14）・`supply_tightness_range`（既定7）を上書き可能。

### 2.2 regime_score の算出式（設計文書に無い箇所・今回定義）

設計文書は `regime_score` の出力例（`demand_pressure=8.2`, `supply_risk=7.5`）のみを示し、
算出式は未規定だったため、以下の連続指標として実装した（分類の閾値とは独立に、
0-10 スケールで市場の逼迫度合いを表現する）：

```python
demand_pressure = clip(10 * (1 - fulfillment_rate), 0, 10)
# fulfillment_rate=1.0 → 0（圧力なし）、0.0 → 10（最大圧力）

supply_risk = clip(10 * average_lead_time / (2 * threshold), 0, 10)
# average_lead_time = threshold のとき 5（中立）、2*threshold 以上で 10（最大リスク）
```

### 2.3 regime_cell と推奨戦略

3×3マトリクスの行=供給タイト度（Tight=1, Balanced=2, Surplus=3）、
列=需要レベル（Low=1, Medium=2, High=3）として `regime_cell = (row, col)` を返す。
9通りの組み合わせは設計文書 2.3 節の `REGIME_STRATEGIES` をそのまま実装。

### 2.4 classify_horizon() の risk_weeks / opportunity_weeks

設計文書 2.1 節のマトリクス図で、Tight行が「Risk Mode」、Surplus行が「Opportunity」と
ラベル付けされていることに対応させ、以下で定義した：

- `risk_weeks`: `supply_tightness == "Tight"` となった週（horizon内の1始まり位置）
- `opportunity_weeks`: `supply_tightness == "Surplus"` となった週

`transition_matrix` は隣接週間の regime ラベル（`"{supply_tightness}/{demand_level}"`）の
遷移回数を、遷移元ラベルごとに正規化した遷移確率として実装。

### 2.5 get_strategy_actions()

9戦略それぞれに対応する `actions`（実装アクションのリスト）と `kpi_targets`
（`max_lead_time_days` / `min_quality_score` / `cost_tolerance_pct`）を
設計文書 2.3 節の戦略概要表に基づいて定義した静的テーブル `STRATEGY_ACTIONS` から返す。
未知の戦略名を渡すと `ValueError`。

---

## 3. ParetoFrontAnalyzer（Phase 2-B）

### 3.1 目的関数

```python
compute_allocation_objectives(allocation) -> {
    "cost": allocation["allocated_qty"] * allocation["unit_cost_usd"],  # 総コスト（絶対額）
    "quality": allocation["quality_score"],
    "lead_time": allocation["lead_time_days"],
}
```

設計文書 3.3.1 の docstring 通り、入力は Merit Order の `recommended_allocation` に含まれる
**1サプライヤー分のフラットな配分レコード**（設計文書 3.3.2 の使用例で `allocations_list.extend(...)`
としているのと同じ形式）。

### 3.2 支配関係・Pareto Front

設計文書 3.2 節の定義通り：

> 解 A が解 B より「Pareto 支配」される ⟺ すべての目的軸で A が B 以上に悪く、少なくとも1軸で厳密に悪い

cost・lead_time は小さいほど良い、quality は大きいほど良い、として O(n²) 総当たりで判定
（設計文書 5.1 節の通り、配分案数 n=10〜100 程度を想定した素朴実装）。
`compute_pareto_front()` は cost 昇順でランク付けした Pareto Front を返す。

### 3.3 ⚠️ 既知の注意点：総コスト（絶対額）比較の性質

`cost` を「単価」ではなく「配分量×単価の総額」で定義しているため（設計文書 3.3.1 の
docstring の通り）、**配分量（`allocated_qty`）が小さいレコードほど総コストが小さくなり、
Pareto Front 上で有利に見える**。

実際にサンプルデータで検証したところ、複数週・複数サプライヤーの配分レコードを
フラットに1つのリストへ集約すると（設計文書 3.3.2 の使用例通りの操作）、
「小ロット・高品質・短納期」のレコード1件だけが他の全レコードを支配し、
Pareto Front が1点に収束するケースが確認された（`demo_phase2.py` で再現）。

これは実装のバグではなく、**「総コストを主軸に複数の異なる規模の配分案を直接比較する」**
という設計文書の定義から論理的に導かれる帰結。実務での解釈に使う際は、以下のいずれかの
補正を検討する余地がある（Phase 3 以降の検討候補、今回はスコープ外）：

1. `cost` を「配分量×単価」ではなく「単価そのもの」に変更し、規模に依存しない比較にする
2. フラットな個別レコードではなく、「必要数量を満たす配分セット全体」を1つの解として比較する
   （設計文書 3.3.2 の使用例とは異なる粒度になる）

### 3.4 compute_tradeoff_ratios()

Pareto Front を cost 昇順に並べ、**隣接ランク間**の `cost_increase_pct`（コスト増加率%）・
`quality_gain_points`（品質差）・`lead_time_change_days`（リードタイム差）を算出し、
`ratio` に人間可読な要約文を格納する。

---

## 4. テスト結果

```
tests/test_regime_map.py    : 12 passed
tests/test_pareto_front.py  : 8 passed
tests/test_merit_order.py   : 28 passed（Phase 1、回帰なし）
tests/ (リポジトリ全体)      : 325 passed（既存305 + 新規20）
```

`demo_phase2.py`（スクラッチパッド）で Merit Order → Regime Map → Pareto Front の
一連のデータフローを実データ的なシナリオで目視確認済み（Tight/Medium レジームの検出、
risk_weeks の抽出、戦略アクションの提示まで一通り動作）。

---

## 5. Phase 3 への申し送り事項

- Pareto Front の cost 定義（3.3節の注意点）の扱い方針の確定
- Regime Map の `regime_score` 算出式（今回 Claude君が暫定定義）の大杉さんレビュー
- Merit Order → Regime Map → Pareto Front の3モジュールを GUI（Management タブ等）へ
  統合するかどうかの検討（今回はいずれも `wom/visualization/` 配下の独立モジュールとして実装、
  Planning Engine 本体・禁足コアには一切触れていない）
