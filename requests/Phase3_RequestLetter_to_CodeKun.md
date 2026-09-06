# Phase 3 実装 Request Letter — 可視化層（Merit Order / Regime Map / Pareto Front）

**宛先**: Code君
**作成日**: 2026年9月6日
**担当**: Claude君（設計・仕様）→ 大杉さん（レビュー承認済）→ Code君（実装）
**優先度**: MEDIUM（2週間）
**ブランチ**: `wom-v1r4m0`
**設計正典**: `requests/Phase3_DesignMD_Visualization.md`（R1/R3/R4 確定・R2 見送り確定済）

---

## 概要

`wom/visualization/` の3モジュール（Phase 1/2 で実装済、いずれも Dict を返す headless なロジック層）に対して、**matplotlib による静止画描画層**を追加する。

**対象ファイル**:

| ファイル | 扱い |
|---|---|
| `wom/visualization/pareto_front.py` | **API 追加のみ**（既存メソッドのシグネチャ・挙動は不変） |
| `wom/visualization/merit_order.py` | **無変更** |
| `wom/visualization/regime_map.py` | **無変更** |
| `tools/plot_merit_order_suite.py` | **新規作成**（描画関数＋CLI） |
| `tests/test_merit_order_plot.py` | **新規作成**（9テスト） |
| `tests/test_pareto_front.py` | **追記のみ**（4テスト追加。既存8テストは1文字も変更しないこと） |

**実装工数**: 約 12-16 時間（テスト含む）
**リスク**: 低い（Planning Engine・禁足コアに一切触れない。既存ロジックの変更は §V0 の追加APIのみ）

---

## ⚠️ 実装前に必ず読むこと — 絶対制約

### C1. matplotlib のみを使う

**plotly / bokeh / dash / streamlit その他 Web 系 GUI ライブラリの導入は禁止。**

WOM は企業の機密情報を扱い、**スタンドアロンの Windows PC での運用**を前提としている。Web 系 GUI は情報セキュリティの観点から採用しない。この判断は覆らないので、代替案の提案も不要。

### C2. 新規依存パッケージを追加しない

`matplotlib` / `numpy` / `pandas` は既に依存にある（CLAUDE.md L39）。それ以外は使わない。`seaborn` も入れないこと。

### C3. `matplotlib.use("Agg")` を pyplot import の**前**に置く

```python
import matplotlib
matplotlib.use("Agg")          # ← 必ず pyplot より前
import matplotlib.pyplot as plt
```

`tools/plot_allocation_map.py` L29-31 と同じ形。

### C4. 図中のテキストは**すべて英語**で書く

タイトル・軸ラベル・凡例・注記のすべて。**日本語を一文字も入れないこと。**

理由：日本語フォントが未導入の Windows 環境で豆腐（□□□）になるため。`tools/plot_allocation_map.py` が既にこの慣行で統一されている。日本語はドキュメントとソースコメントにのみ書く。

### C5. 各描画関数は「生成した出力パス」を返す

```python
def plot_xxx(...) -> str:
    ...
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out          # ← 必ず返す
```

`plot_allocation_map.py` の慣行。テストが `_nonempty(plot_xxx(...))` の形で検証できるようにするため。**`plt.close(fig)` を忘れないこと**（テストで大量に図を作るとメモリを食う）。

### C6. 禁足コアに触れない

`backward_planner.py` / `forward_planner.py` / `plan_copy.py` / `plan_node.py` / `sc_tree.py` / `push_pull.py` は**一切変更しない**。本 Phase はこれらと無関係。

### C7. 乱数を使わない

`--demo` のサンプルデータ生成を含め、**すべて決定的にすること**。`random` を使うとテストが不安定になる。週次の需要変動が必要な場合は、決定的な式（例：`5000 + 300 * ((w % 5) - 2)`）で作る。

---

## V0: `pareto_front.py` — cost 定義の粒度補正（**前提作業・最初に実施**）

Phase 3-C はこの補正なしには成立しない。最初に片付けること。

### V0.1 現状の問題

```python
def compute_allocation_objectives(self, allocation: Dict) -> Dict:
    allocated_qty = allocation.get("allocated_qty", 0)
    unit_cost = allocation.get("unit_cost_usd", allocation.get("unit_cost", 0))
    cost = allocated_qty * unit_cost      # ← 総額
```

`allocations` の1要素は**サプライヤー1社分の配分レコード**。したがって配分量が小さいレコードほど総コストが小さく、Pareto 上で無条件に有利になる。複数週・複数サプライヤーのレコードをフラットに集約すると、「小ロット・高品質・短納期」の1レコードが他の全レコードを支配し、**Pareto Front が1点に収束する**（実データで確認済、CLAUDE.md L1554）。

バグではなく定義上の帰結。

### V0.2 方針

**1解 = 1配分案**（required_qty を満たすサプライヤー配分のセット ＝ `calculate_merit_order()` 1回分の結果）という粒度を**追加**する。

**既存の `__init__(allocations)` 経路（record 粒度）は挙動を1ミリも変えないこと。** 既存8テストが無変更で通ることが受入条件。

### V0.3 実装仕様

`ParetoFrontAnalyzer` に以下を追加する。

#### V0.3.1 `__init__` に内部状態を1つ追加

```python
def __init__(self, allocations, objectives=None):
    self.allocations = list(allocations) if allocations else []
    self.objectives = list(objectives) if objectives else list(self.DEFAULT_OBJECTIVES)
    self._granularity = "record"          # ← 追加。"record" | "plan"
    self._lead_time_metric = "weighted_avg"   # ← 追加
```

デフォルトが `"record"` なので、既存の呼び出し側の挙動は不変。

#### V0.3.2 `from_merit_order_results()` クラスメソッド

```python
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
```

#### V0.3.3 `compute_plan_objectives()`

```python
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
```

**重要**: `total_cost` / `average_quality` / `average_lead_time` は `allocate_demand()` が既に数量加重で算出済み（`merit_order.py` L307-313）。**再計算しないこと**。

#### V0.3.4 `compute_pareto_front()` の分岐

```python
def compute_pareto_front(self) -> List[Dict]:
    if self._granularity == "plan":
        objective_fn = self.compute_plan_objectives
    else:
        objective_fn = self.compute_allocation_objectives

    solutions = [
        {"objectives": objective_fn(a), "allocation": a}
        for a in self.allocations
    ]
    # ... 以降の支配判定・ソート・rank採番は既存のまま変更しない ...
```

戻り値の `"allocation"` キーには、plan 粒度では **merit_order result 全体**（`recommended_allocation` を含む）が入る。平行座標がサプライヤー内訳を参照できるようにするため。

#### V0.3.5 `compute_all_solutions()`（新規・描画のために必要）

散布図・平行座標は「支配されている解」も淡色で描く必要があるため、全解を目的関数値つきで取れるメソッドを追加する。

```python
def compute_all_solutions(self) -> List[Dict]:
    """全解（支配されているものも含む）を目的関数値つきで返す。

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
```

実装は `compute_pareto_front()` の結果と突き合わせる形でよい。`allocation` オブジェクトの同一性（`id()`）ではなく**入力リスト上のインデックス**で対応づけること（辞書は unhashable なので）。

### V0.4 テスト仕様（`tests/test_pareto_front.py` に**追記**、4件）

**既存8件は1文字も変更しないこと。**

```python
class TestPlanGranularity:
    """V0: 1配分案=1解の粒度（Phase 3-0）"""

    def test_plan_objectives_matches_merit_order_totals(self, sample_suppliers):
        """compute_plan_objectives() が merit_order result の
        total_cost / average_quality / average_lead_time と一致する"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({"required_qty": 5000})

        pa = ParetoFrontAnalyzer.from_merit_order_results([result])
        obj = pa.compute_plan_objectives(result)

        assert obj["cost"] == result["total_cost"]
        assert obj["quality"] == result["average_quality"]
        assert obj["lead_time"] == result["average_lead_time"]

    def test_pareto_front_from_plans_multiple_on_front(self, sample_suppliers):
        """異なる制約で生成した複数の配分案を与えたとき、
        Pareto Front が1点に収束しないこと（§V0.1 の症状に対する回帰テスト）"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        scenarios = [
            {},                              # 制約なし（最安）
            {"quality_threshold": 95},       # 高品質のみ
            {"lead_time_max": 14},           # 短納期のみ
        ]
        results = [
            analyzer.calculate_merit_order({"required_qty": 5000},
                                           constraints=sc)
            for sc in scenarios
        ]
        pa = ParetoFrontAnalyzer.from_merit_order_results(results)
        front = pa.compute_pareto_front()

        assert len(front) >= 2, (
            "Pareto Front collapsed to a single point — "
            "the plan-granularity fix is not working"
        )

    def test_pareto_front_record_granularity_backward_compat(self, sample_suppliers):
        """従来の __init__(allocations) 経路が補正前と同じ結果を返すこと"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({"required_qty": 5000})
        allocations = result["recommended_allocation"]

        pa = ParetoFrontAnalyzer(allocations)
        assert pa._granularity == "record"

        front = pa.compute_pareto_front()
        # 各解の cost が allocated_qty * unit_cost_usd（総額）であること
        for item in front:
            a = item["allocation"]
            assert item["cost"] == a["allocated_qty"] * a["unit_cost_usd"]

    def test_lead_time_metric_max_option(self, sample_suppliers):
        """lead_time_metric='max' で最大リードタイムが採られること"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({"required_qty": 5000})

        pa_max = ParetoFrontAnalyzer.from_merit_order_results(
            [result], lead_time_metric="max"
        )
        expected = max(a["lead_time_days"]
                       for a in result["recommended_allocation"])
        assert pa_max.compute_plan_objectives(result)["lead_time"] == expected

        # 不正値は ValueError
        with pytest.raises(ValueError, match="lead_time_metric"):
            ParetoFrontAnalyzer.from_merit_order_results([result],
                                                         lead_time_metric="median")
```

**注**: `constraints` の実際のキー名は `merit_order.py` の `_filter_suppliers()` / `allocate_demand()` の実装に合わせること。上記は例示。**実装を読んで正しいキー名を使うこと。**

**期待結果**: 既存8件 + 新規4件 = 12件 全PASS

---

## V1: Merit Order 曲線（`tools/plot_merit_order_suite.py`）

### V1.1 `plot_merit_order_curve()`

電力の merit order curve と同じ形式の階段図。

```python
def plot_merit_order_curve(
    result: Dict,
    out: str,
    *,
    title: Optional[str] = None,
    annotate_lambda: bool = True,
) -> str:
    """Merit Order 曲線を描画し、保存先パスを返す。

    Args:
        result: calculate_merit_order() の戻り値
        out:    出力 PNG パス
        title:  図タイトル（None なら week と required_qty から自動生成）
        annotate_lambda: 需要線との交点にシャドープライス λ を注記するか
    """
```

#### 図の仕様

- **X軸**: `Cumulative supply (units)`
- **Y軸**: `Unit cost (USD/unit)`
- **各サプライヤー** = 幅 `max_supply` / 高さ `unit_cost_usd` の矩形。merit order 順（`rank` 昇順 = 単価昇順）に左から積む
- **縦破線**: `required_qty`
- **塗り分け**: 需要線の**左側は濃色**（実際に供給される）、**右側は淡色**（供給されない）
- **ラベル**: 各矩形の中に `supplier_name`。矩形の幅が狭くて入らない場合は `supplier_id`、それも入らなければ省略する
- 矩形の境界に細い白線を入れてブロックを区別しやすくする

#### 実装上の注意（重要）

**① 需要線をまたぐブロックの扱い**

`required_qty` がちょうどブロックの途中に来る場合、そのブロックは**部分供給**になる。1つの矩形を needle で分割し、左側を濃色・右側を淡色の**2つのパッチ**として描くこと。単純に「ブロック全体を濃色 or 淡色」にすると誤解を招く。

```python
x_left = 0.0
for s in result["merit_order"]:
    w = s["max_supply"]
    h = s["unit_cost_usd"]
    x_right = x_left + w
    if x_right <= req:
        # 全部供給される
        draw_rect(x_left, w, h, color=DARK)
    elif x_left >= req:
        # 全部供給されない
        draw_rect(x_left, w, h, color=LIGHT)
    else:
        # 需要線をまたぐ → 2分割
        draw_rect(x_left, req - x_left, h, color=DARK)
        draw_rect(req, x_right - req, h, color=LIGHT)
    x_left = x_right
```

**② λ（シャドープライス）の算出と、供給不足時の扱い**

λ = 需要線が当たっているブロックの `unit_cost_usd` = 限界サプライヤーの単価。

```python
lam = None
for s in result["merit_order"]:
    if s["cumulative_supply_from_rank_1"] >= req:
        lam = s["unit_cost_usd"]
        break
```

**総供給量が `required_qty` に届かない場合（`fulfillment_rate < 1.0`）、λ は定義されない。** その場合は水平線を引かず、代わりに図中に

```
Unmet demand: {req - total_supply:.0f} units (fulfillment {rate:.1%})
```

と注記すること。**λ を 0 や最後のサプライヤーの単価で代用しないこと**（意味が変わる）。

λ が定義できる場合は、`y = lam` の水平補助線 + `λ = {lam} USD/unit (marginal supplier: {name})` のテキスト注記を付ける。

**③ `cumulative_supply_from_rank_1` は既に入っている**

`merit_order.py` の `_build_merit_order()` が累積供給量を計算済み（L358）。描画側で積み直す必要はないが、矩形の左端座標は自前で持つほうが分かりやすい（上記コード例のとおり）。

### V1.2 `plot_merit_order_shift()`

為替・関税の変化でサプライヤー順位が入れ替わる様子を、2本の階段で重ねる。

```python
def plot_merit_order_shift(
    before: Dict,
    after: Dict,
    out: str,
    *,
    labels: Tuple[str, str] = ("Before", "After"),
    title: Optional[str] = None,
) -> str:
```

#### 図の仕様

- 2本の階段曲線を重畳。`ax.step(cum_supply, unit_cost, where="post")` を使う
  - Before: 細線・グレー・破線
  - After: 実線・カラー
- 各曲線の λ を水平線で示し、凡例に `λ_before` / `λ_after` の値を書く
- **順位が入れ替わったサプライヤーをマークする**：`supplier_id` をキーに before/after の `rank` を突き合わせ、変化したものを検出

```python
rank_before = {s["supplier_id"]: s["rank"] for s in before["merit_order"]}
rank_after  = {s["supplier_id"]: s["rank"] for s in after["merit_order"]}
swapped = [sid for sid in rank_after
           if sid in rank_before and rank_before[sid] != rank_after[sid]]
```

検出したサプライヤーを図の下部に注記する（例：`Rank changes: SUP_002 (#2→#1), SUP_001 (#1→#2)`）。矢印での図示は、重なると読めなくなるのでテキスト注記で十分。

- before/after の一方にしか存在しないサプライヤーがあり得る（除外制約が変わった場合など）。その場合は順位変化の検出対象から外し、注記に `(new)` / `(dropped)` として別途列挙する

---

## V2: Regime Map（`tools/plot_merit_order_suite.py`）

入力は `RegimeMapAnalyzer.classify_horizon()` の戻り値。

### V2.1 `plot_regime_matrix()`

```python
def plot_regime_matrix(horizon: Dict, out: str, *, title: Optional[str] = None) -> str:
```

#### 図の仕様

- 3×3 のヒートマップ（`ax.imshow()`）
- **行** = Supply Tightness、上から `Tight` / `Balanced` / `Surplus`
- **列** = Demand Level、左から `Low` / `Medium` / `High`
- **セルの値（色）** = そのセルに落ちた**週数**
- **セル内テキスト** = 戦略名（`REGIME_STRATEGIES` の値）＋ `(N weeks)` の2行
- **行ラベルの右側**に `Risk Mode` / `Balanced` / `Opportunity` を添える（Phase 2 設計書 §2.1 の図と同じ）
- `horizon["summary"]["dominant_regime"]` に対応するセルを**太枠**で強調

#### 実装上の注意

**セルの集計は `regime_cell` のインデックスに依存せず、文字列ラベルから行うこと。**

```python
SUPPLY_ORDER = ["Tight", "Balanced", "Surplus"]      # 上から
DEMAND_ORDER = ["Low", "Medium", "High"]             # 左から

counts = np.zeros((3, 3), dtype=int)
for w in horizon["week_by_week"]:
    r = SUPPLY_ORDER.index(w["supply_tightness"])
    c = DEMAND_ORDER.index(w["demand_level"])
    counts[r, c] += 1
```

`regime_cell` タプルの基点（0始まりか1始まりか）に依存した実装にしないこと。

戦略名は `regime_map.REGIME_STRATEGIES[(supply, demand)]` から引く（`week_by_week` に該当セルの週が1つもなくても戦略名は表示する）。

### V2.2 `plot_regime_timeline()`

```python
def plot_regime_timeline(horizon: Dict, out: str, *, title: Optional[str] = None) -> str:
```

#### 図の仕様

- **上段パネル**:
  - X = 週（`week_by_week[i]["week"]` のラベル。None の場合は `W{i+1}`）
  - Y = `regime_score` の2本の折れ線：`demand_pressure` / `supply_risk`（いずれも 0-10）
  - Y軸レンジを `[0, 10]` に固定する（週ごとに軸が伸縮すると比較できない）
  - **背景帯**：`risk_weeks` を赤系（`axvspan`, alpha 0.15）、`opportunity_weeks` を緑系でハイライト
- **下段パネル（細い帯）**:
  - 各週の `recommended_strategy` を色分けした帯として表示。戦略の切り替わりが時間軸で読める
  - 凡例に戦略名と色の対応を出す

#### 実装上の注意（**必ず読むこと**）

**`risk_weeks` / `opportunity_weeks` は「週ラベル」ではなく「1始まりの位置」である。**

`regime_map.py` の実装（L365-372）：

```python
risk_weeks = [i + 1 for i, w in enumerate(week_by_week)
              if w["supply_tightness"] == "Tight"]
```

つまり値 `3` は「2026-W03」ではなく「ホライズンの3番目の週」を意味する。**X座標に使うときは `-1` すること。**

```python
for pos in horizon["summary"]["risk_weeks"]:
    ax.axvspan(pos - 1 - 0.5, pos - 1 + 0.5, color="red", alpha=0.15)
```

ここを取り違えると帯が1週ずれる。しかも見た目では気づきにくい。

---

## V3: Pareto Front ＋ 平行座標（`tools/plot_merit_order_suite.py`）

**前提**: V0 が完了していること。

### V3.1 `plot_pareto_scatter()`

```python
def plot_pareto_scatter(
    front: List[Dict],
    all_solutions: List[Dict],
    out: str,
    *,
    tradeoffs: Optional[Dict] = None,
    title: Optional[str] = None,
) -> str:
    """目的空間の散布図（2パネル）を描画し、保存先パスを返す。

    Args:
        front: compute_pareto_front() の戻り値
        all_solutions: compute_all_solutions() の戻り値
        tradeoffs: compute_tradeoff_ratios() の戻り値（None なら注記なし）
    """
```

#### 図の仕様

2枚並びのパネル：

| パネル | X | Y | 点の色 |
|---|---|---|---|
| 左 | `Total cost (USD)` | `Quality score` | Lead time（colormap + colorbar） |
| 右 | `Total cost (USD)` | `Lead time (days)` | Quality（colormap + colorbar） |

- **Pareto Front 上の解**：★マーカー（大きめ）＋ cost 昇順で線分接続
- **支配されている解**：淡いグレーの小さな `o`
- **各 Pareto 点に `#rank` ラベル**を添える
- `tradeoffs` が渡された場合、左パネルの Pareto 線分の中点に `+5.0% cost / +3.2 quality` の形で注記する（`tradeoffs["cost_vs_quality"]` の `cost_increase_pct` と `quality_gain_points`）。右パネルは `tradeoffs["cost_vs_lead_time"]` を使う

#### 実装上の注意

- Pareto 解が1点しかない場合、線分接続は描けない。**例外を出さず、点だけ描くこと**（V0 の補正後は通常2点以上になるが、入力次第では1点もあり得る）
- 全解が Pareto Front 上（支配関係なし）の場合、グレーの点は0個になる。これも正常系として扱う

### V3.2 `build_supplier_share_axes()`（軸構築ヘルパー）

平行座標の軸決定は描画から独立した純粋ロジックなので、**単独の関数に切り出す**。単体テスト対象。

```python
def build_supplier_share_axes(
    solutions: List[Dict],
    top_k: int = 5,
) -> Tuple[List[str], List[Dict[str, float]]]:
    """全配分案の和集合から Merit Order 順に上位 K 社の軸を決め、
    各案の配分比率を返す。

    Args:
        solutions: compute_all_solutions() の戻り値（**plan 粒度であること**）
                   各要素の ["allocation"] が merit_order result で、
                   その ["recommended_allocation"] にサプライヤー配分が入る
        top_k: 軸として採用するサプライヤー数（デフォルト 5）

    Returns:
        (axis_labels, shares)
        axis_labels: ["SUP_002", "SUP_001", ..., "Others"]
                     Merit Order 順（unit_cost_usd 昇順）。
                     top_k に収まらないサプライヤーが1社以上いる場合のみ
                     末尾に "Others" が付く
        shares: [{axis_label: 比率}, ...]（solutions と同順。各案の合計 1.0）

    Raises:
        ValueError: solutions が plan 粒度でない（recommended_allocation を持たない）とき
    """
```

#### アルゴリズム

1. 全 solution の `allocation["recommended_allocation"]` から、**サプライヤーの和集合**を作る。各サプライヤーの `unit_cost_usd` も記録する（複数案に現れる場合は最小値を採る）
2. `unit_cost_usd` 昇順（= Merit Order 順）にソートし、**上位 `top_k` 社**を軸に採用
3. 採用外のサプライヤーが1社以上いれば、末尾に `"Others"` 軸を追加
4. 各案について、`allocated_qty` を合計で割った比率を計算。採用外サプライヤーの分は `"Others"` に合算
5. **その案が使っていないサプライヤーの share は `0.0`**（キーを欠落させず、明示的に 0.0 を入れること）

#### 実装上の注意

- 同じ `supplier_id` が1つの案の中に複数レコードで現れる場合は**合算**する
- `allocated_qty` の合計が 0 の案があり得る（全サプライヤーが制約で除外された場合）。ゼロ除算を避け、その案の share はすべて 0.0 とすること
- タイブレーク：`unit_cost_usd` が同値のサプライヤーは `supplier_id` の辞書順で並べる（**結果が実行ごとに変わらないようにするため**）

### V3.3 `plot_parallel_coordinates()`

```python
def plot_parallel_coordinates(
    front: List[Dict],
    all_solutions: List[Dict],
    out: str,
    *,
    include_supplier_share: bool = True,
    top_k_suppliers: int = 5,
    title: Optional[str] = None,
) -> str:
```

#### 軸構成

**目的軸3本 ＋ share 軸（上位K社 + Others）**。

| 軸群 | 正規化 | 向き |
|---|---|---|
| Cost / Quality / Lead Time | min-max で [0,1] | **「上ほど良い」に統一**。cost と lead_time は `1 - normalized` で反転。quality はそのまま |
| share 軸 | **[0,1] 固定スケール**（min-max しない） | 良い／悪いの向きは持たない |

- share 軸を min-max しないのは、**軸間で比率を直接比較できるようにする**ため。min-max すると「A社の 5%」と「B社の 60%」が同じ高さに来てしまい、意味が壊れる
- **目的軸群と share 軸群の間に縦の区切り線**を入れる（`ax.axvline`、太めのグレー）。「上ほど良い」の規約が share 軸には適用されないため、混同を防ぐ
- **各軸に実値の目盛**を振る（正規化後の 0-1 ではなく）。軸ごとに min/max の実値をテキストで上下端に表示するのが簡便

#### 折れ線

- 1本 = 1配分案
- **Pareto 解**：rank 順のカラーマップ（例 `viridis`）、線幅やや太め、凡例に `#rank`
- **支配解**：淡いグレー、線幅細め、alpha 0.4

#### 実装上の注意

- min-max の分母が 0 になる場合（全案で cost が同一など）は、その軸の正規化値を一律 0.5 にする。**ゼロ除算で NaN を出さないこと**
- `include_supplier_share=False` のときは目的軸3本のみで描く。この経路も壊さないこと
- 軸が9本（目的3 + share 5 + Others 1）を超えると判読性が落ちる。`top_k_suppliers` のデフォルト 5 を守ること

---

## V4: CLI（`tools/plot_merit_order_suite.py` の `__main__`）

```bash
# サプライヤー CSV から一式生成
python -m tools.plot_merit_order_suite \
    --suppliers data/sample/<case>/supplier_master.csv \
    --required-qty 5000 \
    --out output/visualization/

# 内蔵サンプルで全図を一発生成（動作確認用）
python -m tools.plot_merit_order_suite --demo
```

### 引数

| 引数 | 必須 | 説明 |
|---|---|---|
| `--suppliers` | `--demo` 未指定時は必須 | サプライヤー CSV パス（`load_suppliers_from_csv()` に渡す） |
| `--required-qty` | 任意（既定 5000） | 需要量 |
| `--out` | 任意（既定 `output/visualization/`） | 出力ディレクトリ |
| `--demo` | 任意 | 内蔵サンプルデータで全図を生成 |
| `--horizon-weeks` | 任意（既定 12） | Regime Map のホライズン週数 |

### 生成される図（ファイル名固定）

| ファイル名 | 関数 |
|---|---|
| `merit_order_curve.png` | `plot_merit_order_curve()` |
| `merit_order_shift.png` | `plot_merit_order_shift()` |
| `regime_matrix.png` | `plot_regime_matrix()` |
| `regime_timeline.png` | `plot_regime_timeline()` |
| `pareto_scatter.png` | `plot_pareto_scatter()` |
| `parallel_coordinates.png` | `plot_parallel_coordinates()` |

### 出力先

`output/visualization/`。`.gitignore` の `output/` 配下なのでコミットされない。**ディレクトリが無い場合は `os.makedirs(..., exist_ok=True)` で作ること。**

### デモデータの作り方（**決定的に**）

`--demo` は `merit_order.py` の `__main__` ブロックにあるサンプルサプライヤーを流用してよい。

- **Regime Map 用**：`horizon_weeks` 週分の merit order result を作る。需要量を週ごとに変化させるが、**乱数を使わないこと**。例：

```python
required = 5000 + 300 * ((w % 5) - 2)   # 4400, 4700, 5000, 5300, 5600 の循環
```

- **Merit Order shift 用**：`before` は既定の為替、`after` は一部サプライヤーの `exchange_rate` を変えたものを使い、順位が実際に入れ替わるように作ること（入れ替わらないと図の意味がない）
- **Pareto 用**：異なる制約（制約なし / 品質閾値 / リードタイム上限 など）で3〜5案を作る

---

## テスト仕様（`tests/test_merit_order_plot.py` — 新規、9件）

`tests/test_allocation_plot.py` と**同じ方式**。「例外なく画像が生成されること」を固定し、**見た目は人手 QA** とする。画像比較はしない（脆いため）。

```python
# -*- coding: utf-8 -*-
"""
tests/test_merit_order_plot.py — 可視化のスモークテスト（Phase 3）
================================================================
tools/plot_merit_order_suite の各描画関数が例外なく画像を生成することを固定する。
（matplotlib Agg。中身の見た目は人手 QA。ここは "コード経路が壊れていない" ことの網。）
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import matplotlib
matplotlib.use("Agg")


def _nonempty(p):
    return os.path.exists(p) and os.path.getsize(p) > 1000
```

### スモークテスト（7件）

1. `test_plot_merit_order_curve`
2. `test_plot_merit_order_curve_unmet_demand` — `required_qty` が総供給量を超えるケースで λ 注記なしでも例外にならないこと（§V1.1 実装注意②）
3. `test_plot_merit_order_shift`
4. `test_plot_regime_matrix`
5. `test_plot_regime_timeline`
6. `test_plot_pareto_scatter`
7. `test_plot_parallel_coordinates` — `include_supplier_share=True` で生成されること

### 軸構築ロジックテスト（2件）— **画像ではなく戻り値を検証**

8. `test_build_supplier_share_axes_union_and_order`
   - 登場サプライヤーの異なる複数案を与える
   - 軸が**和集合**から作られること
   - 軸が **Merit Order 順（単価昇順）** に並ぶこと
   - ある案が使っていないサプライヤーの share が `0.0` であること（キーが存在すること）

9. `test_build_supplier_share_axes_others_bucket`
   - `top_k` を超えるサプライヤーが `"Others"` に集約されること
   - 各案の share 合計が `1.0`（浮動小数の誤差を考慮し `pytest.approx`）であること

### CLI テスト

CLI は `--demo` 経路をスモークで通すのが望ましいが、**サブプロセス起動はテストを遅くするので、`main()` を直接呼ぶ形にすること**。`argparse` のパース関数と実処理を分けておくと呼びやすい。

上記7のスモークテストが CLI 経由の全図生成を兼ねる形でもよい（その場合は9件のまま）。

### 合計

| | 件数 |
|---|---|
| 既存 | 325 |
| `tests/test_merit_order_plot.py`（新規） | 9 |
| `tests/test_pareto_front.py`（追記） | 4 |
| **合計** | **338 全PASS** |

---

## 実装チェックリスト

### `wom/visualization/pareto_front.py`（追加のみ）

- [ ] `__init__` に `self._granularity = "record"` / `self._lead_time_metric = "weighted_avg"` を追加
- [ ] `from_merit_order_results()` クラスメソッド追加（不正な `lead_time_metric` は `ValueError`）
- [ ] `compute_plan_objectives()` 追加（`total_cost` / `average_quality` / `average_lead_time` を再計算せず流用）
- [ ] `compute_pareto_front()` に粒度分岐を追加（支配判定・ソート・rank採番のロジックは変更しない）
- [ ] `compute_all_solutions()` 追加
- [ ] docstring 更新
- [ ] **既存メソッドのシグネチャ・既定挙動が一切変わっていないことを確認**

### `tools/plot_merit_order_suite.py`（新規）

- [ ] `matplotlib.use("Agg")` を pyplot import の前に置いた
- [ ] `plot_merit_order_curve()` — 需要線をまたぐブロックの2分割、λ算出、供給不足時の注記
- [ ] `plot_merit_order_shift()` — 2曲線重畳、順位入替検出、new/dropped の扱い
- [ ] `plot_regime_matrix()` — 文字列ラベルからの集計、dominant セル強調
- [ ] `plot_regime_timeline()` — **`risk_weeks` の 1始まり → X座標は `-1`**、Y軸 `[0,10]` 固定
- [ ] `build_supplier_share_axes()` — 和集合 / Merit Order 順 / top_k / Others / 未使用は 0.0 / タイブレークは supplier_id 辞書順
- [ ] `plot_pareto_scatter()` — 2パネル、Front★＋線分、支配解グレー、tradeoff 注記
- [ ] `plot_parallel_coordinates()` — 目的軸は反転して「上ほど良い」、share 軸は [0,1] 固定、区切り線、実値目盛
- [ ] CLI（`--suppliers` / `--required-qty` / `--out` / `--demo` / `--horizon-weeks`）
- [ ] 出力ディレクトリの `os.makedirs(exist_ok=True)`
- [ ] **全関数が出力パスを返す**
- [ ] **全関数が `plt.close(fig)` している**
- [ ] **図中のテキストがすべて英語**（日本語が1文字も無いこと）
- [ ] **乱数を使っていない**

### `tests/`

- [ ] `tests/test_merit_order_plot.py` 新規作成（9テスト）
- [ ] `tests/test_pareto_front.py` に `TestPlanGranularity` を追記（4テスト）
- [ ] **既存 `tests/test_pareto_front.py` の8テストが無変更であること**
- [ ] 既存 325 テストが全 PASS（回帰なし）

### テスト実行コマンド

```bash
# 新規分
python -m pytest tests/test_merit_order_plot.py -v
python -m pytest tests/test_pareto_front.py -v

# 全体回帰
python -m pytest tests/ -q

# golden（本 Phase は Planning Engine に触れないので不変のはず）
python -m pytest tests/test_golden.py -v
```

---

## 実装後の確認事項

### 1. 依存の確認

```bash
# plotly 等が紛れ込んでいないこと
grep -rn "plotly\|bokeh\|dash\|streamlit\|seaborn" tools/plot_merit_order_suite.py wom/visualization/
# → 何も出ないこと
```

### 2. 日本語混入の確認

```bash
# 図に渡す文字列に日本語が無いこと（コメント・docstring は可）
python - <<'EOF'
import re, io
src = io.open("tools/plot_merit_order_suite.py", encoding="utf-8").read()
# 文字列リテラルのうち、set_title/set_xlabel/set_ylabel/legend/text/annotate に渡るもの
hits = re.findall(r'(?:set_title|set_xlabel|set_ylabel|set_label|legend|\.text|\.annotate)\([^)]*[぀-ヿ一-鿿][^)]*\)', src)
print("NG:", hits if hits else "none")
EOF
```

### 3. 目視確認（大杉さんへ提出するもの）

```bash
python -m tools.plot_merit_order_suite --demo
# → output/visualization/ に6枚のPNGが生成される
```

以下を目視で確認する（**これは自動テストの対象外。人手 QA**）：

- [ ] `merit_order_curve.png` — 需要線の左右で濃淡が分かれ、λ の水平線と注記が出ている
- [ ] `merit_order_shift.png` — 2本の階段が重なり、順位が入れ替わったサプライヤーが注記されている
- [ ] `regime_matrix.png` — 3×3 に戦略名と週数が入り、dominant セルが太枠
- [ ] `regime_timeline.png` — risk/opportunity の背景帯が**正しい週位置**に出ている（1週ずれていないか特に注意）
- [ ] `pareto_scatter.png` — Pareto 点が2点以上あり（1点に収束していない）、★と線分が出ている
- [ ] `parallel_coordinates.png` — share 軸が出ており、区切り線で目的軸群と分かれている。折れ線の形状で戦略の違いが読める
- [ ] **すべての図に日本語の豆腐（□）が無い**

---

## Phase 4 への進出条件

以下を全て満たしたら Phase 4（生産配分の利益地形図・N市場化）の設計に進む。

1. ✅ 新規 13 テスト全て PASS
2. ✅ 既存 325 テスト全て PASS（`tests/test_pareto_front.py` の既存8件は無変更）
3. ✅ golden 12ケース不変
4. ✅ `--demo` で6枚の図が生成され、上記の目視確認を通過
5. ✅ 大杉さんの最終確認
6. ✅ Code君の実装完了報告

---

## 補足・参考

### 参照すべき既存実装

| 目的 | 参照先 |
|---|---|
| matplotlib の書き方・慣行 | `tools/plot_allocation_map.py` |
| 可視化のスモークテストの書き方 | `tests/test_allocation_plot.py` |
| 入力となる Dict の構造 | `wom/visualization/merit_order.py` `calculate_merit_order()` の docstring / `regime_map.py` `classify_horizon()` の docstring |

### 本 Phase のスコープ外（Phase 4 送り）

以下は**実装しないこと**。混入するとスコープが崩れる。

- レジーム地図の軸を外部環境パラメータ空間（USD/JPY × 関税率）へ差し替えること
- `ask_global_allocation`（`wom/allocation/`）への接続
- 階層化三角図（Hierarchical Triangulation）
- N市場（N≥4）への拡張
- 遷移行列ヒートマップ（**R2 見送り確定**。標本不足のため。§設計文書 4.3 参照）
- GUI（tkinter）への組込

### エラーメッセージの設計思想

Phase 1 と同じく、以下の形式に統一する。

```
❌ [エラータイプ]: [具体的な問題]
```

例：
```
❌ ValueError: lead_time_metric must be 'weighted_avg' or 'max', got 'median'
❌ ValueError: solutions are not plan-granularity (missing 'recommended_allocation')
```

---

## Git コミット情報

**git 操作は大杉さんが Windows 側ターミナルで実施する**（CLAUDE.md L267：Linux bash マウント経由の `git add` / `git commit` はファイル切り捨ての恐れがあるため禁止）。Code君は実装とテストまでを行い、コミットメッセージ案を提示すること。

### コミットメッセージ形式

```
Phase 3: Merit Order / Regime Map / Pareto Front の可視化層

- V0: pareto_front.py に配分案（plan）粒度を追加
  - from_merit_order_results() / compute_plan_objectives()
  - compute_all_solutions()
  - lead_time_metric="weighted_avg"(既定) | "max"
  - 既存の record 粒度は挙動不変（既存8テスト無変更でPASS）

- V1: メリットオーダー曲線
  - plot_merit_order_curve()（需要線・シャドープライスλ・部分供給の2分割）
  - plot_merit_order_shift()（Before/After 重畳・順位入替の検出）

- V2: Regime Map
  - plot_regime_matrix()（3×3・戦略名・dominant強調）
  - plot_regime_timeline()（regime_score 時系列・risk/opportunity 帯）

- V3: Pareto Front + 平行座標
  - plot_pareto_scatter()（目的空間2パネル・トレードオフ注記）
  - build_supplier_share_axes()（和集合＋Merit Order順＋上位K社＋Others）
  - plot_parallel_coordinates()（目的軸は「上ほど良い」に統一、share軸は[0,1]固定）

- V4: CLI tools/plot_merit_order_suite.py（--suppliers / --demo）

制約: matplotlib のみ（Web系GUIは情報セキュリティ上不採用）、
      新規依存なし、図中テキストは全て英語、禁足コア無変更。

新規テスト: 13個（test_merit_order_plot.py 9 + test_pareto_front.py 4）
既存テスト: 325個（全て PASS、回帰なし）
計: 338個テスト全て PASS

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EEihXkBiSxhPNk83Uw6CKB
```

---

**Request Letter 完成日**: 2026年9月6日
**設計責任**: Claude君
**レビュー承認**: 大杉さん（R1/R3/R4 確定、R2 見送り確定 — 2026-09-06）
**実装責任**: Code君
