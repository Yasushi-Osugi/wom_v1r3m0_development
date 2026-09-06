# Phase 3 実装設計 — Merit Order / Regime Map / Pareto Front の可視化層

**作成日**: 2026年9月6日
**設計責任**: Claude君
**対象**: 大杉さんレビュー → Code君実装
**優先度**: MEDIUM（2週間）
**ブランチ**: `wom-v1r4m0`

---

## 1. Phase 3 全体像

### 1.1 位置づけ — 「Phase」二系統の整理（重要・恒久記録）

WOM には現在、**独立した二つの Phase 系統**が並走している。過去のセッションでこの二つを取り違えた事故があったため、正典としてここに固定する。

#### A系統：`ask_global_allocation` ＝ 生産配分の利益地形図（v1r3m0）

| | 内容 | 状態 |
|---|---|---|
| Phase 1 | コアエンジン（`wom/allocation/`：`transmission.py` / `cost_block.py` / `grid.py` / `analytics.py`）— 3市場 231格子点の全数評価 | ✅ 完了 |
| Phase 2 | 可視化（`tools/plot_allocation_map.py`）— 直角三角形・利益等高線・尾根線・最適★・FXB=1.0線 | ✅ 完了 |
| Phase 3 | `AllocationMapPanel` GUI 組込 | ❌ 未対応 |

対象問題は **年次の「どの市場に何個供給するか」**。CLAUDE.md L1292-1321 参照。

#### B系統：Merit Order 分析（v1r4m0、`wom/visualization/`）

| | 内容 | 状態 |
|---|---|---|
| Phase 1 | `merit_order.py`（`MeritOrderAnalyzer`）＋エラーハンドリング強化 | ✅ 完了（28テスト） |
| Phase 2-A | `regime_map.py`（`RegimeMapAnalyzer`）3×3分類＋9戦略 | ✅ 完了（12テスト） |
| Phase 2-B | `pareto_front.py`（`ParetoFrontAnalyzer`）Cost/Quality/LT | ✅ 完了（8テスト） |
| **Phase 3** | **← 本文書。上記3モジュールの可視化層** | 設計中 |

対象問題は **週次の「どのサプライヤーからいくら調達するか」**。CLAUDE.md L1534-1602 参照。

**B系統の Phase 1/2 は分析ロジック（headless）のみで、描画コードを一切持たない。** 戻り値は全て Dict / JSON。本 Phase 3 はその描画層を追加するものである。

### 1.2 目的

`wom/visualization/` の3モジュールが返す Dict を、**matplotlib の静止画**として描画する。分析ロジックは変更しない（唯一の例外が §2 の Pareto cost 定義補正）。

### 1.3 スコープ外（Phase 4 へ送るもの）

以下は本 Phase 3 に**含めない**。混入を防ぐため明記する。

- レジーム地図の軸を、外部環境パラメータ空間（USD/JPY × 関税率）へ差し替えること
- A系統（`ask_global_allocation`）の生産配分データへの接続
- 階層化三角図（Hierarchical Triangulation）
- N市場（N≥4）への拡張

Phase 3 はあくまで **B系統の既存出力をそのまま描く**。Phase 4 で対象問題を A系統（市場配分）へ載せ替える。

### 1.4 構成

```
Merit Order 分析（Phase 1、済）
    ↓ result Dict
    ├─→ Phase 3-A: メリットオーダー曲線（階段図＋需要線＋λ）
    ↓
Regime Map 分析（Phase 2-A、済）
    ↓ horizon Dict
    ├─→ Phase 3-B: 3×3ヒートマップ / ホライズン時系列 / 遷移行列
    ↓
Pareto Front 分析（Phase 2-B、済）
    ↓ ※ Phase 3-0 で cost 定義を補正してから
    └─→ Phase 3-C: 目的空間散布図 ＋ 平行座標プロット
```

### 1.5 納期

| Week | Task | Deliverable |
|------|------|-------------|
| **Week 6-1** | Phase 3-0（Pareto cost 補正）＋ Phase 3-A | cost 補正 API + 4テスト / Merit Order 曲線 2関数 + 2テスト |
| **Week 6-2** | Phase 3-B ＋ Phase 3-C | Regime Map 3関数 + 3テスト / Pareto 2関数 + 2テスト |
| **Week 7** | CLI・統合テスト・ドキュメント | `tools/plot_merit_order_suite.py` CLI + 1テスト / 日本語仕様書 |

---

## 2. Phase 3-0：Pareto Front cost 定義の補正（**前提作業**）

Phase 3-C は、この補正なしには成立しない。最初に片付ける。

### 2.1 現状の問題（CLAUDE.md L1554 / L1590 の既知事項）

`pareto_front.py` の現状：

```python
def compute_allocation_objectives(self, allocation: Dict) -> Dict:
    allocated_qty = allocation.get("allocated_qty", 0)
    unit_cost = allocation.get("unit_cost_usd", ...)
    cost = allocated_qty * unit_cost      # ← 総額
```

`allocations` の1要素は **サプライヤー1社分の配分レコード**。したがって：

- 配分量が小さいレコードほど総コストが小さく、Pareto 上で無条件に有利
- 複数週・複数サプライヤーの配分レコードをフラットに集約すると、「小ロット・高品質・短納期」の1レコードが他の全レコードを支配し、**Pareto Front が1点に収束する**（実データで確認済み）

バグではなく定義上の帰結である。

### 2.2 なぜ「単価に変える」だけでは不十分か

補正候補1（cost を単価そのものにする）は規模非依存にはなるが、**Phase 3-C の平行座標プロットが描けない**。

平行座標は「複数の**配分案**を1本ずつの折れ線として重ね、戦略クラスタを塊として見る」図である。1本の線は1つの意思決定案でなければならない。サプライヤー単体を線にしても、そこに現れるのはサプライヤーの素性であって戦略ではない。

### 2.3 採用案：粒度の変更（補正候補2）＋ 後方互換の維持

**1解 = 1配分案**（required_qty を満たすサプライヤー配分のセット ＝ `calculate_merit_order()` 1回分の結果）とする。

| 目的軸 | 定義 | 出典 |
|---|---|---|
| `cost` | Σ(allocated_qty × unit_cost_usd) | merit_order result の `total_cost` |
| `quality` | 数量加重平均（0-100） | 同 `average_quality` |
| `lead_time` | 数量加重平均（days） | 同 `average_lead_time` |

**全案が同じ required_qty を満たすため、総額での比較が意味を持つようになる。** 既存の `average_quality` / `average_lead_time` は `allocate_demand()` が既に数量加重で算出済みなので、新規計算は不要。

#### API（追加のみ。既存シグネチャは変更しない）

```python
class ParetoFrontAnalyzer:

    # --- 既存（無変更、既存8テストはこの経路を通る） ---
    def __init__(self, allocations: List[Dict], objectives=None): ...
    def compute_allocation_objectives(self, allocation: Dict) -> Dict: ...
    def compute_pareto_front(self) -> List[Dict]: ...

    # --- 新規追加 ---
    @classmethod
    def from_merit_order_results(
        cls,
        results: List[Dict],
        *,
        lead_time_metric: str = "weighted_avg",   # "weighted_avg" | "max"
    ) -> "ParetoFrontAnalyzer":
        """calculate_merit_order() の結果リストから、
        「1配分案 = 1解」の粒度で Analyzer を構築する。

        Args:
            results: [calculate_merit_order(...), ...]（各要素が1つの配分案）
            lead_time_metric: lead_time 目的の算出方法（§2.4）

        Returns:
            granularity="plan" が設定された ParetoFrontAnalyzer
        """

    def compute_plan_objectives(self, result: Dict) -> Dict:
        """merit_order result 1件 → {"cost", "quality", "lead_time"}"""
```

`compute_pareto_front()` は内部で `self._granularity`（`"record"` | `"plan"`）を見て
`compute_allocation_objectives()` / `compute_plan_objectives()` を切り替える。
**デフォルトは従来通り `"record"`**（既存8テストの挙動を1文字も変えない）。

戻り値の `"allocation"` キーには、plan 粒度では merit_order result 全体（`recommended_allocation` を含む）を格納する。平行座標がサプライヤー内訳を参照できるようにするため。

### 2.4 lead_time の定義（**R1 確定：2026-09-06 大杉さん承認**）

配分案「全体」として見れば、**最後のサプライヤーが届くまで計画は完了しない**ので、binding な指標は `max(lead_time_days)` である。一方、既存実装と Phase 2 設計書は数量加重平均を採っている。

**決定：数量加重平均をデフォルトとして維持し、`lead_time_metric="max"` をオプションで用意する。**

- `lead_time_metric="weighted_avg"`（デフォルト）: `average_lead_time` をそのまま使う。既存挙動・回帰値に一切影響しない
- `lead_time_metric="max"`: `recommended_allocation` 中の `lead_time_days` の最大値を採る。「計画完了までの binding な時間」を見たい場合に使う

デフォルトを変えないことで、既存テストと Phase 2 の回帰値を保護する。

---

## 3. Phase 3-A：メリットオーダー曲線

### 3.1 図の仕様

電力の merit order curve と同じ形式。

- **X軸**: 累積供給量（cumulative supply, units）
- **Y軸**: 単価（unit cost, USD/unit）
- **各サプライヤー** = 幅 `max_supply` ／ 高さ `unit_cost_usd` の矩形ブロック。merit order（単価昇順）に左から積む
- **縦線**: `required_qty`（需要線、破線）
- **交点の高さ** = 限界サプライヤーの単価 = **シャドープライス λ**。水平補助線＋テキスト注記で明示
- **塗り分け**: 需要線の左側（実際に供給される組合せ）は濃色、右側は淡色
- **ラベル**: 各ブロックに supplier_name（幅が足りない場合は supplier_id）

入力は `calculate_merit_order()` の戻り値をそのまま使う。必要フィールドは既に揃っている：

```python
result["merit_order"]      # [{rank, supplier_id, supplier_name, unit_cost_usd,
                           #   max_supply, cumulative_supply_from_rank_1,
                           #   quality_score, lead_time_days}, ...]
result["required_qty"]
result["week"]
```

**`cumulative_supply_from_rank_1` が既に入っているので、描画側で累積を計算し直す必要はない。**

### 3.2 Before/After 重ね描き

為替・関税の変化でサプライヤーの順位が入れ替わる様子を、2本の階段として重ねる。

- Before を細線グレー、After を実線カラーで重畳
- 順位が入れ替わったサプライヤーを矢印＋注記でマーク
- 凡例に λ_before / λ_after の値を表示
- **需要線（`required_qty` の垂直線）を引く**（rev.4 で追記）。単独図（§3.1）と同じ黒破線にし、2枚を並べたときに同じ意味の線だと分かるようにする。before / after で `required_qty` が異なる場合はそれぞれの色で2本引く
- **需要線と各階段の交点 `(required_qty, λ)` に丸マーカーを打つ**（rev.4 で追記）。凡例には載せない

**需要線は必須である。** λ は「需要線と階段曲線の交点の高さ」として定義されるため、需要線が無いと2本の階段と2本の水平線が別々に置かれているだけに見え、**なぜ λ がその値になるのかが図から読み取れない**。初版ではこの要件を書き漏らし、実装後の目視 QA で判明した（`requests/request_fix_phase3_plot_readability.md` F1）。

これは chatlog の「Before/After を重ねれば、どの市場がどの市場を追い抜いたかがそのまま読める」に対応する図であり、**Phase 4 ①（メリットオーダー曲線の市場配分への適用）の土台**になる。横軸を「市場×ルート」に差し替えるだけで同じ図が使える。

### 3.3 関数シグネチャ

```python
def plot_merit_order_curve(
    result: Dict,
    out: str,
    *,
    title: Optional[str] = None,
    annotate_lambda: bool = True,
) -> str:
    """メリットオーダー曲線を描画し、保存先パスを返す"""

def plot_merit_order_shift(
    before: Dict,
    after: Dict,
    out: str,
    *,
    labels: Tuple[str, str] = ("Before", "After"),
) -> str:
    """2シナリオのメリットオーダーを重ね描きし、保存先パスを返す"""
```

---

## 4. Phase 3-B：Regime Map

入力は `RegimeMapAnalyzer.classify_horizon()` の戻り値。

### 4.1 3×3 ヒートマップ

- **行** = Supply Tightness（上から Tight / Balanced / Surplus）
- **列** = Demand Level（左から Low / Medium / High）
- **セル色** = そのセルに落ちた週数（`week_by_week` を集計）
- **セル内テキスト** = 戦略名（`REGIME_STRATEGIES` の値）＋ 週数。**単複を表記に反映すること**（`0 weeks` / `1 week` / `9 weeks`。rev.4 で追記）
- **行ラベル右側** に "Risk Mode" / "Balanced" / "Opportunity"（Phase 2 設計書 §2.1 の図と同じ）
- `dominant_regime` のセルを太枠で強調

### 4.2 ホライズン時系列

- **X** = 週（`week` ラベル）
- **Y** = `regime_score` の2本折れ線：`demand_pressure` / `supply_risk`（0-10）
- **Y軸レンジは `[0, 10]` に固定する**。週ごとに軸が伸縮すると週間比較ができなくなるため
- **背景帯**: `risk_weeks` を赤系、`opportunity_weeks` を緑系でハイライト
- **下部帯**: 各週の `recommended_strategy` を色分けした帯として表示（戦略の切り替わりが時間軸で読める）
- **上段の凡例は軸の外（上）に横並びで置く**（rev.4 で追記）。Y軸を `[0, 10]` に固定するため上部に逃がす余白が作れず、`demand_pressure` は下端付近、`supply_risk` は中央帯を走るので、**軸の内側に安全な置き場所が存在しない**。軸外へ出すことで描画領域を消費せずに固定レンジを保てる

### 4.3 遷移行列（**R2 確定：2026-09-06 見送り**）

`transition_matrix` を行列ヒートマップとして描画（行＝遷移元レジーム、列＝遷移先、値＝確率）。

#### 4.3.1 この図の目的とメリット

読み取れるのは **レジームの粘着性（persistence）** である。

- **対角成分が高い** = 市場環境が同じレジームに留まりやすい → 戦略を頻繁に切り替える必要がない。固定契約・単一戦略が正当化される
- **対角成分が低く非対角に散る** = レジームがめまぐるしく変わる → 単一戦略の固定は危険。`dual_source` のようなヘッジ戦略のコストが正当化される

つまり **「戦略の切り替えコストを払うべきか」** の判断材料になる。これは §4.1 の3×3ヒートマップ（どのセルに何週落ちたか＝分布）にも §4.2 のタイムライン（いつ切り替わったか＝系列）にも現れない、独立した情報である。

#### 4.3.2 ただし、現状のホライズン長では統計的に成立しない

`classify_horizon(horizon_weeks=12)` が想定する12週では、**遷移サンプルはわずか11件**しかない。一方レジームの組は 9×9 = 81 通り。

`transition_matrix` は `cnt / total` を遷移元ごとに計算しているため、各遷移元が1〜4回しか現れない12週では、確率値のほとんどが **1.0 か 0.5 という退化した値**になる。これを「遷移確率」として提示するのは、読み手に実態以上の統計的根拠があるかのような誤解を与える。

#### 4.3.3 決定：Phase 3 では実装を見送る

**大杉さんの判断により、Phase 3 では実装しない（2026-09-06 確定）。** 理由は上記の標本不足であり、図そのものの価値を否定するものではない。

12週程度であれば、遷移は §4.2 のタイムラインで**目で直接追える**（11回の切り替わりを行列に潰す必然性がない）。行列表現が意味を持つのは、数十〜数百週の系列がある場合である。

将来、長期ホライズン（例：`soysauce-jpy-2027` の156週）で使う際には、以下の条件付きで実装するのが妥当：

- `horizon_weeks >= 52` のときのみ描画する（それ未満は警告を出して skip）
- 値は **確率ではなく実測回数（counts）** で表示する。標本数が読み手に見えるようにするため
- 対角成分を強調表示し、粘着性が一目で分かるようにする

この見送りにより、Phase 3 の描画関数は **6関数**、新規テストは **13件**（全体 338 件）となる。

### 4.4 関数シグネチャ

```python
def plot_regime_matrix(horizon: Dict, out: str, *, title=None) -> str
def plot_regime_timeline(horizon: Dict, out: str, *, title=None) -> str

# plot_regime_transitions() は R2 確定により Phase 3 では実装しない（§4.3.3）
```

---

## 5. Phase 3-C：Pareto Front ＋ 平行座標

**前提**: §2 の cost 定義補正が完了していること。

### 5.1 目的空間の散布図

2枚並びのパネル：

| パネル | X | Y | 点の色 |
|---|---|---|---|
| 左 | cost | quality | lead_time（colormap） |
| 右 | cost | lead_time | quality（colormap） |

- **Pareto Front 上の解**: ★マーカー ＋ cost 昇順で線分接続
- **支配されている解**: 淡いグレーの小さな○
- **各 Pareto 点に rank ラベル**
- **トレードオフ注記**: `compute_tradeoff_ratios()` の隣接ランク間比率を線分の中点に付記（例 `+5.0% cost / +3.2 quality`）

### 5.2 平行座標プロット（**R3 確定：2026-09-06 大杉さん承認**）

**軸構成 = 目的軸3本 ＋ サプライヤー配分比率軸 K本＋Others。** 配分比率を軸に含める（戦略クラスタの判読性を優先）。

#### 5.2.1 「案ごとに登場するサプライヤーが違う」問題の解法

軸が定まらないという懸念に対し、以下の手順で**全案共通の軸集合**を決める。

1. 全配分案の **和集合（union）** からサプライヤーを収集する。これにより軸の候補集合が全案共通になる
2. **Merit Order 順（`unit_cost_usd` 昇順）** に並べ、上位 **K 社**を軸として採用（K のデフォルト = 5）
3. K 社に入らなかった分は `Others` 軸1本に集約する
4. ある配分案がそのサプライヤーを使っていない場合、share = 0。これは**欠測ではなく「この案はこのサプライヤーを使わない」という意味を持つ値**なので、そのまま最下点にプロットする

**軸を Merit Order 順に並べる副次効果**として、折れ線の形状がそのまま戦略の性格を表す — 左（安い側）に山があれば低コスト重視型、右（高い側）に山があれば品質・納期重視型。戦略クラスタが**視覚的な形状差**として現れる。

K を 5 に抑えるのは、平行座標は軸が8本を超えると急速に判読性を失うため（目的3 + share 5 + Others 1 = 9本が上限の目安）。

#### 5.2.2 スケールと向き

| 軸群 | 正規化 | 向き |
|---|---|---|
| 目的軸（Cost / Quality / Lead Time） | min-max で [0,1] | **「上ほど良い」に統一**。cost と lead_time は反転（`1 - normalized`）、quality はそのまま |
| share 軸（上位K社 + Others） | **[0,1] 固定スケール**（min-max しない） | 良い／悪いの向きは持たない |

- share 軸を min-max しないのは、**軸間で比率を直接比較できるようにする**ため（min-max すると「A社の 5% とB社の 60%」が同じ高さに来てしまう）
- **2群の間に縦の区切り線を入れ、視覚的に分離する**。「上ほど良い」の規約が share 軸には適用されないため、混同を防ぐ
- **軸ラベルは実値の目盛**を振る（正規化後の 0-1 ではなく）

#### 5.2.3 折れ線

1本 = 1配分案。Pareto 解は rank 順のカラーマップ、支配解は淡いグレー。

これが chatlog の「上位 K 個の配分ベクトルを平行座標で重ねると、戦略クラスタが塊として見えてくる」に対応する。**Phase 4 ③ の土台。**

#### 5.2.4 軸構築ヘルパー（テスト対象）

軸の決定は描画から独立した純粋なロジックなので、単独の関数に切り出して単体テストする。

```python
def build_supplier_share_axes(
    solutions: List[Dict],
    top_k: int = 5,
) -> Tuple[List[str], List[Dict[str, float]]]:
    """全案の和集合から Merit Order 順に上位 K 社の軸を決め、
    各案の配分比率を返す。

    Returns:
        (axis_labels, shares)
        axis_labels: ["SUP_002", "SUP_001", ..., "Others"]（Merit Order 順）
        shares: [{axis_label: 比率}, ...]（案ごと、合計 1.0）
    """
```

### 5.3 関数シグネチャ

```python
def plot_pareto_scatter(
    front: List[Dict],
    all_solutions: List[Dict],
    tradeoffs: Optional[Dict] = None,
    *,
    out: str,
    title: Optional[str] = None,
) -> str

def plot_parallel_coordinates(
    front: List[Dict],
    all_solutions: List[Dict],
    *,
    out: str,
    include_supplier_share: bool = True,   # R3 確定：デフォルト True
    top_k_suppliers: int = 5,
    title: Optional[str] = None,
) -> str

def build_supplier_share_axes(
    solutions: List[Dict],
    top_k: int = 5,
) -> Tuple[List[str], List[Dict[str, float]]]
```

---

## 6. 共通仕様

### 6.1 技術制約（**厳守**）

1. **matplotlib のみを使用する。** plotly / bokeh / dash / streamlit その他 Web 系 GUI ライブラリは、**情報セキュリティの観点から使用禁止**。WOM は企業の機密情報を扱い、スタンドアロンの Windows PC での運用を前提とする。
2. **新規依存パッケージを追加しない。** matplotlib / numpy / pandas は既に依存にある（CLAUDE.md L39）。
3. `matplotlib.use("Agg")` を `import matplotlib.pyplot` の**前**に置く（`tools/plot_allocation_map.py` L29-31 と同じ）。
4. **図中のラベル・タイトル・凡例は全て英語で書く。** `tools/plot_allocation_map.py` の既存慣行に合わせる。日本語フォントが未導入の Windows 環境で豆腐化するのを避けるため。日本語は本ドキュメントとソースコメントのみ。
5. **各描画関数は生成した出力パスを返す**（`plot_allocation_map.py` の慣行。テストが `_nonempty()` で検証できるようにするため）。
6. **凡例をデータの上に重ねないこと**（rev.4 で追記）。`loc` は図の構造から「構造的に必ず空く」場所を選ぶ（例：階段曲線は左端が最安なので `upper left` は必ず空く）。軸レンジが固定されていて軸内に安全な場所が無い図では、`bbox_to_anchor` で軸の外に出す。**軸レンジを緩めて凡例のための余白を作ることはしない** — 軸の固定は比較可能性のための要件であり、凡例配置より優先される。

### 6.2 ファイル配置

| 対象 | パス | 備考 |
|---|---|---|
| 描画関数（新規） | `tools/plot_merit_order_suite.py` | `tools/plot_allocation_map.py` と同じ層（**R4 確定：2026-09-06 大杉さん承認**） |
| 分析ロジック | `wom/visualization/merit_order.py` / `regime_map.py` | **無変更** |
| 分析ロジック | `wom/visualization/pareto_front.py` | §2 の cost 補正のみ（追加API、既存挙動は不変） |
| 出力先 | `output/visualization/` | `.gitignore` の `output/` 配下＝コミットされない |
| テスト | `tests/test_merit_order_plot.py`（新規） / `tests/test_pareto_front.py`（追記） | |

### 6.3 CLI

```bash
# サプライヤー CSV から一式生成
python -m tools.plot_merit_order_suite \
    --suppliers data/sample/<case>/supplier_master.csv \
    --required-qty 5000 \
    --out output/visualization/

# 内蔵サンプルで全図を一発生成（動作確認用）
python -m tools.plot_merit_order_suite --demo
```

### 6.4 禁足ルールとの関係

Planning Engine 保護対象コア（`backward_planner.py` / `forward_planner.py` / `plan_copy.py` / `plan_node.py` / `sc_tree.py` / `push_pull.py`）には**一切触れない**。golden 12ケースにも影響しない。本 Phase は `ask_global_allocation` と同じく Management 層の分析補助ツールである。

---

## 7. テスト計画

**既存 325 件に回帰なしを維持すること。**

### 7.1 スモークテスト（新規 `tests/test_merit_order_plot.py`、7件）

`tests/test_allocation_plot.py` と同じ方式。「例外なく画像が生成されること」を固定し、**見た目は人手 QA** とする（画像比較はしない — 脆いため）。

判定は既存の慣行に合わせ、ファイルが存在し 1000 バイト超であること。

1. `test_plot_merit_order_curve`
2. `test_plot_merit_order_curve_unmet_demand` — 供給不足時（λ 未定義）に例外にならないこと
3. `test_plot_merit_order_shift`
4. `test_plot_regime_matrix`
5. `test_plot_regime_timeline`
6. `test_plot_pareto_scatter`
7. `test_plot_parallel_coordinates` — share 軸込み（`include_supplier_share=True`）

### 7.2 平行座標の軸構築ロジックテスト（同ファイル、2件）

`build_supplier_share_axes()` は描画から独立した純粋関数なので、画像生成ではなく**戻り値を直接検証**する。

9. `test_build_supplier_share_axes_union_and_order`
   — 登場サプライヤーの異なる複数案を与えたとき、軸が**和集合**から作られ、**Merit Order 順（単価昇順）**に並ぶこと。案が使っていないサプライヤーの share が 0 になること
10. `test_build_supplier_share_axes_others_bucket`
   — `top_k` を超えるサプライヤーが `Others` に集約され、各案の share 合計が 1.0 になること

### 7.3 Pareto cost 補正のロジックテスト（`tests/test_pareto_front.py` に追記、4件）

**既存8件は1文字も変更しないこと。**

11. `test_plan_objectives_matches_merit_order_totals`
   — `compute_plan_objectives()` の返す cost/quality/lead_time が、merit_order result の `total_cost` / `average_quality` / `average_lead_time` と一致する
12. `test_pareto_front_from_plans_multiple_on_front`
   — 複数の配分案（異なる制約条件で生成）を与えたとき、**Pareto Front が1点に収束しない**ことを固定（§2.1 の症状に対する回帰テスト）
13. `test_pareto_front_record_granularity_backward_compat`
   — 従来の `__init__(allocations)` 経路が、補正前とまったく同じ結果を返すこと
14. `test_lead_time_metric_max_option`
   — `lead_time_metric="max"` で最大リードタイムが採られること

### 7.4 合計

| | 件数 |
|---|---|
| 既存 | 325 |
| `tests/test_merit_order_plot.py`（新規：7 スモーク + 2 ロジック） | 9 |
| `tests/test_pareto_front.py`（追記） | 4 |
| **Phase 3 完了時点** | **338 件 全PASS** |
| rev.4 の修正（`_weeks_label()`、`request_fix_phase3_plot_readability.md` F3） | 1 |
| **合計** | **339 件 全PASS** |

既存 325 件に回帰なしを維持すること。

rev.4 の F1（需要線）・F2（凡例配置）に対する自動テストは追加しない。描画関数は Figure ではなく出力パスを返す契約（§6.1-5）のため Axes の内部を検査できず、また体裁は自動判定になじまない。既存のスモークテストがコード経路を担保し、**見た目は人手 QA** とする（§7.1 と同じ立場）。

### 7.4 実行コマンド

```bash
python -m pytest tests/test_merit_order_plot.py -v
python -m pytest tests/test_pareto_front.py -v
python -m pytest tests/ -q          # 全体回帰
```

---

## 8. 実装スケジュール

| Timeline | Task | Owner | Deliverable |
|---|---|---|---|
| **Week 6-1** | Phase 3-0（Pareto cost 補正）＋ Phase 3-A（Merit Order 曲線） | Code君 | 新API + 4テスト / 2描画関数 + 2テスト |
| **Week 6-2** | 大杉さんレビュー | 大杉さん | λ 注記・Before/After の見え方の確認 |
| **Week 6-3** | Phase 3-B（Regime Map）＋ Phase 3-C（Pareto / 平行座標） | Code君 | 5描画関数 + 5テスト |
| **Week 6-4** | 大杉さんレビュー | 大杉さん | 戦略クラスタが読めるかの確認 |
| **Week 7** | CLI・統合・ドキュメント | 両者 | CLI + 1テスト / `docs/development/wom-v1r4m0_phase3_visualization.md` |

---

## 9. 成功基準

- [ ] `ParetoFrontAnalyzer.from_merit_order_results()` 実装、Pareto Front が1点に収束しないことを回帰テストで固定
- [ ] 既存 `pareto_front.py` の8テストが**無変更で**全PASS
- [ ] 6つの描画関数すべてがスモークテストPASS
- [ ] 平行座標に share 軸が入り、軸が全案共通（和集合・Merit Order 順）で構築されていること
- [ ] CLI `--demo` が全図（6枚）を一発生成
- [ ] リポジトリ全体 339件 全PASS（既存325件に回帰なし）
- [ ] `plot_merit_order_shift()` に需要線と交点マーカーがあり、λ が交点の高さとして読めること（rev.4）
- [ ] どの図でも凡例がデータに重なっていないこと。`plot_regime_timeline()` の Y軸が `[0, 10]` 固定のままであること（rev.4）
- [ ] golden 12ケース不変（本 Phase は Planning Engine に触れないため自明だが確認する）
- [ ] plotly 等の Web 系ライブラリを一切導入していないこと
- [ ] 図中のテキストが全て英語であること（豆腐化しないこと）
- [ ] 日本語ドキュメント完備
- [ ] 大杉さんの最終確認

---

## 10. レビュー事項と決定（大杉さん、2026-09-06）

| # | 箇所 | 内容 | 決定 |
|---|---|---|---|
| **R1** | §2.4 | 配分案全体の `lead_time` の定義 | ✅ **確定** — 数量加重平均をデフォルト維持、`lead_time_metric="max"` をオプションで提供 |
| **R2** | §4.3 | 遷移行列ヒートマップを Phase 3 に含めるか | ✅ **確定（見送り）** — 12週では遷移サンプル11件に対しレジームの組は81通りで、確率値が退化する（§4.3.2）。将来の長期ホライズンでの実装条件を §4.3.3 に記録 |
| **R3** | §5.2 | 平行座標の軸に主要サプライヤーの配分比率を含めるか | ✅ **確定（含める）** — 軸が定まらない問題は §5.2.1 の「和集合＋Merit Order 順＋上位K社＋Others」方式で解決 |
| **R4** | §6.2 | 描画関数の配置 | ✅ **確定** — `tools/plot_merit_order_suite.py` に置く |

---

## 11. Phase 4 への引き継ぎ

Phase 3 の各成果物が、Phase 4（生産配分の利益地形図・N市場化）でどの手法の土台になるかの対応。

| Phase 4 の手法 | Phase 3 の土台 | Phase 4 で必要になる差分 |
|---|---|---|
| ① メリットオーダー曲線 | §3 `plot_merit_order_curve` / `plot_merit_order_shift` | 横軸を「サプライヤー」から**「市場×供給ルート」の組**へ差し替え。縦軸を単価から**希少資源1単位あたりの限界利益**へ。能力制約線の交点が λ になる構造は同一 |
| ② レジーム地図 | §4 `plot_regime_matrix` | 軸を「需要レベル×供給タイト度」から**外部環境パラメータ空間（USD/JPY × 関税率）**へ差し替え。セルの塗り分けを「週数」から**「そこで最適となる配分パターン」**へ。市場数 N に依存しない主力手法 |
| ③ Pareto ＋ 平行座標 | §5 `plot_pareto_scatter` / `plot_parallel_coordinates` | 目的軸を Cost/Quality/LT から**利益 × ロバストネス（minimax）**へ。A系統 `analytics.py` の `robust_point` が素材として既にある |
| ④ 階層化三角図 | **A系統** `tools/plot_allocation_map.py` の三角図 | 既存三角図を「階層の1レベル」として再利用。束ね方自体の感度分析が必要 |

### 11.1 Phase 4 に向けた所見（chatlog への追記）

添付 chatlog では「5ケースのどれも自然に3ブロックに束ねられる多市場を持っていないため、④階層化三角図は出番がない」と結論していたが、**`data/sample/oil-global-2027` が該当する**（CLAUDE.md L453-613）。

- 日本／欧州／米州の**3市場体制が完成済み**（8 SKU）
- 各市場が Local / Import の2ルートを持つ
- 上位で「日本・欧州・米州」の三角図、各市場内で「Local / Import / (第3ルート)」に降りる — **階層化三角図の構造そのもの**
- 撹乱要因が市場ごとに異なる（地政学 / 労働争議 / 自然災害）ため、**束ね方の感度分析の題材としても適している**

Phase 4 の手法×ケースのマトリクスを作る際は、このケースを④の適用先として評価対象に加えることを推奨する。

---

**次のステップ**: `requests/Phase3_RequestLetter_to_CodeKun.md` に基づき Code君が実装

**改訂履歴**
- 2026-09-06 初版
- 2026-09-06 rev.2 — R1 / R3 / R4 を大杉さん承認により確定。R3 の軸構築方式（§5.2.1）とヘルパー関数（§5.2.4）を追記。R2 の目的・メリットと標本不足の分析（§4.3.1-4.3.3）を追記、判断保留
- 2026-09-06 rev.3 — **R2 を見送りで確定**（大杉さん判断）。描画関数 6・新規テスト 13・全体 338 件に確定。Request Letter 発行
- 2026-09-06 rev.4 — 実装完了（338件全PASS）後の目視 QA を反映。**§3.2 に需要線と交点マーカーの要件を追記**（初版の記述漏れ。λ の定義が図から読めなかった）。§4.1 に週数の単複表記、§4.2 に Y軸固定の明記と凡例を軸外へ出す指針、§6.1-6 に凡例配置の一般原則を追記。テスト 339 件。修正依頼は `requests/request_fix_phase3_plot_readability.md`
