# wom-v1r4m0 Phase 3: Merit Order / Regime Map / Pareto Front 可視化層 実装ガイド

**設計正典**: `requests/Phase3_RequestLetter_to_CodeKun.md`（`requests/Phase3_DesignMD_Visualization.md` rev.3 に基づく）
**実装責任**: Code君　**設計責任**: Claude君　**レビュー**: 大杉さん（R1/R3/R4 確定・R2 見送り確定、2026-09-06）
**実装日**: 2026年9月6日

---

## 1. 概要

Phase 1（`MeritOrderAnalyzer`）・Phase 2（`RegimeMapAnalyzer`／`ParetoFrontAnalyzer`）は headless な分析ロジックで、戻り値は全て Dict/JSON だった。Phase 3 はこれに matplotlib による静止画描画層を追加する。

- `tools/plot_merit_order_suite.py`（新規）— 6つの描画関数 + CLI
- `wom/visualization/pareto_front.py`（Phase 3-0 のみ変更、追加APIのみ・既存挙動不変）
- `tests/test_merit_order_plot.py`（新規、9テスト）
- `tests/test_pareto_front.py`（追記、4テスト。既存8テストは無変更）

制約（絶対厳守、設計文書§6.1）: matplotlib のみ（plotly/bokeh/dash/streamlit/seaborn 禁止）、新規依存なし、`matplotlib.use("Agg")` を pyplot import 前、図中テキストは全て英語、各関数は出力パスを返す、禁足コア無接触、乱数不使用。

---

## 2. Phase 3-0：`ParetoFrontAnalyzer` の plan 粒度追加

### 2.1 背景（既知問題の解消）

Phase 2 で判明していた問題：`compute_allocation_objectives()` の `cost` は「配分量×単価の総額」であり、`allocations` の1要素が**サプライヤー1社分のレコード**だと、配分量が小さいレコードほど無条件に有利になる。複数週・複数シナリオの配分レコードをフラットに集約すると、小ロット・高品質・短納期の1レコードが他を全て支配し、Pareto Front が1点に収束していた（CLAUDE.md 既知事項）。

### 2.2 解法：粒度の追加（既存の record 粒度は1ミリも変えない）

**1解 = 1配分案**（`calculate_merit_order()` 1回分の結果＝ required_qty を満たす配分セット全体）という粒度を追加した。

```python
# 既存（無変更）
pa = ParetoFrontAnalyzer(allocations)          # _granularity="record"（既定）
pa.compute_allocation_objectives(allocation)   # サプライヤー1社分 → {cost, quality, lead_time}

# 新規
pa = ParetoFrontAnalyzer.from_merit_order_results(results, lead_time_metric="weighted_avg")
                                                # _granularity="plan"
pa.compute_plan_objectives(result)             # merit_order result 1件 → {cost, quality, lead_time}
pa.compute_all_solutions()                     # 支配解も含む全解（on_front, rankつき）
```

`compute_pareto_front()` は `_granularity` に応じて内部で目的関数を切り替えるだけで、支配判定・ソート・rank採番のロジックは完全に共通化（`_rank_front()` という内部ヘルパーに集約し、`compute_pareto_front()` と `compute_all_solutions()` の両方から使う）。

### 2.3 R1確定：lead_time の定義

配分案「全体」で見れば、最後に届くサプライヤーが計画完了を決めるため binding な指標は `max(lead_time_days)` だが、既存実装・Phase 2設計は数量加重平均。**両立させるため、既定を`"weighted_avg"`のまま維持し、`lead_time_metric="max"`をオプション提供**（デフォルトを変えないことで既存の回帰値を保護）。

```python
cost = result.get("total_cost", 0)              # 再計算しない（allocate_demand()が既に数量加重で算出済み）
quality = result.get("average_quality", 0)      # 同上
lead_time = (
    max(a["lead_time_days"] for a in result["recommended_allocation"])
    if lead_time_metric == "max"
    else result.get("average_lead_time", 0)
)
```

### 2.4 実証結果

異なる demand フィルタ（`min_quality_acceptable`/`max_lead_time_acceptable`。**`constraints`ではなく`demand`辞書のキー**である点に注意——実装を読んで確認した）で生成した3つの配分案が、record粒度では1点に収束していたところ、plan粒度では**3案とも相互に非支配**のまま Pareto Front に残ることを確認（`tests/test_pareto_front.py::TestPlanGranularity::test_pareto_front_from_plans_multiple_on_front`）。

---

## 3. Phase 3-A：Merit Order 曲線

### 3.1 `plot_merit_order_curve(result, out, *, title=None, annotate_lambda=True) -> str`

電力の merit order curve と同形式の階段図。`result["merit_order"]`（`rank`昇順・`cumulative_supply_from_rank_1`込み）をそのまま使い、累積計算はやり直さない。

- 需要線（`required_qty`の縦破線）の**左側は濃色（実供給）・右側は淡色（非供給）**
- 需要線がブロックの途中に来る場合は**そのブロックを2分割**して描く（`x_left < req < x_right`のケース）。単純に「ブロック全体を濃淡どちらか」にすると誤解を招くため
- λ（シャドープライス、需要線が当たる限界サプライヤーの単価）は水平補助線+テキスト注記。**供給不足（`fulfillment_rate<1.0`）でλが定義できない場合は水平線を引かず、"Unmet demand: N units (fulfillment X%)" に切り替える**（λを0や最後のサプライヤー単価で代用しない）

### 3.2 `plot_merit_order_shift(before, after, out, *, labels=("Before","After"), title=None) -> str`

2つの `calculate_merit_order()` 結果（例：為替変化前後）を`ax.step(..., where="post")`で重畳。`supplier_id`をキーに`rank`を突き合わせ、**入れ替わったサプライヤー**をテキスト注記（`SUP_002 (#1->#3)`形式）。片方にしか存在しないサプライヤーは`(new)`/`(dropped)`として別途列挙。

---

## 4. Phase 3-B：Regime Map

入力は `RegimeMapAnalyzer.classify_horizon()` の戻り値。

### 4.1 `plot_regime_matrix(horizon, out, *, title=None) -> str`

3×3ヒートマップ（`ax.imshow`）。**セル集計は`regime_cell`のタプルではなく`supply_tightness`/`demand_level`の文字列ラベルから行う**（インデックスの基点に依存させないため）。セル内テキストは戦略名+週数（該当週が0でも戦略名は表示）、行ラベル右に"Risk Mode"/"Balanced"/"Opportunity"、`dominant_regime`セルを太枠強調。

### 4.2 `plot_regime_timeline(horizon, out, *, title=None) -> str`

上段：`demand_pressure`/`supply_risk`の2本折れ線（Y軸`[0,10]`固定）+ `risk_weeks`（赤帯）/`opportunity_weeks`（緑帯）。下段：週次`recommended_strategy`の色分け帯。

**重要な実装上の罠**：`risk_weeks`/`opportunity_weeks`は「週ラベル」ではなく**1始まりの位置**（`regime_map.py`の`[i+1 for i, w in enumerate(...)]`）。X座標に使うときは`-1`する必要があり、これを取り違えると帯が1週分ずれるが見た目では気づきにくい。本実装では`ax.axvspan(pos-1-0.5, pos-1+0.5, ...)`として正しく処理し、実データでずれがないことを確認済み。

### 4.3 R2確定：遷移行列ヒートマップは見送り

`transition_matrix`（遷移確率）を行列ヒートマップとして描く案があったが、`horizon_weeks=12`では遷移サンプルが11件しかなく、レジームの組み合わせ81通りに対して標本が極端に不足し、確率値のほとんどが1.0か0.5という退化した値になる。大杉さんの判断によりPhase 3では実装せず、12週程度なら`plot_regime_timeline`で遷移を目で直接追える、とした（設計文書§4.3）。将来、52週以上の長期ホライズン（`soysauce-jpy-2027`等）では、確率ではなく実測回数(counts)ベースで実装することを検討する。

---

## 5. Phase 3-C：Pareto Front + 平行座標

### 5.1 `plot_pareto_scatter(front, all_solutions, out, *, tradeoffs=None, title=None) -> str`

2パネル（左：Cost vs Quality、右：Cost vs Lead Time、色は残りの1軸のcolormap）。Pareto Front上の解は★+cost昇順の線分接続+`#rank`ラベル、支配解は淡いグレーの小さな`o`。`tradeoffs`（`compute_tradeoff_ratios()`の戻り値）を渡すと、隣接ランク間の線分中点に`+N% cost / ±M quality`形式で注記。

**実装時の細かい修正**：品質やリードタイムが「悪化」する遷移（例：品質-15.4）の注記で、符号を`+`固定でハードコードしていたため`+-15.4`という二重符号になるバグがあった。`{value:+.1f}`形式指定子に置き換えて解消。

Pareto解が1点しかない場合や、支配解が0件の場合も例外を出さず正常に描画する。

### 5.2 R3確定：`build_supplier_share_axes()` + `plot_parallel_coordinates()`

平行座標の軸に「案ごとに登場するサプライヤーが違う」問題を、以下の手順で解決（設計文書§5.2.1）：

1. 全配分案の**和集合**からサプライヤーを収集
2. **Merit Order順（`unit_cost_usd`昇順、タイブレークは`supplier_id`辞書順）**に並べ、上位K社（既定5）を軸に採用
3. K社に入らなかった分は`Others`軸1本に集約
4. ある案が使っていないサプライヤーの share は明示的に`0.0`（キー自体は必ず存在させる。欠測ではなく「使わない」という意味を持つ値のため）

```python
axis_labels, shares = build_supplier_share_axes(all_solutions, top_k=5)
# axis_labels: ["SUP_002", "SUP_001", ..., "Others"]（Merit Order順）
# shares: [{axis_label: 比率}, ...]（各案の合計1.0）
```

`plot_parallel_coordinates()` の軸構成は**目的軸3本（Cost/Quality/Lead Time、min-maxで[0,1]、cost/lead_timeは反転して「上ほど良い」に統一）+ share軸（[0,1]固定スケール、min-maxしない）**。2群の間に縦の区切り線を入れて視覚的に分離（share軸には「上ほど良い」の規約が適用されないため）。各軸の上下端に実値の目盛を表示。

**実装時に発見・修正したバグ**：軸の下端実値ラベル（例"3.5e+05"）を、matplotlibの既定xtickラベル（"Cost"等の軸名）と同じ視覚的な位置に描画していたため、文字が重なって読めなくなっていた（"3cost05"のような文字化け状態）。原因は、matplotlibの既定xtickラベルが軸線から一定ポイント数下に固定描画される一方、自前の実値ラベルはデータ座標で配置していたため、たまたま同じ視覚位置に来ていたこと。`ax.set_xticklabels([])`で既定ラベルを無効化し、実値ラベル・軸名ラベルの両方を十分な間隔を空けて自前でデータ座標に描画することで解消した。

折れ線は1本＝1配分案。Pareto解は`rank`順のカラーマップ（`viridis`）、支配解は淡いグレー・細線・半透明。

---

## 6. CLI（`tools/plot_merit_order_suite.py`）

```bash
python -m tools.plot_merit_order_suite --suppliers <CSVパス> --required-qty 5000 --out output/visualization/
python -m tools.plot_merit_order_suite --demo
```

`--demo`は内蔵の5サプライヤー（`DEMO_SUPPLIERS`）による**完全に決定的な**（乱数不使用）サンプルデータで6枚のPNGを一発生成する。

### 6.1 `--demo`データセット設計上の注意（次に触るClaude/Code君へ）

`DEMO_SUPPLIERS`はMerit Order曲線・Shift・Regime Map・Pareto Frontの4用途すべてで共用している。設計時に判明した制約：

- **Regime Mapの`Tight`（供給タイト）レジームは、最安サプライヤー群のlead_timeが軒並み短いと到達不能**になる。加重平均lead_timeの理論上限は「総供給能力で全量調達した場合の加重平均」であり、これがTight閾値（`demand_lead_time_threshold + supply_tightness_range` = 既定21日）を超えない限り、どれだけ需要を増やしてもTightに到達しない。デモでは「緊急調達枠」（`Epsilon Emergency`、lead_time=70日、cost=90）を1社加え、この上限を意図的に21日超に設計している
- **`_plan_scenarios()`（quality/lead_timeの中央値を閾値にする汎用フォールバック、`--suppliers`のCSVモード用）は、データセット次第では複数シナリオが同一の配分結果に収束しうる**（本データセットでは実際に発生した）。バグではないが、デモとしての訴求力に欠けるため、`--demo`専用には`DEMO_PLAN_SCENARIOS`（`min_quality_acceptable=95`/`max_lead_time_acceptable=5`と、事前に手計算で3案が相互に非支配になることを確認した値）を別途用意した

### 6.2 生成される図（ファイル名固定）

| ファイル名 | 関数 |
|---|---|
| `merit_order_curve.png` | `plot_merit_order_curve()` |
| `merit_order_shift.png` | `plot_merit_order_shift()` |
| `regime_matrix.png` | `plot_regime_matrix()` |
| `regime_timeline.png` | `plot_regime_timeline()` |
| `pareto_scatter.png` | `plot_pareto_scatter()` |
| `parallel_coordinates.png` | `plot_parallel_coordinates()` |

---

## 7. テスト結果・確認事項

```
tests/test_merit_order_plot.py : 9 passed（スモーク7 + 軸構築ロジック2）
tests/test_pareto_front.py     : 12 passed（既存8 + 新規4。既存8件は無変更で確認済み）
リポジトリ全体                  : 338 passed（既存325 + 新規13、回帰なし）
golden 12ケース（13件収録）     : 全PASS（不変。Planning Engine 無接触のため自明だが確認した）
```

実装後の確認事項（設計文書記載の3項目）：
- `grep -rn "plotly\|bokeh\|dash\|streamlit\|seaborn" tools/plot_merit_order_suite.py wom/visualization/` → 本ファイルのコメント（禁止事項の説明）以外ヒットなし
- 図中の`set_title`/`set_xlabel`/`set_ylabel`/`legend`/`.text`/`.annotate`呼び出しに日本語文字が含まれていないことを正規表現で確認
- `python -m tools.plot_merit_order_suite --demo` で6枚のPNGを生成し、大杉さんへ目視QA用に送付

---

## 8. Phase 4 への申し送り

設計文書§11の対応表の通り、Phase 3の各成果物はPhase 4（生産配分の利益地形図・N市場化）の土台になる想定：

| Phase 4 の手法 | Phase 3 の土台 |
|---|---|
| ① メリットオーダー曲線の市場配分への適用 | `plot_merit_order_curve`/`plot_merit_order_shift` |
| ② レジーム地図（外部環境パラメータ空間へ） | `plot_regime_matrix` |
| ③ Pareto + 平行座標（利益×ロバストネス） | `plot_pareto_scatter`/`plot_parallel_coordinates` |
| ④ 階層化三角図 | A系統`tools/plot_allocation_map.py`（`oil-global-2027`の日欧米3市場体制が適用候補） |

未対応・次回検討：
- 遷移行列ヒートマップ（R2見送り、52週以上のホライズンで実測回数ベースの実装を検討）
- レジーム地図の軸を外部環境パラメータ空間へ差し替え、A系統（`ask_global_allocation`）への接続——いずれもPhase 4スコープ
