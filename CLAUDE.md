# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## What is WOM

**WOM (Weekly Operation Model)** は週次PSI（Production/Sales/Inventory）を基本単位とするE2Eサプライチェーン計画・シミュレーションツール。Python \+ tkinter GUI。

- 起動: `python -m main`（GUIモード）/ `python -m main --cli`（ヘッドレス）  
- 現バージョン: v1r0m3（branch: `wom-v1r0m3`）  
- 適用事例: Japanese Rice SC（`data/sample/rice-japan-2027-2028/`）、iPhone Global SC（`data/sample/iphone-2027-2029/`）

---

## Commands

\# GUI起動（通常）

python \-m main

\# CLIシミュレーション

python \-m main \--cli \--start-week 2027-W01 \--num-weeks 156

\# テスト実行

python \-m pytest tests/ \-v

\# 単一テスト

python \-m pytest tests/test\_ppc\_vertical\_slice.py \-v

\# PPC CLIスタンドアロン

python \-m wom.ppc

依存: `pip install tkintermapview pandas numpy matplotlib openpyxl networkx pytest`

---

## Architecture

### 3層モデル

Physical Layer  ←→  Planning Layer  ←→  Management Layer

(実ノード/地図)      (SCTree \+ PSI)       (KPI / PPC / P\&L)

### Planning Engineの実行順序（`wom/gui/app.py: _run_planning_engine`）

1\. demand\_forecast.csv → 需要ロット生成（lot\_generator.py）

2\. sc\_tree\_master.csv → SCTree構築（sc\_tree\_builder.py）

3\. HOOK\_PRE\_PLAN（プラグイン処理: HarvestBatch等）

4\. BackwardPlanner.run(prod\_nm)  ← 需要をleaf\_outから逆伝播

5\. HOOK\_POST\_BACKWARD

6\. copy\_demand\_to\_supply()       ← psi4demand → psi4supply コピー

7\. HOOK\_POST\_COPY

8\. ForwardPlanner.run(prod\_nm)   ← 供給能力制約を適用、CO生成

9\. HOOK\_POST\_FORWARD / HOOK\_POST\_PLAN

10\. sc\_tree\_to\_planning\_df()     ← SCTree → DataFrame（KPI用）

11\. PPC engine自動実行

### コアデータ構造: PlanNode（`wom/model/plan_node.py`）

各ノードが保持するPSIバケット：

psi4demand\[week\_idx\]\[bucket\]  \# BackwardPlannerが書く

psi4supply\[week\_idx\]\[bucket\]  \# ForwardPlannerが書く

\# bucket定数

S  \= 0  \# Sales / 出荷

CO \= 1  \# Carry Over（繰越需要）

I  \= 2  \# Inventory（期末在庫）

P  \= 3  \# Purchase/Production（入荷計画）

### SCTree構造（`wom/model/sc_tree.py`）

InBound (supply side)          OutBound (demand side)

leaf\_in                        supply\_point (bridge/root)

  └─ MOM(tier=0)  ────────────▶  DAD(tier=0)

       └─ tier-1  ←─ Bridge ─▶     └─ DC

            └─ leaf\_in               └─ leaf\_out (sales channel, region必須)

**重要な設計上の注意：**

- 【v1r0m4で更新】以前は「OutBound DADノードは需要アンカー型ロット方式のため`psi4supply[w][I]`は常に0」としていたが、これは**Lot_ID identity-matching方式の導入（下記バグ修正履歴参照）により解消済み**。現在はOutBound DADノードでも`buffering_stock_flag=1`（is_decoupling）かつ`ss_days`が設定されていれば、実際に`psi4supply[w][I]`にSS_days分の安全在庫バッファが積み上がる（Cookie_Import: DC_Import_Bufferで実証済み）。
- 実際のバッファ在庫はInBound MOMノード・OutBound DADノードのどちらでも、is_decouplingノードなら`psi4supply[w][I]`に蓄積されうる（v1r0m4以降）。
- `Buffer Stock (DAD)` はWOMモデル用語として正しい（OutBound decoupling point）  
- GUIのBuffer Stockチャートは実装上MOMノードのデータを参照しているが、DADノードのIも今後表示対象に含める余地がある（v1r0m4時点では未対応、次回検討事項）。

### node\_type一覧

| node\_type | 側 | 役割 |
| :---- | :---- | :---- |
| `supply_point` | OutBound root | InBound/OutBound bridge |
| `dad` | OutBound | 倉庫・DC（Demand Anchored Decoupling point） |
| `leaf_out` | OutBound leaf | 販売チャネル（region必須） |
| `mom` | InBound root | 製造拠点・産地集荷センター（Mother of Manufacturing） |
| `leaf_in` | InBound leaf | 原材料・稲作田 |

### プラグインシステム（`wom/plugins/__init__.py`）

`HookBus`経由でPlanning Engineに割り込む：

HOOK\_PRE\_PLAN      \# sc\_tree構築後、計画ループ前

HOOK\_POST\_BACKWARD \# BackwardPlanner完了後

HOOK\_POST\_COPY     \# demand→supplyコピー後

HOOK\_POST\_FORWARD  \# ForwardPlanner完了後

HOOK\_POST\_PLAN     \# 全製品計画完了後

**組み込みプラグイン：**

- `HarvestBatchPlugin` — 収穫期バッチ生産（seasonal supply spike）  
- `HolidayCalendarPlugin` — 長期休暇の能力閉鎖・需要変動（holiday\_calendar.csv）  
- `CapacityOverridePlugin` — cap\_override.csvによる能力上書き  
- `DemandSmoothingPlugin` — 3週移動平均需要平準化
- `BufferingStockOptimizerPlugin`（v1r0m4〜、HOOK\_POST\_BACKWARD） — decouple\_optimizer\_config.csvでSKUごとにON/OFF、有効時はOutBoundのbuffering\_stock\_flag（is\_decoupling）をコスト最適・サービスレベル制約付きの配置に自動上書き（詳細は本ファイル下部「BufferingStockOptimizerPlugin」参照）

新プラグインは `plugin_base.py` の `WOMPlugin` を継承し、`ALL_BUILTIN_PLUGINS` に追加する。

### PPC（Profit Price Cost）エンジン（`wom/ppc/`）

Planning Engine完了後に自動実行（`_run_ppc_from_planning`）。

- 入力CSV: `ppc_market_price.csv`, `ppc_supplier_cost.csv`, `ppc_node_cost_rule.csv`  
- 計算: Revenue → COGS → Gross Profit → Profit Zone  
- 結果: GUIのPPCタブに表示

### Landed Cost（`wom/engine/landed_cost.py`）

関税・為替・輸送費のシナリオ比較エンジン（Phase 1実装済み）。

- `edge_cost_master.csv`: シナリオ別ルートコスト（Base/FreightUp/DriverShortage等）  
- `route_master.csv`: SKU×regionのルート割り当て  
- ManagementタブのTariff & FX パネルに表示  
- Phase 1制約: ルート間の按分は単純平均（出荷量加重はPhase 2）

---

## Master CSV スキーマ（モデルフォルダ必須ファイル）

| ファイル | 主キー | 用途 |
| :---- | :---- | :---- |
| `sku_master.csv` | sku\_id | 製品定義 |
| `demand_forecast.csv` | sku\_id, region, week | 週次需要予測 |
| `node_master.csv` | node\_id | ノード定義（lat/lon/node\_type） |
| `sc_tree_master.csv` | node\_name, product\_name | SCTree構造定義 |
| `capacity_plan.csv` | node\_id, sku\_id, week | 週次能力制約 |
| `lane_assignment.csv` | sku\_id, leaf\_out\_node | InBound割り当てルール |
| `node_cost_master.csv` | node\_id, sku\_id | ノード別コスト |
| `edge_cost_master.csv` | scenario, src\_region, dst\_region | 輸送コストシナリオ |
| `route_master.csv` | sku\_id, region | SKU×regionルート |
| `holiday_calendar.csv` | node\_id, week | 長期休暇カレンダー |
| `inventory_master.csv` | node\_id, sku\_id | 期初在庫 |

`node_master.csv`の`node_type`はWorldMapの`_MAP_NODE_STYLE`と整合させること：

- `procurement` → オレンジ（玄米保管・SP\_Kome等）  
- `mother_plant` → 紫（産地集荷センター等）  
- `sku_supplier` → 緑（稲作田・サプライヤー）  
- `region_dc` → 青（精米センター・DC）  
- `marketing` → 赤（小売チャネル）

`sc_tree_master.csv`の`node_type`はWOMモデル用語（`mom`/`dad`/`leaf_in`/`leaf_out`/`supply_point`）を使用。上記とは別体系。

---

## GUI構造（`wom/gui/app.py`）

約3900行の単一ファイル。主要パネルクラス：

| クラス | タブ | 役割 |
| :---- | :---- | :---- |
| `ChartPanel` | Charts | Buffer Stock/Harvest Input/Fill Rate等 |
| `WorldMapPanel` | World Map | tkintermapviewベースの地図（起動時初期タブ） |
| `NetworkPanel` | Network | NetworkXによるHammockグラフ |
| `ManagementPanel` | Management | KPI・PPC・Tariff\&FX |
| `PPCPanel` | PPC | Profit Zone可視化 |

**WorldMapPanel:** 起動時の初期タブ。`_render_nodes()`後に`self.after(200, self._fit_to_nodes)`でノード群にauto-zoom。`fit_bounding_box()`またはフォールバック`set_position()+set_zoom()`。

**Planning Engine完了後のフロー（`_on_planning_done`）：**

1. `NetworkPanel.load_planning_tree(sc_tree)`  
2. `ChartPanel.load_sc_tree(sc_tree)` → Buffer Stock/Harvest Inputチャートが有効化  
3. `ChartPanel.load(mgr)` → 既存チャートデータ更新  
4. `_run_ppc_from_planning(sc_tree)` → PPC自動実行

---

## v1r0m3の開発方針

- v1r0m2（branch: `wom-v1r0m2`）をベースラインとして保存済み  
- v1r0m3では **MOM Constrained Demand Allocation**（BackwardPlannerでのMOM cap_hardクリップ + CO前倒し）を実装  
- `data/sample/iphone-2027-2029/` を参照・拡充する  
- コード変更はすべて `wom-v1r0m3` ブランチで行い、GitHubへpushする  
- GitHub: `https://github.com/Yasushi-Osugi/wom_v1r0m0.git`

---

## 禁足ルール（Planning Engine 保護対象コア）― Anti-Degrade

**背景**: v1r0m3 の「MOM Constrained Demand Allocation」リファクタで、cap_soft の配線（sealer 呼出＋CSV列）が**意図せず外れ**、骨格だけ残って休眠した（＝承認済みリファクタの副作用）。この教訓から、Planning Engine のコアを**手続きルール（本節・soft）＋機械的な網（3層テスト・hard）の二重**で守る。設計根拠は `requests/operating-constraint-layer-request-letter.md` §11。

**保護対象コア（禁足対象ファイル）**:
- `wom/engine/backward_planner.py`
- `wom/engine/forward_planner.py`
- `wom/engine/plan_copy.py`
- `wom/model/plan_node.py`
- `wom/model/sc_tree.py`
- `wom/engine/push_pull.py`

**ルール（ゲート式＝「絶対に触るな」ではなく「認可＋テスト緑を条件に触る」）**:
1. **明示的な指示（Request Letter 参照）が無い限り、上記コアを改変しない。**
2. 改変する場合は、以下の **3層テストを必ず緑**にする（§11.1）:
   - **Unit**：望む挙動を固定値で assert（合成ツリー、CSV バイパス可）。例 `tests/test_capacity_soft_backward.py`。
   - **Integration**：CSV→**実ローダ**→ノードのデータ経路を実際に通す（`wom/engine/capacity_sealer.load_capacity_dataframe` 等）。例 `tests/test_capacity_soft.py`。← cap_soft 休眠の真因（欠けていた層）。
   - **E2E ゴールデン**：`tools/run_headless_from_folder.py` ＋ `tests/golden/*.json` で、既存12ケースの `period/products/config/forward/backward/ppc/psi` が不変であること。例 `tests/test_golden.py`。
3. **オーナー（大杉さん）が `git diff` を差分レビュー**してからコミット。
4. 挙動を**意図的に**変えるときは golden を**意図的に再生成・コミット**（差分そのものが監査証跡）。

**二重化の必然（§11.4）**: 禁足ルール（soft）だけでは「承認された変更の副作用」を防げない。それを機械的に捕まえるのはテストのみ。AI が markdown ルールに従うかは確率的だが、テストは機械が強制する。→ **禁足ルール＋3層テストの二重化が必須。**

**ゴールデン・ハーネス（網の実体・v1r2m2 で整備）**:
- `tools/run_headless_from_folder.py`：GUI 抜きで Load→Planning→PPC を実行し、KPI スナップショット（`forward{cap_hard_sealed, cap_soft_violation_count}` / `backward{cap_soft_envelope_count}` / `ppc` / `psi`〔各ノードの P/S/I/CO 集計＋週次系列 md5〕）を出力。GUI の実値と一致することを確認済み（＝ハーネス自体の忠実性担保）。
- `tests/golden/<case>.json`：12ケースの凍結スナップショット。`tests/test_golden.py` が「現行実行 == golden」を assert。
- **golden 再生成**（Windows・オーナー実行。bash マウントは切り捨てのため不可）:
  ```powershell
  Get-ChildItem tests\golden -Filter *.json | ForEach-Object {
    $c=$_.BaseName
    $pl= if ($c -like "rice-japan*") {"HolidayCalendarPlugin,BufferingStockOptimizerPlugin,CapacityOverridePlugin,HarvestBatchPlugin"} else {"safe"}
    python -m tools.run_headless_from_folder --model-dir "data\sample\$c" --plugins $pl --out "tests\golden\$c.json" --quiet }
  ```
  ※ レガシー `iphone`（旧サンプル・CNY FX 欠落で失敗）と `rice-…_BK…`（古いバックアップ）は golden 対象外。

---

## 設計上の制約・注意事項

- `app.py`はLinuxのbashでは約172KB付近で切り捨てられる。構文チェックはWindowsで行うこと: `python -c "import ast; ast.parse(open('wom/gui/app.py').read())"`
  **重要（2026-07-07追記、v1r0m5セッションで再確認・範囲を拡大）**: この切り捨ては`cat`/`python open()`だけでなく**`git`コマンド自体**（Linux bashマウント経由で実行した場合）にも及ぶことを確認済み。`git diff`/`git status`がapp.pyの末尾（`_on_ppc_done`以降、`launch()`まで）を「削除」として表示するが、これは実際の変更ではなく、bashマウント越しにgitが読んだファイルが切り捨てられているために生じる幻影。しかも**この現象はapp.py（約170KB超級）だけでなく、CLAUDE.md自体（57KB程度、760行）でも再現した**——`wc -l`がgit HEAD blobより少ない行数を返し、`git diff -w`が実際には発生していない大量の削除を表示した。つまり閾値は「約172KB」という固定サイズではなく、bashマウントのセッション内での累積読み込み量や再読込みタイミングに依存する可能性が高く、**編集した全てのファイルについて`git`をLinux bash経由で実行するのは危険**と考えるべき。
  **対策**: WOMのコード・ドキュメントに変更を加えたセッションでは、`git add`/`git commit`/`git push`は必ずユーザー自身のWindows側ターミナルで実行してもらうこと。Claude側のbashツールで`git add`/`git commit`を実行するのは絶対に避ける（ステージされる内容が切り捨てられた壊れたバージョンになる恐れがあるため）。`git diff`/`git status`をClaude側で覗き見て「変更点の要約」を作ること自体は無害だが、その差分表示を鵜呑みにせず、真に受けるべきは常にRead toolで読んだ内容（Windows側の実ファイル）である。
- `sc_tree_to_planning_df()`は`leaf_out`ノードのみを処理するため、DAD在庫はKPI DataFrameに現れない  
- `fit_bounding_box()`はtkintermapview \>= 0.3が必要  
- Planning Engine実行後にChartsタブを確認する場合、`Refresh`ボタンを押すこと  
- 新しいモデルフォルダを追加する場合は`rice-japan-2027-2028/`を参考に全CSVを揃えること
- Linuxのbash Editツールは大きいファイルを切り捨てることがある。重要ファイルの書き換えは `cat > file << 'PYEOF'` ヒアドックで行うこと

---

## 既知のバグ修正履歴（新しいClaude君へ）

### HolidayCalendarPlugin MemoryError（修正済み）
`wom/engine/holiday_calendar_plugin.py` の `on_post_backward` で `cap_hard(w)==0.0` を使って閉鎖週を判定していたが、`plan_node.py` はデフォルトで全週 `cap_hard=0.0` に初期化するため、全週が閉鎖扱いとなり displaced lots が指数的に増加して MemoryError が発生した。
**修正**: `self._rules` から `explicit_closures` dict を構築し、CSVに明示定義された週のみを閉鎖週とする方式に変更。open週がない場合はlotsをdropする（ForwardPlannerへの再割り当てなし）。

### BackwardPlanner._build_lot_leaf_index（実装済み）
`wom/engine/backward_planner.py` の多MOM（Multi-MOM）パスで `self._build_lot_leaf_index(ot_root)` を呼び出しているが、このメソッドが未実装だった（iPhone モデルで `AttributeError`）。
**修正**: OTツリーのleaf_outノード（`node.children`が空）を走査して `lot_id → PlanNode` インデックスを構築するメソッドを追加。また `leaf.region`（PlanNodeに`.region`属性なし）は `lot_id.split(":")[1]` でlot_idから抽出する方式に変更。

### holiday_calendar.csv のノード名（修正済み）
demand_multiplier 行のノード名 `Sales_US_iPhone16` / `Sales_EU_iPhone16` は存在しない。正しくは `Retail_AMER` / `Retail_EMEA`（sc_tree_master.csvのleaf_out node_name）。

### ファイルのnullバイト汚染（修正済み）
v1r0m1の `backward_planner.py` と `holiday_calendar.csv` にnullバイトが混入していた（Windowsでのコピー操作が原因の可能性）。Linuxの `bash cp` で上書きして修復。

### ForwardPlanner: PUSH MOM の在庫が0になるバグ（v1r0m3で修正済み）
`wom/engine/forward_planner.py` の Phase 1（InBound POST-ORDER）で、`is_decoupling=True` の全ノードに `psi4supply[w][P] = psi4demand[w][P]`（Demand-S copy）を適用していた。しかしPUSH設定されたMOM（`plan_mode="push"`）は `is_decoupling=True` になるため、leaf_in → tier-1 の `_propagate_to_parent` で積み上げた P が Demand-S copy に上書きされ、バッファ在庫が 0 になっていた。
**修正**: `node.plan_mode != "push"` 条件を追加し、PUSH MOM には Demand-S copy を適用しない。Buffer_Wafer_TW（`plan_mode="pull"`）は引き続き Demand-S copy が適用される。

### pytest .pyc キャッシュ問題（Linux環境）
Windowsでフォルダをコピーした場合、`__pycache__/*.pyc` も元のパスを `co_filename` として持つ。Linux FUSE マウント経由では .pyc の削除が permission error になるため、Python が古い .pyc を優先して .py の変更が反映されない。
**対処**: `os.utime(file, (now+10, now+10))` で .py ファイルのタイムスタンプを .pyc より新しくするか、`PYTHONDONTWRITEBYTECODE=1` + Python による .py 直接書き込み（`python3 << 'EOF'` ヒアドック）で迂回する。pytest 実行時は `PYTHONDONTWRITEBYTECODE=1 python -m pytest ... -p no:cacheprovider` を使うこと。

### PPC detect_scenario() が biscuit-jp-2026 → Cookie-jp-2026 リネーム後に不一致（修正済み、2026-07-05）
`data/sample/biscuit-jp-2026/` を `Cookie-jp-2026/` にリネーム＋SKU名を `OREO_JP`/`LUVAN_JP` → `Cookie_Import`/`Cookie_Local` に変更した際、`wom/ppc/ppc_engine.py` の `detect_scenario()` 側（`_BISCUIT_PRODUCTS = {"OREO_JP", "LUVAN_JP"}` 等）が更新されておらず、新SKU名と一致しないため `"iphone"` シナリオにフォールバックしていた。`mom_node`/`supplier_node`/`dad_node`/`dad_nodes_chain` も旧ノード名（`Factory_OREO_CN`, `DC_JP_BONDED`, `Factory_LUVAN_JP`, `DC_LUVAN_JP` 等）のままで、`ppc_node_cost_rule.csv` の実ノード名（`Factory_GP_CN`, `DC_Import_Buffer`, `DC_Import_Main`, `Factory_DP_JP`, `DC_Local_JP`）と不一致だった。
**修正**: `wom/ppc/ppc_engine.py`（`_COOKIE_PRODUCTS`/`_COOKIE_CHANNELS`, `build_cookie_vs_paths()`, `detect_scenario()` の戻り値 `"cookie"`）、`wom/ppc/ppc_runner.py`、`wom/ppc/__main__.py`、`wom/ppc/ppc_backward.py`（コメント）を新ノード名・新シナリオ名に更新。また `node_master.csv` / `sc_tree_master.csv` / `edge_cost_master.csv` 内の日本語ラベル「ビスケット」を「クッキー」に修正（World Map表示にも反映）。
**確認状況**: World Map表示は確認済みOK。PPC Cockpit（Cookie_Local）のCost Waterfallが `ppc_node_cost_rule.csv` の実値（Factory_DP_JP conversion_cost 9000 JPY, DC_Local_JP sga_cost 4000 JPY）と一致することを確認し、正しいノードチェーンでコストが拾えていることを確認済み。**Cookie_Import 側（`DC_Import_Buffer`→`DC_Import_Main` の2段DADチェーン、`DC_Import_Main`のSGA 1500円が正しく合算されるか）は未確認 — 次回セッションでSKUフィルタを`Cookie_Import`に切り替えて確認すること。** また `python -c "import ast; ast.parse(...)"` によるWindows側の構文チェックも未実施（Linux bashマウント経由では大きめの `.py` ファイルが切り捨てられ `ast.parse` が誤ってSyntaxErrorを出すため、Windows側で確認が必要）。

### ev-thailand-2026 の BYD/Tesla 実ブランド名を匿名化（完了、2026-07-06）
`data/sample/ev-thailand-2026/` は `BYD_ATTO3`/`TESLA_M3` という実在EVメーカーのブランド名・車種名を含んだままだった。note記事ドラフト（`260704タイEV_note記事ドラフト.docx`）はすでに `EVmaker_Local`/`EVmaker_Import` という匿名名を前提に書かれており、CSVとの不一致があった。
**修正**: 全17 CSVファイル（`sku_master.csv`, `node_master.csv`, `sc_tree_master.csv`, `node_cost_master.csv`, `edge_cost_master.csv`, `lane_assignment.csv`, `route_master.csv`, `push_config.csv`, `holiday_calendar.csv`, `inventory_master.csv`, `capacity_plan.csv`, `demand_forecast.csv`, `ppc_edge_cost_rule.csv`, `ppc_market_price.csv`, `ppc_node_cost_rule.csv`, `ppc_node_profit_zone.csv`, `ppc_profit_zone_rule.csv`, `ppc_supplier_cost.csv`, `ppc_tariff_rule.csv`, `ppc_transfer_price_rule.csv`）で以下の対応関係にリネーム：
- `BYD_ATTO3` → `EVmaker_Local`、`TESLA_M3` → `EVmaker_Import`
- `SP_BYD_TH`→`SP_EV_Local`、`Factory_BYD_TH`→`Factory_Local_TH`、`DC_BYD_TH`→`DC_EV_Local`
- `SP_TESLA_TH`→`SP_EV_Import`、`Factory_TESLA_CN`→`Factory_Import_CN`、`DC_TESLA_TH`→`DC_EV_Import`、`Components_CN_T`→`Components_CN`
- `Sales_TH_BKK_t`/`_PRO_t`/`_ONL_t`（Tesla側チャネル）→ `_i` サフィックスに変更（`Sales_TH_BKK_i` 等）。Local側の `Sales_TH_BKK`/`_PRO`/`_ONL` はサフィックスなしのまま据え置き（note記事ドラフトの命名と一致）。
- 説明文中の実企業名（レバーオートモーティブ、CATL、Gigafactory等）も除去し一般化。

**確認状況**: `wom/ppc/ppc_engine.py`/`ppc_runner.py`にBYD/Tesla固有のハードコード分岐は存在せず（biscuitのような専用シナリオ関数はなし）、PPCエンジンは`ppc_runner.py`の「GENERIC」自動検出パス（sc_treeからmom/supplier/dad nodeを動的に発見）でこのモデルを扱う設計だったため、**Pythonコード側の修正は不要**。リネーム後、CSV内に旧トークン（BYD_ATTO3, TESLA_M3, Factory_BYD_TH等）が残っていないことをGrep確認済み。行数・列構造も変化なし（capacity_plan.csv 625行、demand_forecast.csv 624行、変更前と一致）。GUI起動してWorld MapとPPC Cockpitの実挙動を確認済みOK（下記Landed Costバグ発見時に併せて確認）。

### Landed Cost engine: Landed GM%が1129%等の異常値になるバグ（修正済み、2026-07-06）
`wom/engine/landed_cost.py` の `compute_landed_cost_kpi()` が、Management タブの「Tariff & FX — Landed Cost Impact」パネルで `ev-thailand-2026` を実行した際に `Landed GM% 1129.0%` という非現実的な値を出していた（ユーザー指摘で発覚）。原因は2つ、いずれも「iPhoneモデル（単価$1000前後・fx_rate=1.0のUSDのみ）でしか成立しない代理計算」だった。
1. `estimated_lots = max(revenue / 1000.0, 1.0)` — 「1lot≈$1000」という前提の代理計算。EVモデル（1台80万〜160万THB）では `revenue=75,086,960,000` から `estimated_lots≈75,086,960`（実際は数万〜十数万lot程度のはずが桁違いに膨張）となり、`freight_total = blended_freight_per_lot × estimated_lots` が異常膨張（Freight $43,175,002,000 という表示値の直接原因）。
2. `fx_gain_loss = (blended_fx - 1.0) * cogs` — `fx_rate` が1.0前後の「比率」である前提だが、実際の`edge_cost_master.csv`の`fx_rate`は35.0（THB/USD）や145.0（JPY/USD）といった**絶対為替レート**。`(35.0-1.0)=34倍`がCOGSに掛かり`landed_cogs`から減算され、Landed GM%が桁違いの値になっていた。

**修正**:
- `wom/engine/money.py`: `evaluate_money()`の週次集計に`total_units`（`demand_fulfilled`の合計＝実lot数）を追加し、`build_scenario_money_kpi()`のシナリオ集計にも`units`列として伝播。これにより`compute_landed_cost_kpi()`が実際のlot数を参照できるようになった（`revenue/1000`の代理計算はunitsが取れない場合のフォールバックとしてのみ残す）。
- `wom/engine/landed_cost.py`: `lot_count`は`kpi_row["units"]`から取得。`freight_total = blended_freight_per_lot × lot_count × blended_fx`（USD建てのfreightをfx_rateで報告通貨に変換、docstring本来の設計通り）に修正。`fx_gain_loss`（COGSへの誤った为替比率適用）は完全に削除——revenue/cogsは既にWOM money engineで報告通貨（JPY/THB等）建てのため、二重にfx調整する必要がない。ファイル冒頭のdocstring（Calculation model節）も実装と一致するよう全面的に書き直した。
- 出力dict内の`fx_gain_loss`キーは後方互換のため残すが常に0（廃止済みの注記付き）。

**確認状況**: 手計算でEVモデルのBaseシナリオを検算——修正後 `landed_gm ≈ 28.5%`（元のgross margin 31.9%からtariff/freight負担で妥当な範囲の低下）となることを確認。GUI実行結果（Management タブ）でも Base 27.5%・EV30/EV35 29.6% と妥当な値で表示されることをユーザー確認済み。なお、PPC Cockpit画面で "Base currency: JPY" と表示されるのはTHB建てのev-thailand-2026でも固定表示になっている可能性があり、別途確認の余地あり（今回は未調査）。

### Management タブ: SKUフィルタ追加 + Inv Value常時0バグ修正（完了、2026-07-06）
Management タブの P&L Summary / Strategic KPI / Tariff&FX (Landed Cost) が SKU=ALL の全体合算のみで、SKU別評価ができなかった（ユーザー要望で追加）。あわせて調査中、`build_scenario_money_kpi()`（`wom/engine/money.py`）の集計キーワードが `inv_value=(Cols.INV_VALUE_COST, "mean")` となっており、出力列名が `"inv_value"`（別の定数 `Cols.INV_VALUE`）になっていた。一方 `app.py` の P&L テーブルや `management.py` の `_row_to_money_dict()` はどちらも `Cols.INV_VALUE_COST`（`"inv_value_cost"`）で参照していたため、**Inv Value は常に0扱い**になっていた（P&L SummaryのInv Value列が常に0だったのはこれが原因）。

**修正**:
- `wom/engine/scenario.py`: `ScenarioManager` に `sc_tree` / `lc_scens` / `route_idx` を追加。Planning Engine 実行後（`app.py`）にこれらを保持し、Management タブ側でPlanning再実行なしにSKUフィルタの再計算ができるようにした。
- `wom/engine/strategic_kpi.py`: `compute_strategic_kpi()` に `product_filter` 引数を追加（指定した1製品のノードのみ集計）。
- `wom/engine/landed_cost.py`: `filter_scenario_by_sku()` を新設（`route_master.csv`の(sku_id,region)→(src_region,dst_region)を使い、LandedCostScenarioのprofilesを対象SKUのレーンだけに絞る）。`compute_landed_cost_kpi()`/`compare_lc_scenarios()`に`sku_id`引数を追加し、KD組立コスト集計・関税/為替ブレンドをSKUスコープにできるようにした（`sku_id`未指定時は従来通り全SKU合算）。
- `wom/gui/app.py` `ManagementCockpitPanel`: 「SKU:」ドロップダウン（All + 実在SKU一覧）を追加。選択変更で `_refresh_pl_table()` / `_refresh_strategic_kpis()` / `_refresh_lc_table()` / `_refresh_charts()` を、`mgr.summary_money`をSKUでフィルタして`build_scenario_money_kpi()`で再集計した行、`compute_strategic_kpi(sc_tree, product_filter=sku)`、`compare_lc_scenarios(..., sku_id=sku)`を使って再計算するように変更（"All"選択時は従来通りPlanning Engine実行時に事前計算済みの値を使用、挙動不変）。
- Inv Value バグ修正: `build_scenario_money_kpi()`の集計キーワードを `inv_value_cost=(Cols.INV_VALUE_COST, "mean")` に変更（列名を実際の定数値と一致させた）。

**確認状況**: コードレビューベースで整合性確認済み（Edit toolでの直接編集、bashマウント経由の構文チェックは大きめファイルで信頼できないため未実施）。**次回セッションでGUI起動し、①SKUドロップダウンで実際にP&L/Strategic KPI/Landed Costが切り替わるか、②Inv Value列が0以外の値を表示するか、の実地確認が必要。**

### ForwardPlanner: Lot_ID identity-matching方式への刷新（DADバッファ在庫問題の根本修正、完了、2026-07-06）

Cookie Japan 2026 note記事ドラフトの注記「DADノードの在庫（I）は常に0（pass-through設計）。ss_days=21は将来実装（v1r0m5）向けの設定で、現在DC_Import_Bufferへの在庫積み上がりは発生しない」について、大杉さんから「SS_daysの取扱いはWOM初期から標準機能だったはず」との指摘を受け調査。

**調査で判明した事実**:
1. `backward_planner.py`の`_ot_propagate`/`_in_propagate`は既に`node.lt_wks + node.ss_wks`をオフセットに使っており、大杉さん提案の`LT_shift = LT_transit + SS_weeks`は実装済みだった。
2. `forward_planner.py`の物理搬送（`_propagate_to_child`/`_propagate_to_parent`）はpure `lt_wks`のみを使用（SS_weeks分だけ早着する設計）。
3. しかし実測すると、DC_Import_Buffer（lt_wks=5, ss_days=21→ss_wks=3）の`I`は52週間フルトレースで常に0だった。
4. 原因は`ForwardPlanner._process_node`のCase1/2/3分岐が**個数（len）ベース**で、`available`（物理在庫+入荷）と`total_demand`（CO+S）を**位置（何番目か）でスライス**していたこと。シミュレーション開始直後（期初在庫ゼロ、初週から満量需要）に生じる不可避な立ち上がり不足が、巨大なCOとして生成され、以後は毎週の新規供給がすべてこの「凍結した過去の負債」の穴埋めに使われ続け、二度と在庫として積み上がらなくなっていた（COは`_process_node`内で毎週クリアされ表示上は常に0に見えるため、この凍結は発見しづらかった）。

**大杉さんとの議論で得られた設計方針**:
- `C:\Users\ohsug\WOM_V0R1M0_github\pysi\network\node_base.py`の`calcPS2I4supply()`（v1r0m0オリジナルのPySIエンジン）を確認したところ、`fifo_lot_diff(i0, p, s)`という**Lot_ID identity（集合差分）ベース**の実装だった（個数比較ではなく`if lot not in s`という一件ごとの判定）。COを読むが書き込まない未完成な実装だったため、大杉さんの提案で「COも含めた対称的なidentityマッチング」に一般化：
  - `I1 = (i0+p) − (CO+S)`（identityで、CO+Sに含まれないLot_IDが在庫として残る）
  - `CO1 = (CO+S) − (i0+p)`（identityで、i0+pに実在しなかったLot_IDが翌週へ、これが欠品リストそのもの）
- `S`と`CO`は「計画値」として**一切書き換えない**（Sの一意性を守る）。物理的に実際に出荷されるLot_IDは`ForwardPlanner._actual_s`という別チャネルに保持し、`_propagate_to_child`/`_propagate_to_parent`/MOM→supply_pointブリッジはこちらを参照する。

**実装内容**（`wom/engine/forward_planner.py`）:
- `_match_by_identity(demand_lots, supply_lots)`staticmethodを新設。Lot_ID identityで`matched`/`unmatched_demand`/`unmatched_supply`を返す。
- `_process_node`の通常（pull）分岐を、個数ベースのCase1/2/3から`_match_by_identity`ベースに置換。`S[w]`/`CO[w]`は変更せず、`I[w] = unmatched_supply`、`CO[w+1] += unmatched_demand`。
- `self._actual_s: Dict[node_id, Dict[w, List[lot_id]]]`を新設（旧`_push_actual_s`を全modeに一般化・リネーム）。`_propagate_to_parent`/`_propagate_to_child`/Phase2ブリッジは`psi4supply[w][S]`ではなく`self._actual_s`を参照するよう変更。
- `is_push_mode`/`is_push_sub`分岐は個数ベースのロジックのまま維持（scope外、iPhoneモデルのBuffer_Wafer_TW等で別途十分にテスト済みのため）。

**確認結果**:
- 既存63件のテストは`tests/test_step7_capacity.py::test_e2e_cap_hard_causes_leaf_shortfall`と`tests/test_step8_push_pull.py::test_dad_inventory_cap_hard_shortfall`の2件が失敗（想定通り。DAD.Sが「実供給で制約された値」ではなく「計画値のまま」になったため）。両テストは新設計の検証内容（`fp._actual_s`で実出荷数、`psi4supply[w][CO]`で欠品数を確認）に書き換え、63件全PASSを再確認。
- Cookie_Import / DC_Import_Buffer を52週フルトレースした結果、`I`が41週で非ゼロとなり、SS_days=21日（3週）分の安全在庫バッファが正しく可視化されることを確認。
- 立ち上がり期の不可避な不足（期初在庫ゼロ・初週から満量需要）は、以前のように無限に増殖する凍結COではなく、**有限かつ正直な**未充足Lot_ID集合として`CO`に残る（該当Lot_IDの需要週がシミュレーション開始週より前で、物理的に到底間に合わないため）。

**未対応・次回検討事項**:
- GUIのBuffer Stockチャート（Charts タブ）は依然MOMノードのみ参照。DAD側のIも表示対象に含めるかは次回検討。
- note記事「Cookie Japan 2026」の該当注記（DADのIは常に0、SS_daysは将来実装向け）は誤りとなったため、記事側の修正が必要（大杉さんの記事執筆時に反映）。
- PPC/Money engine・Fill Rate計算はleaf_outノードの`psi4supply[w][S]`のみ参照しており、leaf_outは常にmatched=S（pull_modeでP=demand.Pに強制されるため）なので影響なし。ただしDebugPanelの`_draw_cost_from_plan_node`（app.py、任意のnodeを選択してRevenue/COGSを表示する機能）は非leaf_outノードでは「計画値」ベースの表示になる点に注意（実出荷ベースへの追従は未実施、影響は限定的）。

### Buffering Stock配置最適化エンジン（`wom/engine/decouple_optimizer.py`、新規実装、完了、2026-07-06）

大杉さんの提案「buffering stock候補 = SKU数 × lane中の平均node数、という限られたnodeの組み合わせをすべて評価すれば、cost最適な在庫配置を提示できる」を受け、v1r0m0（PySI）の`pysi/plan/engines.py`を調査。**候補生成ロジック（`make_nodes_decouple_all`）は残っていたが、評価ロジックは見つからなかった**ため、大杉さんに確認の上、v1r0m4で新規実装する方針とした。

**実装内容**:
- `build_decouple_candidates(ot_root)`: `make_nodes_decouple_all`のポート。leaf_out群を出発点に、兄弟ノードを親ノードへ1候補ずつマージしていく（深い方から）ことで、`O(node数)`件の候補（2^N の全組み合わせではない）を生成。**supply_point（仮想bridgeノード、lt_wks=0、実在しない場所）を含む候補は除外**——最初の実装では除外しておらず、後述の理由でランキングが破綻したため追加。
- `evaluate_decouple_placement(...)`: 各候補について、供給層をリセット（`copy_demand_to_supply`で再構築——CO は識別子マッチング方式で追記専用のため、候補間でリークしないようリセットが必須）した上で`ForwardPlanner(..., decouple_node_ids=candidate)`を1回実行し、全ノード合計の在庫lot数・在庫コスト（`node_cost_master.csv`のunit_cost_per_lot使用）・欠品(shortfall) lot数を測定。
- `find_optimal_decouple_placement(...)`: 全候補を評価し、**サービスレベル制約付きランキング**で最良候補を選定。

**サービスレベル制約が必要だった理由（実装中に発見したバグ）**:
最初の実装は単純に「在庫コスト最小」で候補をランキングしたところ、`supply_point`（仮想bridgeノード）が「最良」に選ばれる縮退結果が発生した。原因は、decouple pointをsupply_pointに置くと、それより下流の全ノードがPULLモードに強制され（`P = demand.P`のコピー）、実際の需給ミスマッチが在庫=0・欠品=0として隠蔽され、真の需給ギャップがsupply_point自身のCOだけに（不自然に）集約されるため。この結果、「在庫コストが低い」ことと「実際にサービスレベルが高い」ことが一致しなくなっていた。
**対策**: (1) 候補生成時にsupply_pointを含む候補を除外。(2) `find_optimal_decouple_placement`で「全候補中の最小shortfall × 許容比率(デフォルト1.10倍)」以内の候補だけを`eligible`として抽出し、その中でコスト最小を選定（`ranked`は参考として全候補・コストのみのソート結果も保持）。

**確認結果**（`data/sample/Cookie-jp-2026`、node_cost_master.csvベース）:
- Cookie_Import: 候補3件（`[Retail×3]`, `[DC_Import_Main]`, `[DC_Import_Buffer]`）→ **`DC_Import_Buffer`が最良**（inv_cost最小かつshortfallも最小）。CSV上`buffering_stock_flag=1`が設定されている実際の設計と一致。
- Cookie_Local: 候補2件（`[Retail×3]`, `[DC_Local_JP]`）→ **`DC_Local_JP`が最良**。
- 既存63件のテストは無変更・全PASS（本モジュールは既存エンジンを呼び出すだけで、`ForwardPlanner`/テストコード自体には変更なし）。

**スコープ制約・次回検討事項**:
- OutBound（leaf_out → DAD → supply_point）側のみ対応。InBound（leaf_in → MOM）側のbuffer配置最適化は別問題として未実装（下記の通り、意図的に対応しない方針で確定）。
- ~~GUI統合（Managementタブ等からの呼び出しUI）は未実装。~~ → `BufferingStockOptimizerPlugin`として実装済み（下記）。
- shortfall許容比率（デフォルト1.10）は暫定値。業種・SKUごとのサービスレベル要件に応じて調整可能な設計にはなっているが、デフォルト値自体の妥当性検証は未実施。

### InBound側バッファ配置最適化を対象外とする方針確定（2026-07-06）

大杉さんの判断: 「各素材・部材の加工工程の生産能力のLOT単位処理能力で、相対的にボトルネックが発生した場所で、DBR的なbuffering stockが発生する」ため、InBound側はOutBoundのような「どこに置いてもコスト最適化できる自由度」がなく、物理的な設備能力差・ボトルネック制約が支配的。よってバッファ配置最適化という問題そのものに発展しない。**InBound側は今後も対象外のまま据え置く。**

### BufferingStockOptimizerPlugin（`wom/plugins/buffering_stock_optimizer.py`、新規実装、完了、2026-07-06）

`decouple_optimizer.py`をPluginとしてPlanning Engineパイプラインに組み込み、`decouple_optimizer_config.csv`のON/OFFフラグでSKUごとに有効化できるようにした。

**フック位置の設計判断（重要）**: 当初「PRE_PLANで発火」という案があったが、`evaluate_decouple_placement()`は内部で候補ごとにForwardPlannerを試走する前提としてpsi4demandが埋まっている必要がある（BackwardPlanner完了後）。PRE_PLANはSCTree構築直後・BackwardPlanner実行前に発火するため、この時点ではpsi4demandが空でPlugin側の評価が成立しない。よって**`HOOK_POST_BACKWARD`**（BackwardPlanner完了直後、公式の`copy_demand_to_supply`実行前）を正しいフック位置として採用した。

**実装内容**:
- `BufferingStockOptimizerPlugin`（`WOMPlugin`継承、`on_post_backward`をoverride）:
  1. `decouple_optimizer_config.csv`（`cap_path`と同じディレクトリ、`cap_override.csv`と同じ解決パターン）を読み、対象SKUの行が無い/`enabled=0`/ファイル自体が無い場合は即return（no-op、既存の手動`buffering_stock_flag`設定がそのまま使われる＝後方互換）。
  2. `enabled=1`の場合、`find_optimal_decouple_placement()`を実行し、最良候補を選定。
  3. OutBoundツリー（`sc_tree.get_ot_root(prod_nm)`配下）の全ノードの`is_decoupling`をいったん`False`にリセットし、最良候補のノードのみ`True`に上書き。
  4. Plugin実行後、`psi4supply`（S/CO/I/P全バケット）を空にクリアした状態で終了（**再`copy_demand_to_supply`は呼ばない**——このHookの直後にパイプライン本体が公式の`copy_demand_to_supply`を実行するため、二重実行を避けた）。
- `decouple_optimizer_config.csv`スキーマ: `sku_id, enabled, max_shortfall_ratio`（`max_shortfall_ratio`列は省略可、省略時デフォルト1.10）。
- `wom/plugins/__init__.py`の`ALL_BUILTIN_PLUGINS`に登録済み。
- `data/sample/Cookie-jp-2026/decouple_optimizer_config.csv`をサンプルとして追加（`enabled=0`——スキーマの見本のみ、デフォルト動作は変更しない）。

**テスト**（`tests/test_decouple_optimizer.py`, `tests/test_buffering_stock_optimizer_plugin.py`、計8件、全PASS）:
- 候補生成がsupply_pointを除外することを確認。
- `ss_days`が特定ノードにのみ設定されている場合、そのノードが「decouple点より下流（pull-mode強制）」になると`ss_days`由来の早期在庫シグナルが完全に消える（`psi4supply[w][P]`が`demand.P`で上書きされるため）ことを実際の合成ツリーで確認・数値検証。これが CLAUDE.md 既出の「is_decoupling **かつ** ss_daysが設定されていれば」という前提条件の具体的なメカニズムである。
- `find_optimal_decouple_placement()`をCookie-jp-2026実データで再検証（`decouple_optimizer.py`本体のテストとしては本セッションで初めて追加——これまでは手動probeスクリプトのみだった）：Cookie_Import→`DC_Import_Buffer`、Cookie_Local→`DC_Local_JP`が引き続き最良候補になることを回帰テスト化。
- Plugin側: 設定ファイル無し/`enabled=0`/対象外SKUの行のみ、の3パターンで確実にno-opになること、`enabled=1`時に正しいノードへ`is_decoupling`が切り替わり、かつ`psi4supply`が空にクリアされて公式パイプラインに引き渡せる状態になることを確認。
- 既存63件 + 新規8件 = 計71件、全PASS。

**未対応・次回検討事項**:
- GUI側のPlugin ON/OFFトグル（Management/Settings的な画面からの切り替えUI）は未実装。現状は`decouple_optimizer_config.csv`を直接編集する運用。
- レーン障害時の代替ルート切り替えPlugin（例: ホルムズ海峡封鎖時に紅海ルートへ切り替え）は、大杉さんから将来実装候補として提案あり。こちらは`HOOK_PRE_PLAN`（SCTree構築直後・BackwardPlanner実行前、`edge_cost_master.csv`/`route_master.csv`ベースのlt_wks・cap_hard書き換え）が適切なフック位置で、既存の`CapacityOverridePlugin`/`HolidayCalendarPlugin`と同じパターンで実装できる見込み。次回セッションでの実装候補として記録のみ（今回は未着手）。

---

## v1r0m5 実装済み機能（新しいClaude君へ）

### PPC: 複数Tier-1サプライヤー対応 + 拠点別P/L評価（`ppc_forward.py`, `ppc_kpi.py`, Management タブ、完了）

「第4回: 仮想の欧州EV市場」note記事（`data/sample/ev-europe-2026/`）で、EVのBOM構造を Battery/Motor/ECU の3 Tier-1 サプライヤー（leaf_in）が1つのMOMに供給する形にした際、既存のPPCエンジンが**最初に見つけたleaf_inノード1つしかコストに反映していない**ことが判明した。

**原因**: `wom/ppc/ppc_runner.py`のGENERICシナリオ自動判定で `elif _nt == NODE_TYPE_LEAF_IN and _prod not in _sup_map: _sup_map[_prod] = _nm` としており、`_prod not in _sup_map`のガードにより2つ目以降のleaf_inは無視されていた。さらに`wom/ppc/ppc_forward.py`の`run_forward_propagation()`は`supplier_node`を単一ノードとしてしか解決しない設計だった（`_resolve_node()`がstr/dict[str,str]のみ対応）。この結果、Motor/ECU側は`ppc_supplier_cost.csv`に行があっても一切参照されず、PPCEventすら生成されないため、ノード別コスト集計をしても0円のまま欠落する。

**修正**:
- `wom/ppc/ppc_forward.py`: `_resolve_node_list(node, product_id) -> List[str]` を新設（str / list[str] / dict[str,str] / dict[str,list[str]] の全形式に対応、`ppc_backward.py`の`dad_nodes_chain`解決パターンを踏襲）。`run_forward_propagation()`の`supplier_node`引数をこの関数で解決し、**解決された全サプライヤーをループしてコストを積算 + サプライヤーごとに1件ずつ`supplier_cost`イベントを生成**するよう変更（各イベントの`node_id`はそのサプライヤー自身のノードID）。Cookie/iPhone/RiceのようなシングルサプライヤーはP`_resolve_node_list`が単一値を1要素リストにラップするため無変更で動作する。
- `wom/ppc/ppc_runner.py`: GENERIC分岐の`_sup_map`（単一値）を`_sup_list_map`（全leaf_inのリスト）に変更。積み上げたリストは複数製品時`dict[product_id -> list[str]]`、単一製品時は素の`list[str]`として`supplier_node`に渡す。
- `wom/ppc/ppc_kpi.py`: `build_node_pl_summary(events)` を新設。週次分解の`build_node_week_summary()`と異なり、全期間を通算した「拠点別P/L評価」テーブル（`node_id, product_id, revenue_base, cost_base, tariff_base, gross_profit_base, gross_margin_pct, lot_events`）を1ノード1行で返す。PPCEventの`node_id`にサプライヤーごとの実ノードIDが乗るようになった今回の修正により、Battery/Motor/ECUがそれぞれ独立した行として正しく現れる。
- `wom/ppc/ppc_models.py` / `ppc_engine.py` / `ppc_export.py`: `PPCSimulationResult.node_pl_summary`フィールドを追加し、`run()`内で自動計算・`output/ppc/ppc_node_pl_summary.csv`として出力するよう配線。
- `wom/gui/app.py` `ManagementCockpitPanel`: 既存の「P&L Summary」テーブル直下に新しい「Node P&L（拠点別損益）」テーブルを追加（既存のSKUフィルタドロップダウンに連動）。`_refresh_node_pl_table()`が`output/ppc/ppc_node_pl_summary.csv`を読み込み表示。PPCエンジンが完了した際（`_on_ppc_done`）にも自動リフレッシュされるよう配線済み。

**確認結果**:
- `tests/test_ppc_multi_supplier.py`（新規10件）: `_resolve_node_list`の4形式、複数サプライヤーのイベント生成・コスト合算、`dict[product_id -> list]`形式、既存の単一サプライヤー形式が無変更で動作すること、`build_node_pl_summary`の拠点別内訳を確認。既存71件と合わせて計81件、全PASS。
- `data/sample/ev-europe-2026/`実データで検証（`ppc_supplier_cost.csv`にMotor_DE/ECU_DE/Motor_HU/ECU_HUの行を追加——`node_cost_master.csv`の`unit_cost_per_lot`と整合する値: 3600/1600/3000/1400 EUR）: 修正前はBattery_DE/HUのみノード別コストが乗っていたはずが、修正後は`ppc_node_pl_summary.csv`にBattery/Motor/ECUの3ノードすべてが両SKU（EVmaker_Local/Import）で非ゼロコストとして現れることを確認（Battery_DE ¥265.1M、Motor_DE ¥90.9M、ECU_DE ¥40.4M、Battery_HU ¥202.0M、Motor_HU ¥75.7M、ECU_HU ¥35.3M）。

**設計上の注意（次回のClaude君へ）**:
- 「拠点別P/L評価」は現状、**leaf_outチャネルにのみRevenueが立ち、それ以外の全ノードはCostのみ**という構造（PPCエンジンがMOM一箇所にしかtransfer_priceを持たないため）。よって非チャネルノードの`gross_profit_base`は実質「-cost_base」であり、真の意味でのノード単体P&L（各ノードに自前のRevenue/Costがある社内取引評価）ではない。あくまで「どのノードにコストが集中しているか」を可視化するための一次的な実装であり、真の拠点別損益（ノード間振替価格を全エッジに設定する等）は将来の拡張候補として残っている。
- InBound側（leaf_in）のバッファ配置最適化は引き続き対象外方針（v1r0m4の`decouple_optimizer.py`のセクション参照）。今回の修正はコスト集計のみでPSI計画ロジックには一切手を入れていない。

---

### 第5回note記事向け: Global Oil Supply Chain（`data/sample/oil-global-2027/`、新規モデル、2026-07-07）

Grokとの構想検討を経て、Claude主導でモデル定義からCSV構築・ヘッドレス検証まで実施。Cookie/EVと同じ「現地生産（Gasoline_Local）vs 越境輸入（Gasoline_Import）」の対比構造を踏襲しつつ、石油SC特有の要素（クラックスプレッド、タンカーLT、貯油タンクの安全在庫）を**新規エンジン拡張なし**で表現する設計とした。

**モデル構造**:
```
Gasoline_Local:  Crude_ME(leaf_in, ME) → Refinery_Local(mom, JP) → supply_point
                   → Tank_Local(dad, ss_days=7, buffering_stock_flag=0)
                   → Retail_Local_KANTO/KANSAI/CHUBU(leaf_out)
Gasoline_Import: Refinery_SG(leaf_in, SG) → Import_Hub(mom, JP) → supply_point
                   → Tank_Import(dad, ss_days=21, buffering_stock_flag=1)
                   → Retail_Import_KANTO_I/KANSAI_I(leaf_out)
```
78週（2027-W01〜2028-W26）。原油/輸入完成品価格（`ppc_supplier_cost.csv`）とUSD/JPY為替（`ppc_fx_rate.csv`）は2027-W20〜W30に一時的にスパイクする一方、小売価格（`ppc_market_price.csv`）は据え置き（粘着的）に設定 — クラックスプレッド圧縮を**データの組み方だけ**で表現できることを実証する狙い。

**ヘッドレス検証用スクリプト**: `wom/gui/app.py`の`_build_planning_context`（4562行目〜）と`_planning_thread`（4728行目〜）、`_run_ppc_from_planning`（4980行目〜）をtkinter抜きで移植したスタンドアロンスクリプトを作成（このセッションでは`/sessions`一時領域に置いたのみでリポジトリ未コミット。次回セッションで`tools/run_headless_from_folder.py`のような形で正式に追加する価値あり——「手動probeスクリプトが毎回消えている」問題の恒久対策）。

**確認できたこと**:
- クラックスプレッド圧縮: Refinery_LocalのPPC `mom_profit`イベントが週次で **+2,000円/kL（平常時）→ −23,750円/kL（原油スパイク中、2027-W20〜W30）→ −1,520円/kL（スパイク後、元の水準には戻らない）** と推移することを`ppc_event_ledger.csv`で確認。小売価格据え置き＋調達コスト急騰＋円安の組み合わせがそのままマージン圧縮として現れることを実証。
- タンカー安全在庫バッファ: Tank_Import（ss_days=21, buffering_stock_flag=1）の`psi4supply[w][I]`が400〜420ロット程度で安定的に積み上がることを確認（Cookie-jp-2026のDC_Import_Bufferと同じ挙動）。
- 71+10=81件の既存テストに影響なし（このモデル追加はサンプルデータのみで、エンジンコードは無変更）。

**未解決・次回検討事項（Refinery_Outageシナリオ）**:
`holiday_calendar.csv`でRefinery_Local（MOM）に2週間の稼働率低下（cap_hard 650→30、supply_closure）を設定したところ、Refinery_Local自身の`psi4supply[w][P/S]`は正しく30に絞られることを確認した。しかし、その下流のTank_Local（DAD）・Retail_Local_*（leaf_out）のPSIには一切変化が見られなかった（`psi4supply[w][S]`は滑らかな季節変動のみ、`psi4supply[w][CO]`もクロージャー前後で完全に同一値のまま凍結）。
調査の結果わかったこと：
1. `_apply_mom_cap_backward`（backward_planner.py）は`cap_hard <= 0.0`のとき`continue`（何もしない）——これは意図的な設計（`plan_node.py`が全ノードをデフォルトcap_hard=0.0で初期化するため、「0.0=未設定」と「0.0=意図的な全停止」を区別できないことに起因、HolidayCalendarPlugin MemoryError修正の教訓と同根）。よって本当の「全停止」を表現したい場合は`holiday_calendar.csv`のvalue列に0ではなく小さい正の値（本モデルでは30、平常時cap_hardの約5%）を設定する必要がある。**この制約は既存の全モデルに影響するため、エンジン側の修正（0.0を「意図的な閉鎖」として扱うための別フラグ導入等）は今回は見送り、次回セッションでの検討課題として記録するに留めた。**
2. 上記のworkaroundで cap_hard=30 に変更後、Refinery_Local自身のP/Sは正しく650→30に落ちることを確認したが、DAD/leaf_out側のPSIには依然として変化が伝播しなかった。`tests/test_step7_capacity.py::test_e2e_cap_hard_causes_leaf_shortfall`（MOM cap_hardがleaf shortfallを引き起こすことを検証する既存の合格テスト）と突き合わせたところ、v1r0m4のLot_ID identity-matching方式導入以降、DAD/leaf_outの`psi4supply[w][S]`は**意図的に**需要アンカー型（demand-anchored）のままで欠品を表示しない設計になっており、真の欠品は`psi4supply[w][CO]`（このモデルでは常に1506で凍結して見えた）や`ForwardPlanner._actual_s`（GUIには一切露出しない内部dict）を見る必要があることが判明。ただし本モデルでCOが本当に「凍結」しているのか、単に立ち上がり期の在庫ゼロに起因する既存の恒常的バックログ（52週デモランプ後も残る）に埋もれて見えないだけなのか、そこの切り分けは未完了。
3. **次回セッションでの検討候補**: (a) `ForwardPlanner._actual_s`をCO同様にGUI/CSVエクスポート対象に含めるかどうかの検討、(b) 本モデルのCO=1506が実際に閉鎖期間中増加しているのか純粋なフローズンバックログなのかを、小規模な合成ツリーで切り分けて確認、(c) cap_hard=0.0の意味の曖昧さ（「未設定」 vs 「意図的な全停止」）を解消する設計変更の要否検討。

**現時点の記事化方針**: Refinery_Outageのダウンストリーム欠品可視化は保留とし、クラックスプレッド圧縮とタンカー安全在庫バッファの2本柱でnote記事を書き進める（大杉さんとの合意、2026-07-07）。

---

### World Map: SKUフィルタ + sc_tree_master.csvベースの実エッジ描画（`WorldMapPanel`、完了、2026-07-07）

oil-global-2027モデルをWorld Mapで確認した際、大杉さんから「北米・欧州・東南アジア・中国・アフリカ・ロシア・インドのようにGlobal Main Marketが増えると、地図が真っ黒になって判別できなくなるのでは？SKUでフィルタリングするのか？」という指摘があり、実際に調査したところ2つの根本的な制約が見つかった。

**発見した制約**:
1. `WorldMapPanel`にはSKU/region/node_typeいずれのフィルタ機構も一切存在しなかった（`node_master.csv`に`sku_id`列はあり読み込まれてはいたが、`_on_marker_click`の情報表示にしか使われておらず、フィルタリングには未配線だった）。
2. 線（ルート）の描画が`sc_tree_master.csv`の実際の親子関係を一切見ておらず、`_MAP_LINKS`という固定の`node_type`総当たりペア（procurement→sku_supplier→mother_plant→region_dc→marketing）で機械的に引かれていた。これはSKU・地域が増えるほどノード数×ノード数で線の本数が爆発し、かつ本来繋がっていないノード同士にも線を引いてしまう設計だった。

**修正内容**（`wom/gui/app.py` `WorldMapPanel`クラス）:
- コントロールバーに「SKU:」ドロップダウン（`_map_sku_var`/`_map_sku_cb`、Managementタブの`_sku_var`と同じパターン）を追加。
- `load_default(csv_path, sc_tree_path="")`に`sc_tree_path`引数を追加し、3箇所の呼び出し元（`_load_model_folder`、`_on_simulation_done`、`_on_planning_done`）すべてで`sc_tree_master.csv`のパス（`self._f_sc_tree`またはフォルダ内の同名ファイル）も渡すように変更。
- 新設`_load_sc_tree_edges()`: `sc_tree_master.csv`を読み込み、`node_name`/`parent_node`列から実際の親子エッジ（`self._sc_edges`）と`product_name → {node_id}`のマップ（`self._product_nodes`）を構築。**重要な注意点**: `sc_tree_master.csv`の`node_name`列は`node_master.csv`の`node_id`列と同じ短い識別子を指しており（`node_master.csv`自身の`node_name`列は人間可読な説明文で別物）、マッチングは`node_id`基準で行う必要がある。
- `_draw_nodes()`: 選択中のSKUで`self._nodes`を絞り込み、エッジ描画も`self._sc_edges`（SKUで絞り込み可）ベースに変更。`sc_tree_master.csv`が渡されていない旧来モデルのために、`_MAP_LINKS`総当たり方式もフォールバックとして残した。`_fit_to_nodes()`（自動ズーム）も絞り込み後のノード集合に対して行うよう変更。
- `_draw_animated_paths()`の背景の淡色ライン（アニメーション時の非アクティブエッジ表示）も同様に`_sc_edges`＋SKUフィルタベースに変更。

**確認状況**: 大杉さんがGUIで実機確認（oil-global-2027の2 SKU、ev-europe-2026のEVmaker_Local、Cookie-jp-2026のCookie_Import/Local）。SKUフィルタ・実エッジ描画とも概ね良好に動作。

**追加で見つかった不具合とその修正（同日）**: Cookie-jp-2026のCookie_Importで、北京クッキー工場（`Factory_GP_CN`、mom）が日本側のDC/店舗クラスタ（`SP_Cookie_Import`以下、supply_point側）と地図上で線が繋がっていないことが判明。原因は、`sc_tree_master.csv`にはInBound側のmom rootとOutBound側のsupply_point rootの間に`parent_node`列の関係が一切無い（両方とも`parent_node=""`の独立したroot行）ため——CLAUDE.mdのSCTree図で「Bridge」と書かれている部分は、CSV上の親子関係ではなく**エンジンが実行時に`product_name`一致で内部的に繋いでいるだけ**（`forward_planner.py`のPhase 2ブリッジ、`_actual_s`経由）で、`_load_sc_tree_edges()`はCSVの`parent_node`列しか見ていなかったため、このブリッジ区間だけ線が抜け落ちていた。EV/Oilモデルではmom側とsupply_point側の拠点がすべて同じ国・近距離に固まっていたため気づかれなかったが、Cookie_Import（中国工場↔日本市場、地理的に大きく離れている）で初めて可視化されて発覚。
**修正**: `_load_sc_tree_edges()`に、各`product_name`ごとの`mom` root（複数可、Multi-MOM対応）と`supply_point` root を集計し、`mom_root → supply_point_root`という合成エッジを追加する処理を追加。

**次回セッションでの確認候補**: 上記修正後の再確認（Cookie_Importで北京工場↔日本市場の線が繋がるか）はまだ大杉さんに見てもらえていない。iPhone Global SC（Multi-MOM、`Buffer_Wafer_TW`のPUSH/PULLブレークポイント）でも正しく複数mom→supply_pointのブリッジ線が引かれるか確認する価値がある。

---

### 第5回note記事: Hormuz海峡封鎖 vs Red Sea代替ルート比較（`data/sample/oil-global-2027/`拡張、新規2 SKU追加、完了、2026-07-07）

大杉さんの提案：「ホルムズ海峡封鎖前の正規ルート(サウジ→ホルムズ海峡経由タンカー)と、封鎖後の緊急ルート(サウジ内陸パイプライン→紅海経由タンカー)を、SKU_nameレベルで`_normal`/`_alt`のように分けて定義し、使わない期間はdemandを0にすれば比較できるのでは」という設計案を採用。さらに「切替の瞬間にTank在庫が消化されずに凍結される」という当初の懸念に対し、「生産STOP後も+4週間分はdemandを継続させ、Tank在庫を自然に消化させる」という改善案も採用。加えて「1 lot = 平均的な原油タンカー1隻分」という物理単位でlot粒度を再定義する提案があり、Crude_ME→Refinery_Localの原油輸送レグに限定して採用（小売側は既存の抽象lot単位を維持、というスコープ限定は大杉さんの了承のもと今回はClaude側の判断で決定）。

**モデル構造**（既存の`Gasoline_Local`/`Gasoline_Import`とは完全に独立な、追加の2 SKU）:
```
Gasoline_Local_Hormuz: Crude_ME_Hormuz(leaf_in, ラスタヌラ想定 26.70/50.20, lt_wks=3)
                          → Refinery_Local_H(mom, 千葉近郊, cap_hard=8lot/wk→W15以降1lot/wk)
                          → Tank_Local_H(dad, ss_days=28[4週間の戦略備蓄], buffering_stock_flag=1)
                          → Retail_Local_H_KANTO/KANSAI/CHUBU(leaf_out)

Gasoline_Local_RedSea:  Crude_ME_RedSea(leaf_in, ヤンブー想定 24.09/38.06, lt_wks=5)
                          → Refinery_Local_R(mom, 千葉近郊, cap_hard=5lot/wk終始一定)
                          → Tank_Local_R(dad, ss_days=28, buffering_stock_flag=1)
                          → Retail_Local_R_KANTO/KANSAI/CHUBU(leaf_out)
```
Refinery_Local_H/RはCLAUDE.mdの既存記法通り「同一の物理拠点だが別SKU＝別ノードとして複製」というCookie/EVで確立済みのパターンを踏襲（1ノードを複数productで共有する仕組みは未検証のため踏み込まなかった）。

**lot粒度**: 1 lot = 100,000 bbl 原油換算（≈15,900 kL）。VLCCではなくSuezmaxクラスの部分カーゴを想定（Red Sea/紅海ルートは喫水制限でVLCCが使えないため、より現実的）。この結果、ppc_supplier_cost/ppc_market_price/ppc_node_cost_ruleの金額は既存Local/Import SKU（kL単位相当、lotあたり数万〜十数万円）と比べて桁違いに大きい（crude供給コスト≈$7.8M〜8.5M/lot、小売価値換算で≈27億円/lot）。これは意図的な設計で、実際のタンカー規模の原油貿易金額を反映した結果であり、モデルの誤りではない旨をCSVのdescription列とここに明記。

**需要のchoreography**（週次lot数、KANTO=4/KANSAI=2/CHUBU=2=計8lot/wk基準）:
- W01-W14: Hormuz側=フル稼働(8/wk)、RedSea側=0（休眠）
- W15（ホルムズ海峡封鎖発生）: Refinery_Local_Hのcap_hardが8→1に低下（`holiday_calendar.csv`の`supply_closure`、cap_hard=0.0の意味論的曖昧さ問題を回避するため既存Refinery_Outageと同じ「0ではなく小さい正の値」ワークアラウンドを踏襲）
- W15-W18（4週間のtail）: Hormuz側demandは1.0→0.75→0.5→0.25と逓減させ、Tank_Local_Hの4週間分バッファ在庫を自然に消化。RedSea側demandは0→0.25→0.5→0.75と逆に立ち上げ、両者の合計が概ね一定（総需要は変わらない）になるよう設計
- W19以降: Hormuz側demand=0（ルート放棄）。RedSea側demandは封鎖前と同じフル目標値(8/wk)に設定——ただしRefinery_Local_Rのcap_hardは5/wkで頭打ちのため、意図的に「需要が供給能力を恒常的に上回る」状態を作り、紅海パイプラインの物理的な容量制約（正規ルートの約6割程度しか代替できない）を表現

**検証結果**（`run_oil_headless.py`拡張トレースで確認）:
- Refinery_Local_Hのcap_hardは想定通りW14の8からW15以降1に切り替わる（既存のRefinery_Local Maintenanceパターンと同じ、信頼できる挙動）。
- Tank_Local_Hのpsi4supply[w][S]はW15-W18で8→6→4→2→0と滑らかに逓減し、[I]も12→6→2→0と減少——「供給停止後も在庫を使い切りながら緩やかにゼロへ収束する」という設計意図通りの挙動を確認。
- Refinery_Local_Rはcap_hard=5固定のもとdemand.P/supply.Pともに5で安定（BackwardPlannerの`_apply_mom_cap_backward`がMOM側で先にcapへクリップするため、demand.P自体が5に収まる——「demand.P vs supply.Pのギャップ」で資源制約を可視化する当初の想定とは異なる形になったが、代わりに下流のTank_Local_RのCOが可視化の役割を果たした）。
- **重要な発見**: Tank_Local_RのCOは、W25=2 → W30=17 → W40=47 → 2028-W01=86 と週を追うごとに単調増加した。これは今回のシナリオが「恒久的な構造的供給不足」（RedSeaのcap_hard=5がW19以降ずっと需要8を下回り続ける）であるため。Refinery_Outageで見られた「CO凍結」現象（2026-07-07の別セクション参照）は**一時的な閉鎖**（2週間）だった場合に限られる観察だった可能性が高く、**恒久的な供給不足の場合はCOが正しく・視覚的にも成長し続けることを確認**。これはCO凍結問題の完全解明ではないが、少なくとも「今回のRedSeaシナリオでは実用上問題なく資源制約の帰結を可視化できる」ことが実証された。記事化においては、このCO成長カーブ（週次3lotずつ積み上がる未充足需要）が「ホルムズ海峡封鎖の恒久的コスト」を定量的に語る主要な数値になる。
- PPC/Node P&L（`ppc_node_pl_summary.csv`）でもCrude_ME_Hormuz/RedSea・Refinery_Local_H/R・Tank_Local_H/Rそれぞれにコストが正しく計上されることを確認（Crude_ME_Hormuz総コスト≈573億円 vs Crude_ME_RedSea総コスト≈2,436億円、稼働期間の違い[Hormuzは約2週間強、RedSeaは約63週間]を反映した妥当な比率）。

**確認状況（2026-07-07、追記）**: 大杉さんがGUIのNetworkタブでGasoline_Local_Hormuz/Gasoline_Local_RedSeaの両方を確認。`Retail_Local_H_CHUBU`（Hormuz、閉鎖後Sが0に落ちる波形）、`Retail_Local_R_CHUBU`（RedSea、W15付近でSが立ち上がる波形）とも正しく需要切替の様子が可視化されていることを確認済み。RedSea側の追加動作確認は大杉さんが引き続き実施中。

**未対応・次回検討事項**:
- Refinery_Local_H/Refinery_Local_Rを本当に「同一物理拠点」として1ノード共有で表現できないか（BOM的な複数leaf_inパターンとは意味が異なる、真の「代替ルート」ケースでの1ノード共有の可否）は未検証のまま。
- 北米市場（自国産シェールオイル中心、輸入依存度が低い市場）を3市場目として追加するかどうかは、大杉さんとの合意通りまだ未着手（日本市場+Hormuz/RedSea拡張を優先）。
- World Mapでの表示確認（Gasoline_Local_Hormuz/Gasoline_Local_RedSeaをSKUフィルタで選択した際、Crude_ME_Hormuz(ラスタヌラ)とCrude_ME_RedSea(ヤンブー)が地理的に異なる地点として正しく描画されるか）は未確認——次回GUI起動時に確認する価値がある。
- `gen_oil_model.py`/`gen_oil_model_eu_patch.py`/`run_oil_headless.py`は引き続き`/sessions`側のスクラッチ領域にのみ存在し、リポジトリ未コミット（既出の「手動probeスクリプトが毎回消えている」問題、今回も未解消）。

---

### 第5回note記事: 欧州市場（`data/sample/oil-global-2027/`拡張、新規2 SKU追加、完了、2026-07-07）

日本市場（中東原油+シンガポール輸入）に続く2市場目として、欧州市場を追加。既存の`Gasoline_Local`/`Gasoline_Import`/`Gasoline_Local_Hormuz`/`Gasoline_Local_RedSea`はすべて無変更のまま、追加で`Gasoline_EU_Local`/`Gasoline_EU_Import`という2つの独立SKUを新設した（日本市場と同じ「域内精製 vs 越境輸入」の対比構造を踏襲、lotは既存Local/Importと同じ抽象kL単位——Hormuz/RedSeaのタンカー単位とは異なる）。

**モデル構造**:
```
Gasoline_EU_Local:  Crude_ME_EU(leaf_in, カタール沖想定 25.30/51.50, lt_wks=3)
                       → Refinery_EU(mom, ロッテルダム近郊 51.95/4.14, cap_hard=650/wk→W25-27ストライキで25/wk)
                       → Tank_EU_Local(dad, ss_days=7, buffering_stock_flag=0)
                       → Retail_EU_DE/FR/NL(leaf_out)
Gasoline_EU_Import: Refinery_US(leaf_in, テキサス湾岸想定 29.75/-95.36, lt_wks=3)
                       → Import_Hub_EU(mom, ロッテルダム・Europoort想定 51.95/4.10)
                       → Tank_EU_Import(dad, ss_days=21, buffering_stock_flag=1)
                       → Retail_EU_Import_DE_I/FR_I(leaf_out)
```
日本市場は「地政学リスク（ホルムズ海峡封鎖）」がテーマだったのに対し、欧州市場は意図的に異なる撹乱要因として**労働ストライキ**（`holiday_calendar.csv`、Refinery_EU、2027-W25〜W27の3週間、cap_hard 650→25）を採用——フランスの製油所ストライキが実際に繰り返し発生している現実を踏まえた選択。通貨もEUR建て（`ppc_fx_rate.csv`にEUR/JPY=163.0を追加）で日本市場のJPY/USDとは独立させ、原油・輸入品のUSD建て価格とEUR建て小売価格の両方が同一モデル内に混在する構成とした。

**検証結果**（`run_oil_headless.py`拡張、全6 SKU: Gasoline_Local/Import/Local_Hormuz/Local_RedSea/EU_Local/EU_Importを同一パイプラインで一括実行）:
- Refinery_EUのcap_hardはW24以前650、W25-27で25、W28以降650に正しく復帰。demand.P/supply.Pともにcap_hardに追従（gapなし）——既存のRefinery_Local Maintenance・Refinery_Local_H Hormuz closureと同じ、信頼できるパターンで機能することを確認。
- PPC実行後、`ppc_node_pl_summary.csv`にCrude_ME_EU/Refinery_EU/Tank_EU_Local/Refinery_US/Import_Hub_EU/Tank_EU_Importそれぞれの費用、Retail_EU_DE/FR/NL・Retail_EU_Import_DE_I/FR_Iの収益が正しく計上されることを確認（EUR建て金額がppc_fx_rate経由でJPYベース金額に変換されている）。
- Tank_EU_LocalのCOは大きく増加し続ける値（W10=1907→W40=5372）が観測されたが、Refinery_EUのdemand.P/supply.Pにgapが無い（cap_hardに起因する新規の欠品ではない）ことから、これは日本市場のGasoline_Local/Tank_LocalでもすでにCLAUDE.mdに記録済みの「起動ランプ期由来のCO凍結」現象と同種のものである可能性が高い。ストライキ固有の追加欠品ではなく、モデル全体の既知の未解明事象（前セクション参照）に帰着すると考えられ、今回は深追いしていない。

**未対応・次回検討事項**:
- `gen_oil_model.py`本体と`gen_oil_model_eu_patch.py`（欧州追加分、行追記パッチとして別ファイル化）の2スクリプトを順番に実行する必要がある構成になっている。将来的に1本化する価値があるが、今回はマージ時の事故リスクを避けるため分離のまま。
- World Mapでの表示確認（Refinery_EU/Crude_ME_EU/Refinery_USが地理的に正しい位置——ロッテルダム、カタール沖、テキサス湾岸——に描画されるか）は未確認。

---

### 第5回note記事: 米州市場（`data/sample/oil-global-2027/`拡張、新規2 SKU追加、完了、2026-07-07）

日本市場・欧州市場に続く3市場目として、米州市場を追加。既存4 SKU（Gasoline_Local/Import/Local_Hormuz/Local_RedSea）・欧州2 SKU（Gasoline_EU_Local/Import）はすべて無変更のまま、追加で`Gasoline_US_Local`/`Gasoline_US_Import`という2つの独立SKUを新設した（`gen_oil_model_us_patch.py`、既存CSVへの行追記パターンをEUパッチと同様に踏襲）。lotは既存Local/Import/EUと同じ抽象kL単位。通貨はUSD（既存の`ppc_fx_rate.csv`のUSD/JPY行をそのまま再利用、新規FXペア追加不要）。

**モデル構造**:
```
Gasoline_US_Local:  Shale_Permian(leaf_in, パーミアン盆地想定 31.80/-102.00, lt_wks=1[国内短距離])
                       → Refinery_USGulf(mom, テキサス湾岸想定 29.75/-95.36, cap_hard=900/wk→W35-36ハリケーンで45/wk)
                       → Tank_US_Local(dad, ss_days=7, buffering_stock_flag=0)
                       → Retail_US_TX/CA/NY(leaf_out)
Gasoline_US_Import: OilSands_Alberta(leaf_in, アルバータ州想定 56.70/-111.40, lt_wks=2[パイプライン])
                       → Import_Hub_US(mom, クッシング原油ハブ想定 35.98/-96.77, lt_wks=1)
                       → Tank_US_Import(dad, ss_days=14, buffering_stock_flag=1)
                       → Retail_US_Import_MW_I/NE_I(leaf_out)
```

**日本・欧州との差別化ポイント（3市場の物語構造）**:
- 日本＝地政学リスク（ホルムズ海峡封鎖、数ヶ月単位の恒久的迂回）
- 欧州＝労働争議（製油所ストライキ、3週間の一時的closure）
- 米州＝自然災害（メキシコ湾岸ハリケーン、`holiday_calendar.csv`のW35-36に2週間のcap_hard急減 900→45、実際のハリケーン・ハービー/アイダ等でメキシコ湾岸製油所が繰り返し停止してきた現実を踏まえた選択）

さらに米州市場は構造自体が日本・欧州と異なり、**需要の約90%をLocal（国内シェールオイル）が占め、Importは約10%（カナダ産オイルサンド、パイプライン輸送）に留まる**設計とした（`demand_forecast.csv`のbase値: Local=TX450+CA300+NY150=900、Import=MW_I60+NE_I40=100）。日本（Local 570 : Import 130 ≈ 81:19）・欧州（Local 680 : Import 160 ≈ 81:19）と比べて明確に輸入依存度が低く、「自国産資源を持つ市場は地政学リスクに対して構造的に強い」という対比を打ち出せる。また調達コストもOilSands_Alberta（$350/lot）がShale_Permian（$450/lot）より安値設定——WTI対比のカナダ産原油ディスカウント（Western Canadian Select）という実際の市場現象を反映しつつ、USMCA下でCanada→US間は無関税（tariff_rate=0.0、欧州のUS→EU関税2%・日本のSG→JP実質関税3%と対照的）とした。

**検証結果**（`run_oil_headless.py`拡張、全8 SKU一括実行）:
- Refinery_USGulfのcap_hardはW34以前900、W35-36で45、W37以降900に正しく復帰。Tank_US_LocalのIもW34の870からW36に45まで急落し、W38には843まで回復——ハリケーンによる「急激・短期・深刻」な供給ショックが、欧州のストライキ（3週間、6割減）や日本の恒久封鎖よりもさらに鋭い落ち込みとして可視化されることを確認（cap_hardの下げ幅が900→45と実質95%減のため）。
- W37（closure翌週）でdemand.P=0・supply.P=900という一時的な不整合が観測されたが、これは既存のRefinery_Local Maintenance検証時にも見られた回復直後の一時的スナップショット挙動と同種と考えられ、新規のバグではない可能性が高い（深追いはしていない）。
- Node P&L（`ppc_node_pl_summary.csv`）でShale_Permian/Refinery_USGulf/Tank_US_Local・OilSands_Alberta/Import_Hub_US/Tank_US_Importそれぞれのコスト、Retail各chの収益が正しく計上されることを確認。
- サニティチェック: 2027-W20時点のLocal合計972 lots/wk・Import合計108 lots/wkで、設計通り約90:10の比率を維持していることを確認。

**未対応・次回検討事項**:
- `gen_oil_model.py`本体・`gen_oil_model_eu_patch.py`・`gen_oil_model_us_patch.py`の3スクリプトを順番に実行する必要がある構成（既出のマージ保留方針を踏襲）。
- World Mapでの表示確認（Refinery_USGulf/Shale_Permian/OilSands_Albertaが地理的に正しい位置——テキサス湾岸、パーミアン盆地、アルバータ州——に描画されるか）は未確認。
- これで日本・欧州・米州の3市場体制が完成。記事化の段階に進む場合、3市場の対比表（地政学 vs 労働 vs 自然災害、輸入依存度の違い）が構成の軸になる見込み。

---

### 第5回note記事: 「外側シナリオレイヤー」構想とOPEC+協調減産シナリオの実装（`data/sample/oil-global-2027/`拡張、新規エンジンコード無し、完了、2026-07-07）

大杉さんから「Global Oil Caseの需給バランスのポイントはどこにあるのか？OPEC+・BP・エクソンのようなメジャーが、価格を高値維持しつつ需要破壊を招かない匙加減で供給を調整している、というイメージでは？」という指摘があり、現状のoil-global-2027モデルには**その戦略的・双方向フィードバック（供給調整→価格→需要破壊）が一切実装されていないこと**を確認・共有した。WOMのPlanning Engine（BackwardPlanner/ForwardPlanner）は「需要予測と生産能力を与えられたらその通りに計画する」決定論的エンジンであり、価格弾力性つきの経済均衡ソルバーではないため、本格的な双方向ループをエンジン内部に実装するのはスコープ外と判断。

大杉さんの提案：「WOMの内部に新機能を実装するのではなく、WOMの外側にGlobal Oil Production and Priceのシナリオをセットするpluginを用意する。OPEC+シナリオ、BPシナリオ、エクソン・シナリオなど、各プレイヤーが何を目指すかをシナリオとして記述し、WOMの外側に定義できるのではないか」。これを採用し、**エンジンを一切変更せず、既存の`gen_oil_model*.py`パターン（CSV生成スクリプト）を拡張する形でOPEC+シナリオを1本、具体的に実装した**（`gen_oil_model_opec_patch.py`）。

**設計**: WOMの既存Pluginシステム（`HookBus`、`HOOK_PRE_PLAN`等）はエンジンの「内側」で`sc_tree`を直接操作する仕組みだが、今回の「外側シナリオレイヤー」はその手前、WOMが読み込む入力CSV（`holiday_calendar.csv`／`ppc_supplier_cost.csv`／`demand_forecast.csv`）を生成する外部スクリプトとして実装した。WOM本体（BackwardPlanner/ForwardPlanner/PPC/HolidayCalendarPlugin）は一切無変更。

**シナリオ内容**（既存のGasoline_Local/Gasoline_EU_Localに追加データを重ねる形、新SKUは追加しない）:
1. **OPEC+協調減産**: `holiday_calendar.csv`に`Refinery_Local`（Gasoline_Local）と`Refinery_EU`（Gasoline_EU_Local）の同時cap_hard削減行を追加（2027-W45〜W50の6週間、650→500、約23%減産）。両方ともCrude_ME系（中東原油）を調達源とする2市場を同時に絞ることで「OPEC+の協調行動」を表現。
2. **価格スパイクと恒久的な価格フロア上昇**: `ppc_supplier_cost.csv`にCrude_ME/Crude_ME_EUの価格行を追加（減産開始と同時に$680/$690へスパイク、減産終了後の2028-W01に$540/$550へ部分的に緩和——ただし元の水準$500/$510より高い「恒久的に切り上がった床」として着地させ、日本市場のRedSeaシナリオで確認済みの「一時的措置が恒久コストに転化する」パターンを踏襲）。
3. **非OPEC+のスイング供給者としての米国シェール**: `holiday_calendar.csv`に`Refinery_USGulf`（Gasoline_US_Local）のcap_hard**増加**行を追加（OPEC+減産開始から2週遅れの2027-W47〜2028-W02、900→1000、約11%増産）。HolidayCalendarPluginの`supply_closure`エフェクトはcap_hardを指定値に単純上書きするだけの仕組みのため、より高い値を指定すれば増産としても機能することを確認——エフェクト名は「閉鎖」だが実体は汎用cap_hard上書きである点に注意。
4. **遅行する需要破壊**: `demand_forecast.csv`のGasoline_Local（KANTO/KANSAI/CHUBU）・Gasoline_EU_Local（DE/FR/NL）の既存デマンド行を、価格スパイクから5〜7週遅れて効いてくる需要減少係数（1.00→0.85まで4週かけて低下、4週間トラフを維持、その後4週かけて1.00へ回復）で直接書き換え。これは本セッション初の「既存デマンド行を後から係数で書き換える」パターン（他は全て新規行の追記のみだった）。

**実装時のバグと修正**: 最初の実装では減産開始週（2027-W45）から起算して「+8週目=2027-W53」のように単純に西暦年+週番号の文字列を組み立てたが、**2027年はISO週が52週しかなく、53週目は「2027-W53」ではなく「2028-W01」にロールオーバーする**ため、存在しない週ラベルを`demand_forecast.csv`の書き換えに使ってしまい、84行が調整されるべきところ18行しか一致しなかった（サイレントに大半のデータが素通りするバグ）。`week_list()`関数の出力を実際に確認して正しいラベル（2028-W01〜2028-W11）に修正し、全CSVを`gen_oil_model.py`→`gen_oil_model_eu_patch.py`→`gen_oil_model_us_patch.py`→`gen_oil_model_opec_patch.py`の順でクリーンに再生成することで解消した。**次回、週番号を跨ぐ期間指定を行う際は`week_list()`の実出力を必ず確認すること。**

**検証結果**（`run_oil_headless.py`拡張、全8 SKU一括実行）:
- Refinery_Local/Refinery_EUのcap_hardはW45-50でともに650→500に正しく低下、W51で650に復帰（demand.P/supply.Pともに追従、Refinery_EU側はgapなしのクリーンな復帰、Refinery_Local側はW51直後に既知の一時的スナップショット不整合あり——他の閉鎖イベントでも見られる既存パターンで新規バグではない）。
- Refinery_USGulf（非OPEC+シェール）のcap_hardは2027-W47（OPEC+減産開始の2週後）に900→1000へ上昇し2028-W02まで持続、2028-W03に900へ復帰——「OPEC+減産に対して、コストが低く機動力のある非加盟プレイヤーが遅れて増産で応答する」という設計意図通りの挙動を確認。
- Retail_Local_KANTOのdemand.Sは、2027-W51=280 → 2028-W04=247（トラフ、-12%程度）→ 2028-W13=316（回復、季節要因も加わり元水準を上回る）と、価格スパイクに遅行する形で明確な需要破壊カーブを描くことを確認。
- Crude_ME/Crude_ME_EUの価格系列も設計通り：$500(W01)→$650(W20)→$520(W31)→$680(W45、OPEC+スパイク)→$540(2028-W01、部分緩和・恒久的に切り上がった床)。Crude_ME_EUも同様に$510→$690→$550。

**確認できたこと（大杉さんの問いへの回答）**: 「WOMのエンジンを変えずに、外側のシナリオ生成スクリプトだけでOPEC+的な戦略的供給管理（協調減産・価格スパイク・非加盟プレイヤーのスイング供給・遅行する需要破壊）を一通り表現できる」ことを実証した。これはWOM本体の疎結合設計（`holiday_calendar.csv`・`ppc_supplier_cost.csv`・`demand_forecast.csv`という素直なCSVインターフェース）のおかげであり、既存のHookBus Plugin（エンジン内部）とは異なる、もう一段外側の「シナリオレイヤー」として明確に区別できる設計であることが確認できた。

**未対応・次回検討事項**:
- 今回はOPEC+シナリオ1本（Crude_ME/Crude_ME_EUのみ）に限定。Gasoline_Import/Gasoline_EU_Import（シンガポール・米国精製品）やGasoline_US_Local/Import（シェール・カナダ産）への波及（グローバル原油価格上昇が間接的に全SKUへ波及する効果）は意図的に対象外とした。
- BP/エクソンのような「個別メジャーのプレイヤーシナリオ」や、複数プレイヤーの意思決定が絡む汎用フレームワーク化（価格バンド・反応ルール・弾力性パラメータを設定ファイル化する等）は、今回は見送り、大杉さんとの合意通り「まず1本の具体例を作る」を優先した。汎用化する場合の設計候補は、プレイヤーごとの価格バンド・供給反応ルール・需要弾力性パラメータを定義し、それらから週次CSVを計算する小さな「シナリオコンパイラ」を作ること。
- `gen_oil_model_opec_patch.py`も引き続き`/sessions`側のスクラッチ領域にのみ存在し、リポジトリ未コミット。

---

## v1r0m2 実装済み機能（新しいClaude君へ）

### JIT週次同期：cap_hard envelope in `_in_propagate`（commit 7a22648）【v1r0m3で廃止】

~~v1r0m2 で `_in_propagate` に cap_hard envelope を追加し、上流伝播をクリップしていた。~~

**v1r0m3 で廃止**。BackwardPlanner は純粋な需要逆伝播（LT offset のみ）とする方針に変更。
cap_hard enforcement は `_apply_mom_cap_backward`（MOM 専任）と ForwardPlanner に移譲した。
これにより上流ノードは cap 前の全量需要を受け取り、ForwardPlanner が supply allocation を判断できる。

---

### DBR設計：PUSH/PULL break-point at Buffer_Wafer_TW（commit 7a22648）

iPhone Global SC の InBound チェーン：

```
SiliconWafer_TW (leaf_in, PUSH sub)
  → Buffer_Wafer_TW (decoupling node, PUSH) ← PUSH/PULL break-point
    → TSMC_TW (PULL)
      → Foxconn_CN (PULL MOM, Drum)
```

- **Drum**: Foxconn_CN（cap_hard staircase: 800→534→267→0/wk）
- **Buffer**: Buffer_Wafer_TW（在庫クッション、DBRバッファ）
- **SiliconWafer_TW**: 自律PUSH（ウェーハFab = 高固定費・常時稼働型）

`push_config.csv` でBuffer_Wafer_TWをdecoupling nodeとして設定。

---

### Mode 4 LT-shifted PUSH：`push_lead_time_weeks`（commit f9ebc37）

`wom/engine/push_pull.py` の `PushConfig` に `push_lead_time_weeks` フィールドを追加。
`push[w] = demand_ref_node.psi4demand[w + LT][S]`

この1パラメータで**DBRバッファの完全なライフサイクルPSIパターン**が自動生成される：

| フェーズ | 期間 | 動作 |
| :---- | :---- | :---- |
| Pre-build | demand[w]=0, demand[w+LT]>0 | 生産開始、バッファ積み上がり（差分が積み上がる） |
| Steady | demand[w] == demand[w+LT] | 生産=消費=staircase、バッファ平坦 |
| Staircase gap | demand[w+LT] < demand[w] | 生産が先行してステップダウン、バッファが差分を吸収 |
| EOL stop | demand[w+LT]=0, demand[w]>0 | 生産停止、バッファが最終需要を賄いゼロに収束 |

**iPhone16モデルでの設定** (`push_config.csv`)：

```csv
push_lead_time_weeks=26
mom_ref_node_id=""（decoupling node自身 = staircase信号を使用）
```

**確認済み波形** (Buffer_Wafer_TW PSIチャート)：
- 2026-W01〜W27: 生産ゼロ（demand[w+26]がまだ0）
- 2026-W28〜W52: 800/wk pre-build、I上昇（〜20,800 lots）
- 2027-W01〜: P=S=800/wk（平坦）
- 2027-W40〜: 生産534 < 消費800、I段階的低下
- 2030-W13付近: I→0（製品ライフサイクル終了と同時に自然消滅）

Foxconn_CN の生産シフトが TSMC_TW 経由で Buffer_Wafer_TW の在庫減少パターンとして
伝播する「SC lane node間のPSI連動」を実現。

**push_config.csv スキーマ（全フィールド）**：

| フィールド | 説明 |
| :---- | :---- |
| `node_id` | decouplingノードのnode_id |
| `push_qty_per_week` | Mode1: 固定週次生産量（>0でMode1） |
| `buffer_lots` | Mode2/3: 目標バッファ在庫 |
| `mode_only` | plan_modeフラグのみ設定（P-schedule上書きなし） |
| `mom_ref_node_id` | Mode2: 需要参照ノード（空=decoupling node自身） |
| `pre_build_qty_per_week` | Mode3: Phase1固定生産量 |
| `pre_build_end_week` | Mode3: Phase1終了週ラベル（例: "2026-W52"） |
| `push_lead_time_weeks` | Mode4: LTオフセット週数（優先度最高） |

**Mode選択ロジック**：
1. `push_qty_per_week > 0` → Mode 1（固定）
2. `push_lead_time_weeks > 0` → Mode 4（LT-shifted、最優先）
3. `pre_build_qty_per_week > 0` AND `pre_build_end_week` → Mode 3（時間軸分割）
4. それ以外 → Mode 2（古典的補充）

---

## v1r0m3 実装済み機能（新しいClaude君へ）

### MOM Constrained Demand Allocation（`_apply_mom_cap_backward`）

`wom/engine/backward_planner.py` に Phase 3b として `_apply_mom_cap_backward()` を追加。
`mom_constrained=True`（デフォルト）のとき、BackwardPlanner が MOM ノードの `psi4demand[w][P]` を cap_hard でクリップし、オーバーフロー分を CO として前週の S に押し戻す。

**設計意図（Plan Transforming Hypothesis）**: BackwardPlanner = Constrained Demand Allocation。
MOM ノードで cap_hard クリップ + CO前倒しを行うことで、`psi4demand[w][P]` = cap_hard 以内の実行可能計画が生成される。ForwardPlanner は（理想的には）この計画をコピーするだけで CO を発生させない。

```python
def _apply_mom_cap_backward(self, node, n_weeks, result):
    if node.node_type != "mom":
        return
    for w in range(n_weeks - 1, -1, -1):
        cap_w = node.cap_hard(w)
        if cap_w <= 0.0:
            continue
        s_lots = list(node.psi4demand[w][S])
        cap_int = int(cap_w)
        if len(s_lots) <= cap_int:
            continue
        within_cap = s_lots[:cap_int]
        overflow   = s_lots[cap_int:]
        node.psi4demand[w][P].clear()
        node.psi4demand[w][P].extend(within_cap)
        for lot_id in overflow:
            node.psi4demand[w][CO].append(lot_id)
        if w > 0:
            for lot_id in overflow:
                node.psi4demand[w - 1][S].append(lot_id)
        else:
            for lot_id in overflow:
                result.record_past_due(node.node_id, lot_id, w)
```

**`mom_constrained` フラグ**:
- `True`（デフォルト）: v1r0m3 動作。MOM cap_hard クリップ実行。
- `False`: v1r0m2 互換。既存テスト（`test_step7_capacity.py`, `test_step8_push_pull.py`）は `config={"mom_constrained": False}` で実行し v1r0m2 セマンティクスを保持。

### ForwardPlanner: PUSH MOM への Demand-S copy 除外

`wom/engine/forward_planner.py` の Phase 1 InBound 処理で、`plan_mode="push"` の MOM ノードには Demand-S copy（`psi4supply[w][P] = psi4demand[w][P]`）を適用しない条件を追加。Buffer_Wafer_TW（`plan_mode="pull"`, `is_decoupling=True`）には引き続き Demand-S copy が適用される。

---

### BackwardPlanner 純粋化：`_in_propagate` からクリッピング削除（v1r0m3後期）

**背景**: v1r0m2 の cap_hard envelope（`_in_propagate` 内のクリッピング）は、上流ノードが cap 前の全量需要を受け取れないという問題を持っていた。MOM の形状（CO あり）と TSMC_TW の形状（クリップ済み）が「少し異なる」という Osugiさんの観察がトリガー。

**変更内容**:
- `_in_propagate` の cap_hard clipping と is_decoupling fill-up ロジックを削除
- 純粋な LT offset 伝播のみ残す
- cap_hard enforcement は `_apply_mom_cap_backward`（MOM 専任）が担当
- 上流ノードは cap 前の全量需要を受け取り、ForwardPlanner が supply allocation を判断

**テスト更新**:
- `test_step7_capacity.py` の `cap_hard_sealed` 期待値を `0` → `2` に変更
  （v1r0m2: BackwardPlanner がクリップ → sealed=0 → v1r0m3: ForwardPlanner が enforce → sealed=demand-cap）

### DebugPanel PSI グラフに Capacity Line 追加（v1r0m3後期）

`app.py` の `_draw_psi_subplot` に `cap_values` 引数を追加。
`_refresh_charts` で `dbg.get_node(product, node_name)` から cap_hard を週次リストとして取得し、
グラフ左軸（lots）に橙色破線のステップ関数として描画する。

```python
# In _refresh_charts:
node_obj  = dbg.get_node(product, node_name)
cap_values = [node_obj.cap_hard(w) for w in range(n_weeks)] if node_obj else None

# In _draw_psi_subplot: step-line where cap > 0
if cap_values and any(v > 0 for v in cap_values):
    # cap_x/cap_y: horizontal segments, NaN breaks for cap=0 weeks
    ax.plot(cap_x, cap_y, color="#FF9800", linestyle="--", linewidth=1.2, label="Cap. Hard")
```

### app.py: v1r0m3 タイトル更新 + デフォルトサンプルパス修正

- タイトルバーを `v1r0m2` → `v1r0m3` に変更（3箇所）
- `_sample_dir` を `data/sample` → `data/sample/iphone-2027-2029` に変更（直下に `sc_tree_master.csv` がないため）

---

## v1r0m2 設計課題：Lead Time offset と DAD 回転在庫

### 背景（PySI v0r8 からの継承設計思想）

PySI v0r8 では BackwardPlanner が各エッジの Lead Time（LT）オフセットを計算する際、
Holiday Calendar の Long Holiday フラグを参照して閉鎖週をスキップする処理を
Planning Engine 内部で行っていた。
現行 WOM v1r0m1 ではこの LT offset が未実装であり、全ノードが同一週に
需要が発生するように扱われている（設計上の制約）。

### 現状の制約

- DADノード（DC等）の `psi4supply[w][I]` は常に 0（pass-through 設計）
- BackwardPlanner は LT オフセットなしで需要を逆伝播するため、
  上流ノードほど早い週に需要が配置されるべき「market requesting position」が
  正しく計算されていない
- 例: Week 10 に Retail_AMER で需要 100 lots、DC→Retail LT=1週、
  Foxconn→DC LT=2週 の場合、本来は Foxconn に Week 7 の需要として伝播すべきだが、
  現状は全ノードが Week 10 に配置される

### v1r0m2 向け役割分担設計

#### BackwardPlanner（LT計算 + Holiday Calendar 参照）
- LT オフセット計算: `week_idx -= lead_time_weeks`
- 閉鎖週スキップ: `explicit_closures`（PlanningContext 経由）を参照し、
  LT 計算中に閉鎖週があれば実質 LT を加算して正しい週に需要を配置する
  例: LT=2、W9 が閉鎖週 → W10 の需要を W7 に配置（閉鎖週1週分を追加オフセット）
- 責任範囲: 市場要求ポジションの正確な配置

#### HolidayCalendarPlugin
- `HOOK_PRE_PLAN (on_pre_plan)`:
  - cap_hard 設定（ForwardPlanner への能力制約）
  - `explicit_closures dict` を `PlanningContext` に書き込む
    （BackwardPlanner が参照するための共有データ）
- `HOOK_POST_BACKWARD (on_post_backward)`:
  - BackwardPlanner が誤って閉鎖週に配置した P-lot の残余修正（フォールバック）
- 責任範囲: ForwardPlanner の能力制約が主担当

#### ForwardPlanner（v1r0m2 で拡張）
- cap_hard に従って CO 生成（現行）
- DAD ノードの在庫計算を追加（`psi4supply[w][I]` が 0 固定から解放）
- `sc_tree_to_planning_df()` を DAD ノードも KPI 対象に拡張

#### 疎結合の維持方法
BackwardPlanner が HolidayCalendarPlugin のインスタンスに直接依存しないよう、
`sc_tree` または `PlanningContext` に `explicit_closures dict` を事前書き込みし、
BackwardPlanner はそれを参照するだけにする。

### 実装時のパフォーマンス考慮事項

`explicit_closures` は `dict[node_name, set[week_idx]]` 構造であり、
`week_idx in explicit_closures.get(node_name, set())` の lookup は O(1)。
ただし 156週 × 全ノード × 全 Lot のループ内での判定となるため、
以下の最適化を検討すること：
- `explicit_closures` は HOOK_PRE_PLAN で一度だけ構築し、計画期間全体で再利用
- BackwardPlanner 内では node ごとに closure_set を変数にキャッシュしてループ内参照を最小化
- 閉鎖週のない node（closure_set が空）は判定処理をスキップ

### 影響ファイル（v1r0m2 実装時）

| ファイル | 変更内容 |
| :---- | :---- |
| `sc_tree_master.csv` | `lead_time_weeks` 列追加（エッジ属性） |
| `wom/engine/backward_planner.py` | LT オフセット付き需要逆伝播 + 閉鎖週スキップ |
| `wom/engine/forward_planner.py` | DAD ノード在庫計算追加 |
| `wom/engine/holiday_calendar_plugin.py` | `explicit_closures` を PlanningContext に書き込む処理追加 |
| `wom/model/sc_tree.py` | エッジ属性として LT 保持 |
| `wom/engine/sc_tree_to_df.py` | DAD ノードも KPI DataFrame 対象に拡張 |

---

## WOM Original KPI Framework

### 設計思想：3次元 KPI アーキテクチャ

従来の財務 KPI ツリーは「財務指標 → 現場指標」へのトップダウン分解（静的・2次元）。
WOM の KPI フレームワークは根本的に異なる 3 次元構造を持つ：

```
次元1（空間軸）: SC Node  leaf_in → MOM → supply_point → DAD → leaf_out
次元2（財務軸）: KPI     現場活動指標 → 中間KPI → 事業損益 → 資本効率(ROE)
次元3（時間軸）: PSI週次  Week 1 → Week 2 → ... → Week 156（アニメーション可能）
```

静的な財務報告ではなく、**サプライチェーンの因果連鎖が時間軸で動く "活きた KPI"** を実現する。

---

### WOM SC Node × KPI マッピング

#### leaf_in（原材料・調達ノード）
調達起点の現場活動指標：

| WOM 指標 | PSI バケット | 上位 KPI への接続 |
| :---- | :---- | :---- |
| 調達 Lead Time (週) | P バケット配置週 | 工場部材在庫日数 → 棚卸資産回転日数 |
| サプライヤー納入精度 | P 実績 vs 計画差 | 欠品率 → 在庫補償費比率 |
| 調達ロック期間 (週) | 計画確定ホライズン | 部品関連変化対応率 → 販売機会損失率 |
| 調達単価 | ppc_supplier_cost | 直材費比率 → 売上原価率 |

#### MOM（製造・産地集荷ノード）
製造起点の現場活動指標：

| WOM 指標 | PSI バケット | 上位 KPI への接続 |
| :---- | :---- | :---- |
| 製造 Lead Time (週) | P→I バケット幅 | 工場仕掛在庫日数 → 棚卸資産回転日数 |
| 工場安全在庫日数 | `psi4supply[w][I]` / 週次出荷 | 棚卸資産回転日数 → 資産コスト |
| 生産能力充足率 (Fill Rate) | P 実績 / P 計画 | 欠品率 → 販売機会損失率 |
| 製造ノードコスト | ppc_node_cost_rule | 労務費比率 → 売上原価率 |
| Air 輸送発生率 | edge_cost（Air シナリオ） | Air コスト比率 → 物流コスト比率 |

#### supply_point（HQ Bridge ノード）
全体最適の調整指標：

| WOM 指標 | 役割 | 上位 KPI への接続 |
| :---- | :---- | :---- |
| Multi-MOM 配分比率 | lane_assignment.csv | 物流コスト比率・製造コスト比率 |
| Scenario Delta (Upside/Downside) | シナリオ感応度 | 変化対応率 → 販売機会損失率 |
| Tariff & FX 影響額 | Landed Cost engine | 売上原価率・物流コスト比率 |

#### DAD（DC・流通在庫ノード）
※ v1r0m1 現在 pass-through 設計。v1r0m2 で回転在庫を実装予定。

| WOM 指標 | PSI バケット | 上位 KPI への接続 |
| :---- | :---- | :---- |
| 販社在庫日数（回転在庫） | `psi4supply[w][I]`（v1r0m2〜） | 棚卸資産回転日数 → 資産コスト |
| DC → Retail 輸送 LT | エッジ属性（v1r0m2〜） | 販社配送 LT → 販社在庫日数 |
| DC スループット (週次) | S バケット | 物流コスト比率 → 販管費比率 |

#### leaf_out（販売チャネル・需要ノード）
市場起点の販売指標：

| WOM 指標 | PSI バケット | 上位 KPI への接続 |
| :---- | :---- | :---- |
| 需要予測精度 | demand_forecast vs 実績差 | 販売予測精度 → 変化対応率 |
| Fill Rate (充足率) | S 実績 / S 計画 | 販売機会損失率 → 売上高成長率 |
| Sell-through サイクル (週) | S バケット連続性 | デイリー在庫日数 → 販社在庫日数 |
| 販売チャネル Revenue | ppc_market_price × S | 売上高 → 事業損益 |
| Gross Profit / Profit Zone | PPC engine 出力 | 事業利益 → ROE |

---

### WOM KPI 集約ツリー（SC Node ボトムアップ → 財務 KPI）

```
ROE
├─ 事業損益（PPC engine が週次計算）
│   ├─ Revenue（売上高）
│   │   └─ 売上高成長率
│   │       ├─ Fill Rate（leaf_out: S実績/S計画）       ← 販売機会損失率
│   │       ├─ 需要予測精度（leaf_out: 予測vs実績）      ← 変化対応率
│   │       └─ Scenario Upside/Downside 感応度          ← 変化対応率
│   ├─ COGS（売上原価）
│   │   └─ 売上原価率
│   │       ├─ 直材費比率（leaf_in: ppc_supplier_cost）
│   │       ├─ 労務費比率（MOM: ppc_node_cost_rule）
│   │       └─ Tariff & FX 影響（supply_point: Landed Cost）
│   └─ 物流・販管費
│       └─ 物流コスト比率
│           ├─ Air コスト比率（MOM: edge_cost Air シナリオ）
│           └─ 通常輸送コスト（DAD: edge_cost Base シナリオ）
│
└─ 資産コスト（棚卸資産回転日数が主ドライバー）
    ├─ 棚卸資産回転日数
    │   ├─ 工場安全在庫日数（MOM: psi4supply[w][I] / 週次S）
    │   ├─ 工場仕掛在庫日数（MOM: 製造LTから算出）
    │   ├─ 販社在庫日数（DAD: psi4supply[w][I]、v1r0m2〜）
    │   └─ 工場部材在庫日数（leaf_in: 調達LTから算出）
    ├─ 売上債権回転日数
    │   └─ Sell-through サイクル（leaf_out: S バケット）
    └─ 固定資産
        └─ 製造設備稼働率（MOM: cap_hard 充足率）
```

---

### WOM KPI の時間軸展開（3次元目）

上記ツリーの各指標は **週次 PSI アニメーション**と連動する：

```
Week t の ROE 分解：
  Revenue[t]   = Σ leaf_out.psi4supply[t][S] × market_price
  COGS[t]      = Σ leaf_in.psi4supply[t][P]  × supplier_cost
                + Σ node.psi4supply[t][P]     × node_cost
  在庫資産[t]  = Σ MOM.psi4supply[t][I]      × unit_cost   （現行）
               + Σ DAD.psi4supply[t][I]      × unit_cost   （v1r0m2〜）
```

**これにより達成できること：**
- 特定週の Supply Shock（台風・関税引上げ）が ROE に波及するまでの因果連鎖を可視化
- Scenario Delta（Upside/Downside）が財務 KPI に与える感応度をアニメーションで確認
- 在庫日数の週次推移から「どの Node・どの週に在庫コストが集中するか」を特定

---

### v1r0m2 以降の実装優先度（KPI 完全性の観点から）

| 優先度 | 実装内容 | 解決する KPI ギャップ |
| :---- | :---- | :---- |
| ★★★ | DAD 回転在庫（`psi4supply[w][I]`） | 販社在庫日数 → 棚卸資産回転日数 |
| ★★★ | Lead Time offset（BackwardPlanner） | 工場部材在庫日数・工場仕掛在庫日数 |
| ★★  | Fill Rate の週次 KPI タブ表示 | 販売機会損失率の定量化 |
| ★★  | 棚卸資産回転日数の Management タブ追加 | 資産コスト → ROE 接続 |
| ★   | 需要予測精度の週次トラッキング | 変化対応率の定量化 |

---

## v1r2m0：soysauce ケース ＋ 関税マスタ統一 Phase 1/2/3（完了、2026-07-25）

branch `wom-v1r2m0`。デモ動画（経営者・コンサル向け、国産醤油の対米/対欧輸出）用の新ケースと、その構築中に大杉さんが発見した「Management と PPC で金額（Revenue/GM/Tariff）が一致しない」問題の解決。設計提案は `requests/tariff-lane-master-unification-request-letter.md`（§11 に実装結果と設計逸脱を記録）。

### 新ケース：soysauce-us-2027（S1）/ soysauce-eu-2027（S2）
- 二本木：`Materials_JP`(leaf_in) → `Brewing_Noda` → `Bottling_Noda`(終端mom) → `SP_Soy` → `FG_WH_Noda`(dad, ss=21) → {`DC_US_SF`/`DC_US_NY`/`DC_JP`/`DC_EU_RTM`}(dad) → `Rest_*`(leaf_out)。MOM＝千葉県野田（仮想座標）。海上LT：US 5/6週、EU 6週。1製品 `Soy_Sauce`、104週（2027-W01〜2028-W52）。
- **S1（米国集中）= us フォルダ**：需要 JP300/US_W350/US_E350/EU0（312 lot-records）。**S2（欧州分散）= eu フォルダ**：JP300/US175×2/FR150/BE100/NL100（624 lot-records）。両フォルダは sc_tree 同一、`demand_forecast.csv` の配分だけが違う（case1 方式）。
- **重要（GUI キャッシュ挙動）**：フォルダ切替のみ（`python -m main` 再起動なし）だと前フォルダの output/ppc が残り、Management に古い値が出る。ケースを替えたら必ず `python -m main` を再実行して初期化すること（大杉さんと確認済み）。
- 匿名化：企業名を出さず「国産醤油」で統一。関税率・価格は例示（HS2103.10、対米 12.5%、対欧 8%）。sku_master は6 region 行（USD建て：US $40 / JP $24 / EU $41、unit_cost $16）。**当初 sku_master が JP 1行だけで US/EU 市場に価格が付かず US=EU 同値になるバグ**があり、6 region 行に拡張して解消。

### Phase 1：関税マスタの canonical 化（`tools/gen_tariff_edges.py` 新設）
- 関税は「HSコード（製品）× 原産国 × 仕向国 × scenario」で一意。`edge_cost_master.csv` に **`product_id` 列を追加**して canonical 化（`trade_lane_master.csv` への改称は後方互換のため見送り。product_id 空欄＝全product wildcard）。
- `gen_tariff_edges.py`：`sc_tree_master` + `ppc_node_profit_zone`(node→country) + `route_master`(hs_code) + canonical `edge_cost_master`(scenario別) から、DC→leaf_out エッジ用の per-edge `ppc_tariff_rule.csv` を生成。`from_country`=終端MOMの国、`to_country`=市場国。`--check` で生成物と既存手作りファイルの一致を確認（US/EU とも **0 diffs**）。`ppc_tariff_rule.csv` は「生成物（正典は canonical）」の位置づけ（手編集しない）。
- 使い方：`python -m tools.gen_tariff_edges --model-dir data/sample/soysauce-us-2027 [--scenario Base] [--check]`。

### Phase 2：Management の金額を PPC 台帳から導出（`wom/gui/app.py`、GUI 層オーバーレイ方式）
`money.py` を置換せず、**GUI 層（`ManagementCockpitPanel`）で台帳値をオーバーレイ**する方式に変更（money.py 無変更、後方互換）。単一 Lot_ID 台帳（`ppc_kpi_summary.json` / `ppc_node_pl_summary.csv`）を唯一の真実源にした。
- `_ledger_pl_for_sku()`（増分1）：**P&L Summary** の Revenue/COGS/GP/GM を台帳から取得。sku=All は json、特定sku は `ppc_node_pl_summary.csv` を集計。運転資本（Inv/CCC/AR/AP）は貸借項目のため money 由来のまま据え置き（意図的）。→ GM が 55%（money誤り）から **US 25.9% / EU 26.2%** へ、PPC と一致。
- `_ledger_lc_overrides()`（増分2）：**Landed Cost パネル** の Revenue/Customs/Landed GM%/ΔMargin/Tariff% を台帳から再導出。`ppc_node_profit_zone.csv`（node→country）と `mgr.lc_scens`（scenario別レート）を使い、チャネル別 `tariff_base` を `rate(scenario)/rate(Base)` で再スケール。Freight はスイープ不変の情報列として money 由来を据え置き（台帳 cost に内包済み＝二重計上回避）。ΔMargin の意味を「landed − gross(55%)」から「vs Base シナリオ」へ変更。
- `mgr.model_dir` を planning 完了時にセット（`_ledger_lc_overrides` が profit_zone を読むため、app.py の planning-done パスに追加）。

### Phase 3：関税感応度の lot 精度化（Phase 2 増分2 で同時達成）
Landed Cost の Base/Tariff10/Tariff0 比較を、上記 per-channel 再スケール（tariff basis 一定＝厳密）で算出。blended 近似を廃止。US レーンのみスイープで変動、EU 8% は不変。US ケースは対米0%で Customs＝$0（欧州需要ゼロ）、EU ケースは対米0%でも EU 8%分 $51,693 が残る——「集中 vs 分散の関税耐性」を実データで可視化。実出力は `WOM_醤油デモ_収録準備メモ_v2_実出力`（B表）に反映済み。

### 既知の陥穽・確認事項
- **発生主義の粗利 ≠ 損益分岐週**：PPC は各ロット P&L（売上−全landedコスト）を販売週に発生主義で計上するため、累積粗利は W01 から正（GPベースの「黒字転換週」は常に W01）。デモ台本の「損益分岐週15/18週」は前倒し生産（`push_lead_time_weeks=7`）による在庫投資のキャッシュ回収の概念で、発生主義台帳には現れない。本モデルは CCC=−2.0週で前受け構造。→ 収録では「粗利率ギャップの週次アニメ」に振替（メモ v2 §C-2）。真の「累積キャッシュ回収週」を出すには PSI タイミング（生産週コスト vs 販売週売上）からの別集計が必要（次フェーズ候補）。
- **既存テストの陳腐化修正**：`tests/test_ppc_vertical_slice.py::test_landed_cost_components` は本作業前から red だった（2026-07-10 の `mom_to_dad_freight_base` 分離時に、テスト期待値〔4項〕が `landed_cost_total` イベント〔`ppc_tariff.py` a-3、mom_to_dad_freight を含む5項〕に未追随。差=CN→JP海上運賃507.456）。**エンジンは正しく、テスト側の期待値に `+ acc.mom_to_dad_freight_base` を追加**して解消（エンジン無変更）。この修正で pytest **81件全緑**。
- **未実施（当初案からの縮小）**：既存6ケースの一括再ラン・ヘッドレス検証スクリプト同梱は見送り（エンジン無変更＝サンプルデータ+GUIのみの変更で回帰リスク低と判断）。運転資本（Inv/CCC/AR/AP）の台帳導出、シナリオ別 PPC 再実行は今回スコープ外。

### commit（wom-v1r2m0）
- `82a1128`：soysauce-us/eu 21CSV 初版
- `d381511`：Phase1（gen_tariff_edges）+ Phase2増分1（P&L Summary 台帳ソース）+ sku_master 6region 修正
- `0ada4ca`：Phase2増分2（Landed Cost 台帳ソース、lot精度スイープ）
- `d470872`：テスト修正（stale landed_cost_components）
- `46a2c5d`：docs（Request Letter §10/§11・本CLAUDE.md v1r2m0 節）
- `8c27dd5`：soysauce-jpy-2027 FXケース + 通貨ラベル base_currency 追従（下記）
- `edaef36`：GP-by-scenario チャート台帳ソース化 + US/EU を CCC+2週・JP¥320 に統一（下記）

---

## v1r2m0：円建て為替ケース（soysauce-jpy-2027）＋ 円安×原油の綱引き（完了、2026-07-25/26）

デモを「USD関税（集中 vs 分散）＋ 円建てFX（円安×地政学）」の2部構成にするため、**JPY建ての為替シナリオケース**を新設。**エンジンコードは無変更、サンプルデータ＋GUI表示のみ**の変更。

### soysauce-jpy-2027（base=JPY・円安・原油リンク）
- `soysauce-eu-2027`（S2/全6市場）を copy して土台に。**base_currency は `ppc_fx_rate.csv` の base_currency 列から自動検出**される（`app.py` の `_run_ppc_from_planning`、5424行付近。FXConverter は `wom/ppc/ppc_fx.py`、`amount_local × rate = base`、rate＝base通貨/現地通貨1単位）。→ CSV を JPY にするだけでコード変更なしに円建てへ。
- **円安 時系列**：`ppc_fx_rate.csv` を JPY/USD/EUR 行に。**2027=USD 150円**、**2028=200円**（W01–W04 ランプ）、EUR=USD×1.08 連動（162→216）。`get_market_price`/`get_supplier_cost` は latest-prior-week 参照（`ppc_rules.py` 156/172行）なので、値が変わる週だけ行を置けば時系列ステップになる。
- **コストを原産で分離**（フル）：輸入原料（大豆・樹脂・包装材）＝`Materials_JP` USD 建て（円安で JPY 増）。国内加工（醸造/瓶詰 ¥750・国内倉庫/DC）＝JPY 建て（FX中立）。海外DC＝現地通貨（US=USD、EU=EUR）。node_cost は静的だが **event 週の FX で換算される**ので海外DCコストも円安で JPY 増になる。
- **原油スパイク（ホルムズ）**：`ppc_supplier_cost.csv` の Materials_JP USD 原価を $6→$8（2028-W10）→$6.5（2028-W26、床が上がる）。円安期に重なり複合ショック。
- **CCC +2週**：`sku_master.csv` を dso=8/dpo=6（買掛支払6週<売掛回収8週）。WOM の CCC = DIO+DSO−DPO ≒ DSO−DPO（DIO≈0）。
- **JP国内価格の較正**：¥320/本＝¥3,840/ケース（全国平均¥311・定価¥350の実勢中間、大杉さん指定）。価格単位＝1ケース＝1L瓶×12本。

### 実データの結果（demo-gold）
- **円安は輸出を潤す**：US/EU の lot GM が 2027 28-30% → 2028 **33-34%** に改善（売上はUSD/EUR建てで円換算増、国内加工費は円建て固定）。
- **同じ円安×原油が国内を沈める**：Rest_JP が 2027 **19.5%** → 円安のみ(2028-W09) **+7.7%** → **円安×原油の複合ピーク(2028-W10〜W25、16週) −2.7% 逆ザヤ** → ショック後 **+5.1%**（床が上がり戻らない）。
- **WOM が該当16週を lot 単位 trust event で自動検知**（NEGATIVE_MARGIN／LANDED_COST_EXCEEDS_MARKET、`ppc_lot_reconciliation.csv` の `trust_events_fired`）。逆ザヤ16週＝原油スパイク窓に完全一致。「円安"単独"なら耐えるが、複合ショックが臨界を超え価格改定を迫られる」＝2022-24の実話と同型。
- 為替2水準の見せ方は**別フォルダを増やさず時間軸で対比**（PPC コックピットの週フィルタで 2027=150円 vs 2028=200円）。

### GUI 変更（`app.py` / `landed_cost.py`、表示のみ）
- **GP-by-scenario チャートを台帳ソース化**：`_refresh_charts` の Revenue/GP/GM を `_ledger_pl_for_sku()` で上書き（P&L Summary と同じオーバーレイ）。従来 money 集計由来で 55% 等ズレていたのを台帳値（例 JPY 28.0%）に一致させた。
- **通貨ラベルを base_currency 追従に**：`_base_ccy()` を新設（`ppc_kpi_summary.json` の base_currency → 記号）。Landed Cost テーブル・GPチャート軸・`build_lc_narrative`（`landed_cost.py` に `currency_symbol` 引数追加、既定"$"で後方互換）が JPY なら `¥` を出す。**表示のみ、金額計算は無変更**。
- **US/EU ケースも統一**：`soysauce-us/eu-2027` を dso8/dpo6（CCC+2週）・JP ¥3,840（$25.6）に。→ B表 GM が US 25.9→**26.9%**、EU 26.2→**27.2%**（JP売上増で約+1pp）。実出力メモ v2・絵コンテ台本 v2 の数値も更新済み。

### 確認状況・次のClaude君へ
- 大杉さんが GUI 実機で全確認（jpy：GPチャート28.0%・Landed Cost¥表示・CCC+2・trust event 16週、us/eu：CCC+2・GM微増）。pytest **81件全緑**（`build_lc_narrative` の引数追加は後方互換）。
- GitHub の**デフォルトブランチを `wom-v1r2m0` に変更済み**（2026-07-26、大杉さん）。
- デモ制作用ドキュメント（`WOM_デモ動画_絵コンテ台本_v2_2部構成`、`WOM_醤油デモ_収録準備メモ_v2_実出力`／`_FX追補`）はリポジトリ対象外（大杉さんのローカル outputs）。
- **未対応・次回候補**：(a) 真の「累積キャッシュ回収週」を PSI タイミング（生産週コスト vs 販売週売上）から別集計（発生主義の粗利では W01 から正で出ない）、(b) 運転資本（Inv/CCC/AR/AP）の台帳導出、(c) `edge_cost_master` の `trade_lane_master` 改称（Phase1で後方互換のため見送り）、(d) 英語版 経験論文（JIMA 研究速報／arXiv）の submit。

### Landed Cost 台帳オーバーレイのバグ修正：DAD計上型の関税に対応（完了、2026-07-26）

「過去6ケースは v1r2m0 で正しく動くか？」の確認中、大杉さんが **apparel-us-2026** を GUI で回したところ、Management の **Landed Cost が Base で 49.0%（P&L Summary は 43.3%）** と食い違い、Customs Duty が **$0** と表示されることを発見。

**原因（`_ledger_lc_overrides`、Phase2 増分2＝commit `0ada4ca` で導入した取りこぼし）**：
- 醤油は関税を **leaf_out チャネル**（`Rest_US_*`）に計上するが、アパレルは関税を **輸入DADノード**（`DC_Local_US`、tariff 37,985）に計上する。`ppc_node_pl_summary.csv` 上、チャネル行の `tariff_base` は 0。
- 旧オーバーレイは「関税はチャネル行に乗る」前提で **チャネル行の tariff だけを集計** → `scen_tariff=0`（Customs $0）。さらに `landed_cogs = total_cost − total_tariff(DAD分含む) + scen_tariff(0)` としたため、**関税をコストから丸ごと消し去り、Landed GM が P&L(43.3%) + tariff/rev(5.7pp) = 49.0% に膨張**（landed が gross を上回るという非現実的結果）。

**修正（`wom/gui/app.py` `_ledger_lc_overrides`）**：チャネル行だけでなく **全ノードの `tariff_base` を、`ppc_node_profit_zone.csv` の node→country で国別に集計**（`node_tariff`）。国が引けない行は再スケール係数1（関税額は据え置き）で **ドロップせず必ず計上**。→ アパレル Base が **Landed GM 43.3%（P&L と一致）・Customs $37,985**、スイープも TariffShock 37.5%($75,971)／TariffRelief 40.4%($56,978) と正しく動く。醤油はチャネル計上・DAD tariff=0 なので **node集計＝旧chan集計で不変**（jpy 28.0%／us 26.9%／eu 27.2% のまま）。pytest **81件全緑**。

**設計上の限界（バグではない・将来 per-lane 化の余地）**：同一仕向国に**複数の輸入レーンが異なる関税率で存在**する場合（アパレルの CN→US と ES→US 等）、`country_rate` は `dst_region`(=国) キーの dict に集約されるため**同一国内で1レートに畳まれる**（後勝ち）。**Base の reconcile は厳密**（scen=totalで total_cost に戻るため）だが、Shock/Relief の額はその国の解決レートに依存する近似。厳密なレーン別スイープが要るなら `ppc_tariff_rule` のエッジ単位で再価格付けする拡張が必要（今回は Base 一致を優先し見送り）。

**結論（「6ケースは動くか」への回答）**：**Planning・PPC・World Map・Network・P&L Summary は全ケース従来どおり正しく動作**（エンジン無変更）。本件は **Landed Cost パネルの表示のみ**の取りこぼしで、修正済み。DAD計上型の関税を持つ他ケース（Cookie / oil / EV 等）も同様に是正される見込み（未実機確認）。

**bash の落とし穴（再確認）**：CLAUDE.md を Linux bash の `tail`/`wc` で読むと**切り捨てられた行数（今回 982 行）を返す**。実ファイル（約1070行）は必ず **Read tool（Windows側実ファイル）** で確認すること。

**commit**：`fix(Management): Landed Cost ledger overlay aggregates tariff over ALL nodes by country`（app.py 1ファイル）。

---

## v1r2m2：Anti-Degrade 網（golden ハーネス）＋ cap_soft 復活（Phase 1a/1b、完了、2026-07-30）

branch `wom-v1r2m2`。`requests/operating-constraint-layer-request-letter.md` の Phase 1a/1b を実装。**エンジンの挙動（psi/ppc）は不変**——cap_soft はすべて「フラグのみ・lot 不動（Fork A）」、既存12ケースの golden は placement 不変で緑。

### Phase 1a：ゴールデン・ハーネス（先に網を張る）
- `tools/run_headless_from_folder.py`（新規）：GUI の `_build_planning_context`/`_planning_thread`/`_run_ppc_from_planning` を tkinter 抜きで移植。`run(model_dir, plugins_spec, ...)` が Load→Planning→PPC を実行し KPI スナップショット dict を返す。忠実性は GUI 実値と一致確認済み（soysauce-us 26.9%／jpy 28.0%・trust32／apparel 42.4%／rice 38.5% 等）。「手動 probe が毎回消える」問題の恒久対策。
- `tests/test_golden.py`（新規）＋ `tests/golden/*.json`（12ケース）：「現行実行 == golden」を assert。スナップショット構造 = `period / products / config{plugins} / forward{cap_hard_sealed, cap_soft_violation_count} / backward{cap_soft_envelope_count} / ppc{GM/Rev/Cost/tariff/trust} / psi{node:{P,S,I_sum,I_max,CO,series_md5}}`。**エンジンを触る前に "before" を凍結**するのが目的。
- レガシー `iphone`（CNY FX 欠落で失敗）と `rice-…_BK…`（古いバックアップ）は golden 対象外。

### Phase 1b：cap_soft の復活（休眠の解消）
cap_soft が死んでいた真因は §11.1 の通り2つ——(i) Backward の demand envelope 未実装、(ii) **CSV→ローダ→ノードのデータ経路欠落**（capacity_plan に列が無く、ローダが cap_hard しか読まない）。Forward の Step 0b（cap_soft 違反フラグ）は生きていた。
- **データ経路スライス**：`wom/engine/capacity_sealer.py` に `load_capacity_dataframe(sc_tree, cap_df, weeks)` を新設（sku_id/week/max_supply/[node_name]/[cap_soft]、cap_soft は**列がある時だけ**セット＝opt-in で既存無変更）。app.py と headless の重複ローダを**この1本に集約**。`tests/test_capacity_soft.py`（Integration 3＋Unit 1）。
- **Slice 2（Backward envelope・禁足コア改変）**：`backward_planner.py` の `BackwardPlanResult` に `cap_soft_envelope_violations` ＋ `record_cap_soft_envelope`、`_apply_mom_cap_backward` の週ループ先頭に「`placed_p=min(demand,cap_hard)` が cap_soft 超過なら記録（lot 不動）」を**純加算**。配置ロジックは無変更。`tests/test_capacity_soft_backward.py`（Unit 4、うち1つは cap_soft 有無で psi 完全一致＝Fork A 保証）。
- **実ケース可視化**：`data/sample/soysauce-jpy-2027/capacity_plan.csv` の Bottling_Noda に `cap_soft=900`（通常2直）／`cap_hard=1500`（繁忙3直）。GUI の **Network タブ → PSI List サブタブ**（`PSIListPanel._draw_capacity_chart`「P vs Capacity Limits」）に CapSoft オレンジ点線＋残業週オレンジ棒が表示。数値：Forward `cap_soft_viol=81`（実行/PUSH スケジュールが900超）、Backward `bwd_env=88`（需要計画が900超）——別レイヤーの残業シグナルとして両方 golden に固定。

**テスト**：81 → **101 件全緑**（+golden12・+cap_soft Integration/Unit 4・+backward Unit 4）。GUI 実機確認済み。

### 禁足ルール成文化（§11.3）
本ファイル上部「## 禁足ルール（Planning Engine 保護対象コア）」と `AGENTS.md` §10 に、保護対象コア6ファイル＋「明示指示＋3層テスト緑＋オーナー差分レビューを条件に触る」ゲート式ルールを明記。

**未着手（次回候補）**：Phase 2（操業カレンダーの core 統合＝休日を plugin から traversal の「配置週スキップ」へ、SS_Days と統一）、Phase 3（撹乱層の分離整理）。

---

## v1r2m2：Phase 2 — 操業カレンダー intrinsic 化 ＋ per-node demand_envelope（hard/soft）＋ 休日 holiday-aware 平準化（完了、2026-07-31）

branch `wom-v1r2m2`。Request Letter「操業制約レイヤー」の Phase 2（操業カレンダーの core 統合）を、大杉さんとの設計対話で当初案を超える形に発展させて実装。**既定 hard で既存11ケースの golden は不変**（opt-in）。

### 確立した設計原則（最重要・次の Claude 君へ）

**「Backward の Demand Allocation が"親心"で全部やる。Forward Planning は `I(W)=I(W-1)+P−S` を前へ回すだけで、決して時間を遡及しない。」**

- 休日（閉鎖週）の作り溜め・前倒しは **Backward の `_apply_mom_cap_backward` が完結**させる（閉鎖週を cap=0 として cap を尊重しつつ手前へ carry-back）。
- Forward に「閉鎖週の生産を手前へ動かす」ような**遡及処理を入れてはならない**。実際、開発途中で Forward 側に `_apply_operating_calendar_shift`（閉鎖週の P を最寄り手前週へ pile）を入れたところ、**W32 の P が cap_hard すら超える 2143 のスパイク**を生み（soysauce-jpy お盆デモで発覚）、大杉さんの「Forward は遡及するな」の指摘で**撤回**した。これがこの原則を確立した経緯。

### Slice 2-1：per-node 操業カレンダー（0-shift → 配置週スキップ）
- `PlanNode.op_shifts`（per-week の shift 数 0〜21、既定 None=常時 open。`MAX_SHIFTS=21`＝3直×7日）。`set_operating_shifts`/`operating_shifts`/`is_open`。`init_psi` で `[None]*n` 確保。
- `wom/engine/capacity_sealer.load_operating_calendar(sc_tree, cal_df, weeks)`：`operating_calendar.csv`（sku_id/node_name/week/shifts）を読み node に展開。app.py＋headless で capacity ローダ直後に呼ぶ（opt-in、ファイル無ければ no-op）。
- `BackwardPlanner.__init__` で `_closed_by_name`（plugin 由来 `explicit_closures` ∪ 各 node の `op_shifts==0`）を構築し、`_offset_week` が両ソースの閉鎖週を LT オフセットでスキップ。SS_Days と同じ「配置週調整」に統一。
- テスト `tests/test_operating_calendar.py`（Unit/Integration 4）。

### Slice 2-2：shifts → cap_soft 導出
- `load_operating_calendar` 内で `sh>0` かつ `cap_hard>0` の週に `cap_soft = round(sh × cap_hard / MAX_SHIFTS)` を導出（capacity_plan の cap_soft を上書き）。21 shift＝cap_hard、0 shift＝閉鎖（skip、cap_soft は据え置き）。＝operating_calendar 1枚で「休み・通常・フル稼働」を表現。テスト `tests/test_shift_cap_soft.py`（3）。

### Fork B：per-node `demand_envelope`（hard / soft）— 平準化の二モード
大杉さんの問い「休み前だけ頑張る（cap_hard）か、平準化計画（cap_soft）で淡々か？」への結論＝**2 の cap_soft 平準化（heijunka）が実務の正解**。ただし生鮮・在庫不可・受注生産は前倒しできないので **hard も残す**。→ **lane 上の各 plan_node が工程特性で選ぶ**二モードに。
- `PlanNode.demand_envelope`（"hard"（既定）/"soft"）。`sc_tree_master.csv` に `demand_envelope` 列（`sc_tree_builder` が読む。列無し＝全 hard）。
- `_apply_mom_cap_backward` の**充填ターゲットを mode 化**：閉鎖週=0／soft かつ cap_soft>0=cap_soft（平準化・超過は前倒し carry-back）／それ以外=cap_hard（従来）。**以降の overflow→CO＋前週 carry-back ロジックは無変更**（cap_int を切り替えるだけ）。cap_hard は両モードで物理天井。
- **平準化には slack（cap_soft − 需要 > 0）が必須**：通常週に余裕が無いと前倒し先が無く past_due になる。soysauce demo は Bottling_Noda を 18 shift（cap_soft≈1286・需要~1000・slack≈286）にして、お盆 W33 の 1000 を手前へ均等に前倒し。
- テスト `tests/test_backward_holiday_carryback.py`（3）＋`tests/test_demand_envelope_soft.py`（3）。

### Forward の遡及処理を撤回
`forward_planner.py` の `_apply_operating_calendar_shift`（Slice 2-3 で一時導入）と `_process_node` の Step 0c 呼び出しを**削除**。休日対応は Backward に一本化（上記原則）。これに伴い pile 挙動を assert していた `tests/test_operating_calendar_skip.py` は**廃止（git rm）**。

### demo（soysauce-jpy）
- `sc_tree_master.csv` に `demand_envelope` 列（Bottling_Noda=soft、他 hard）。
- `capacity_plan.csv` は cap_soft 列を除去（cap_hard=1500 物理天井のみ）。`operating_calendar.csv`＝Bottling_Noda 通常18 shift＋お盆 2028-W33=0。
- 結果：**お盆 W33 が自然な穴、直前週が cap_soft に均等前倒し、W32 スパイク無し、CO/Shortage 無し、cap_soft_viol=0、GM=28.0% 不変**。DEMAND 層と SUPPLY 層が整合。

### テスト・確認
- 114件全緑（既存 ＋ 操業カレンダー4・shift_cap_soft3・holiday_carryback3・demand_envelope3、廃止 skip3）。既定 hard で既存11ケースの golden 不変。soysauce-jpy golden のみ soft demo で意図的に再生成。
- commit（wom-v1r2m2）：Slice 2-1 `c924338`／2-2 `1c71fbe`／demo hard版 `57d78b3`／…／Phase2 統合 engine `dbdd59b`＋demo `13cf44b`。

### 未対応・次回検討事項
- **CO カスケード表示**：Backward carry-back は overflow を各週 CO に記録しつつ前週へ押すため、DEMAND 層の CO 合計が net displacement より大きく見える（カスケード合計。財務・実行は無害、supply 側は欠品なし・GM 不変）。「純 displacement のみ」に整えるのは hard 共通機構の変更＝要 golden 再生成の将来課題。
- **供給側（PUSH/上流）の休日反映**：今回は Backward 一本化で soysauce（Bottling PUSH decoupling）は綺麗に解けた。より複雑な多段 PUSH で残差が出た場合の検討は将来。
- **禁足ルールへの追記**：「Forward は roll-forward のみ・遡及処理禁止」を上部「禁足ルール」節に明文化する価値あり（次回）。
- Phase 3（撹乱層＝ストライキ/災害の cap_hard clip・CO の分離整理）。

---

## v1r2m2：先行生産の per-node パラメータ `init_stock_days`（X2）― `LT_offset(D2S) = B + X1 + X2`（2026-08-02）

branch `wom-v1r2m2`。大杉さんとの設計対話で先行生産の計画モデルを確定し、per-node パラメータ `init_stock_days`（X2）を追加。**既定 0 のため既存11ケースの golden は placement 不変で緑**（opt-in）。

**設計の本体は docs/design 側にある**（本節は実装ログ）：
- `docs/design/holiday_calendar_push_lead_time_and_planning_horizon.md` 9.5 ＋ Decision 7 … 三成分の定義、Tree による役割排他、採用しなかった案の経緯
- `docs/design/planning_warmup_and_reporting_horizon.md` 13章 … 横軸（Planning Horizon）と per-node offset の二層関係

### 要点

```text
LT_offset(D2S) = B + X1 + X2
    B  = lt_wks           物理 LT（pipeline 在庫）
    X1 = ss_wks           安全在庫（需要変動吸収）
    X2 = init_stock_wks   立ち上げ期の初期在庫（人の意思入れ）
```

- **在庫を「量」ではなく「時間」で表現する**。P を S より `(B+X1+X2)` 週手前へずらせば、Forward の `I(W)=I(W-1)+P−S` が差分を I として自動生成する。opening_inv による lot 注入も、先行需要を建てることも不要。
- **Decision 7（Tree による役割排他）**：InBound のボトルネック解消は `push_lead_time_weeks`（Mode 4）、OutBound の需要変動吸収は `init_stock_wks`（X2）。**X2 は OutBound propagation にのみ加算し、InBound には加算しない**——二重前倒しを実装で防ぐ。
- **X2 は定数オフセットなので定常状態にも残る**（解釈A、意図的）。X1+X2 を「この node の在庫政策」と見なす。buffer node の目的が需要変動吸収である以上、目的と合致する。運転資本増は PPC/CCC に出るので、経営判断で絞れる。

### 影響ファイル

- `wom/model/plan_node.py`：`init_stock_days: int = 0` を `ss_days` の隣に追加、`init_stock_wks` プロパティ（`ceil(init_stock_days/7)`、`ss_wks` と同じ流儀）
- `wom/engine/backward_planner.py`：**OutBound propagation の1箇所のみ**変更（`node.lt_wks + node.ss_wks` → `+ node.init_stock_wks`）。InBound propagation は**コード不変**、Decision 7 の理由コメントのみ追加
- `wom/engine/forward_planner.py`：**変更なし**（Forward はそのまま素直に流すだけ。「Forward は時間を遡及しない」原則を維持）

### Master CSV スキーマ

`sc_tree_master.csv`（ノード属性 CSV、`ss_days` を定義しているファイル）に1列追加。

| 列名 | 型 | 意味 | 既定 |
|---|---|---|---|
| `init_stock_days` | int | X2：立ち上げ初期在庫のカバレッジ日数 | 0 |

単位を日数にしたのは `ss_days` と揃えるため。列を持たない既存ケースは挙動完全不変。

### 運用（手動調整を主機構とする）

1. `init_stock_days=0` で 1st run → PSI Graph で buffer node の CO を目視
2. CO の出方を見て `init_stock_days` を設定（在庫週数/日数の意思入れ）
3. re-plan → CO が消えることを PSI Graph で確認

**自動調整は「上書き可能な提案」として併存させる方針**。黙って解く黒箱にはしない（6-keys の owner/field/consultant の役割分担と整合）。適用対象 node を buffer に限定するガードは設けない——ボトルネックもバッファも環境変化で移動するため、設定は運用者の裁量に委ねる。

### 注意（次に触る Claude 君へ）

- **X2 と Planning Horizon は連動する**。`init_stock_days` を増やすと Backward の遡り量が増え、Planning Start が前倒しされていないと `parent_w < 0` で `record_past_due` に落ちるだけで在庫は立たない。横軸（Warm-up Period、`demand_forecast.csv` のゼロ需要行、`capacity_plan.csv` の延長）とセットで見ること。
- **横軸を延ばしても CO が残る場合**の切り分け：(a) X2 未設定、(b) 真の能力不足（X2 を積んでも消えない）。子文書 12章に追記済み。

### 未対応・次回検討

- 実データ検証（`init_stock_days=0` で CO 発生 → 値を入れて CO 消滅、を soysauce-jpy の OutBound DC で確認）
- 自動調整レイヤー：`forward_planner.py` の PUSH decoupling ブロックに `_push_unmatched[w]`（未充足 lot_id の記録専用・Fork A）を加え、1st run の CO から X2 を推定する harvest。`warm_up_mode = manual | auto` の per-node 切替
- X2 が定常に残す運転資本増を PPC/CCC でどう見せるか（「自動値＝CO ゼロの上限提示、人がそれ以下に絞る」UI 思想）
- 環境変化の narrative から bottleneck/buffer 配置を割り出す機能（次バージョン構想。`init_stock_days` の per-node 化で探索空間の一次元としての素地はできた）

---

# v1r2m3（branch `wom-v1r2m3`、baseline = `wom-v1r2m2`）

**このブランチの位置づけ**：`wom-v1r2m2`（Holiday休暇週の Backward 一本化、X2=init_stock_days、Anti-Degrade 網、設計文書まで完了）を**凍結ベースライン**とし、そこから切り直したブランチ。ローカルはフォルダも分け、`...\wom-v1r2m2`＝ブランチ v1r2m2（凍結）、`...\wom-v1r2m3`＝ブランチ v1r2m3（実装）と1対1で運用する。

## 計画期間パラメータ `warmup_lt` / `planning_start` の外出しと warm-up 行の materialize（Phase 1〜3、2026-08-04）

**設計文書（正典）**：`requests/planning-horizon-warmup-parameter-request-letter.md`。
関連：`docs/design/planning_warmup_and_reporting_horizon.md` §13、同 §9.5。

### 背景・確定事項
Buffer node の startup CO 解消には「販売開始より前から供給準備を計算する助走区間（Planning Warm-up Period、横軸の前倒し）」が必要（§9.5 の第1層＝必要条件）。しかし WOM には助走区間の明示パラメータが無く、計画開始週は `demand_forecast.csv` の最早週から自動検出されるだけだった。v1r2m2 では soysauce-jpy に手作業で 2026-W28〜W53 のゼロ需要行等を追記して CO=0 を得た（commit `eb8691e`）。本機能はこの手作業を**基本パラメータ `warmup_lt` として外出し**し、助走行生成を規約化された materialize 処理に置き換える。

**重要（v1r2m2 実測で確定）**：startup CO の解消は**横軸（`warmup_lt`）**で行う。必要量は最深レーンの累積 **B+X1**（soysauce で26週）。`init_stock_days`（X2）は横軸の代替では**ない**——横軸を延ばさず X2 だけ足すと past_due が増えて**悪化**する（§9.5 の注意の実証）。`warmup_lt` は運用者が与えるパラメータで、取るべき最小値が「累積 B+X1」。

### 確定した設計判断（D1〜D3）
- **D1**：demand は助走週=quantity 0（W1コピー禁止＝実需要の捏造で CO 再発を防ぐ）。capacity_plan / operating_calendar は各 node の「最初の非ゼロ需要週」の値を後方コピー。holiday/ppc 等は対象外。
- **D2**：`warmup_lt`（週数、既定0）が主。`planning_start`（週ラベル）は任意 override。`effective_start = planning_start指定: min(planning_start, real_start) / warmup_lt>0: real_start − warmup_lt / それ以外: real_start`。
- **D3**：CSV に materialize（in-memory 合成ではなく、load/save の一貫性優先）。生成物と原本の区別は「最初の非ゼロ需要週 real_start より前の週の行」。idempotent（strip→再生成）・byte-stable・write-if-needed。冪等性＝アルゴリズム、監査（durable）＝コミット済みCSV＋golden、サマリー出力＝実行時の観測性、と役割分担（案B-safe）。

### 実装
- **`wom/engine/warmup.py`（新設）**：`materialize_warmup(model_dir, warmup_lt=None, planning_start=None, write=True) -> summary dict`。`planning_config.csv` 読込→`effective_start`算出→strip(週<real_start)→助走行生成(demand=0／cap・opcalは最初の実週値を後方コピー、source=warmup)→byte-stable 書き出し／write-if-needed。**config も引数も無ければ完全 no-op**（既存11ケース保護）。`format_summary()` 同梱。ISO週ラベルは `date.fromisocalendar`（2026 は W53 まで、年跨ぎ注意）。
- **`tools/gen_warmup_rows.py`（新設）**：上記を叩く薄いCLI（`python -m tools.gen_warmup_rows --model-dir <dir> [--warmup-lt N] [--planning-start W] [--dry-run]`）。
- **planning 初期処理へのフック（案B-safe）**：`wom/gui/app.py` の `_auto_detect_planning_period` 先頭、`tools/run_headless_from_folder.py` の `run()` の period 検出直前で `materialize_warmup(model_dir)` を呼ぶ。1起動で materialize→planning が完結。config 無し＝no-op で全 golden 不変。
- **`planning_config.csv` スキーマ（新設・per-model）**：`key,value` の2列。`warmup_lt`（int, 既定0）／`planning_start`（週ラベル, 任意）。ファイルを持たない既存ケースは挙動完全不変。

### soysauce-jpy への適用（Phase 3）
`data/sample/soysauce-jpy-2027/planning_config.csv` に `warmup_lt=26` を置き、`gen_warmup_rows` で手作業助走行（`eb8691e`、末尾追記）を**正規形に置換**（助走行がソートされて先頭へ、source=warmup）。実データ（2027-W01以降）は verbatim 保持。正規形は手作業版と**意味的に同一**（並び順・source テキストのみ違い、計算結果に無影響）のため、**soysauce-jpy golden は再生成不要でそのまま緑**。以降の planning 実行は write-if-needed で clean な no-op。FG_WH_Noda の startup CO=0・GM=28.0% 不変を確認。

### テスト・確認
- `tests/test_warmup_materialize.py`（新設・8件）：Unit（ISO週/年跨ぎ/effective_start導出/first_nonzero_demand_week）＋Integration（demand=0・コピー・idempotent・byte-stable・write-if-needed・config無しno-op・warmup_lt=0でstrip）。
- 既存 golden 12ケース全緑（config 無し＝全 no-op で挙動不変）。soysauce-jpy も再生成せず緑。

### commit（wom-v1r2m3）
- `7b344b1`：Phase 1（`warmup.py`＋CLI＋8テスト）
- `4aeaadb`：Phase 2（planning 初期処理フック：GUI `_auto_detect_planning_period`＋headless `run()`）
- （Phase 3：`planning_config.csv`＋soysauce 3CSV 正規化＋本 CLAUDE.md v1r2m3 節）

### 未対応・次回検討（Phase 4）
- `warmup_lt` の自動算定（§13.4）：最深レーンの累積 `B+X1+X2` から `required_warmup_weeks(market)` を計算し、`planning_start = final_demand_start − max(...)`。自動値は override 可能な提案として扱う（黒箱にしない）。
- `planning_config.csv` の GUI 入力欄／「Rebuild warm-up」ボタン（現状は CSV 直接編集＋起動時 materialize）。

## 関連する v1r2m2 セッションの補足（本ブランチの前提）
本ブランチの土台となった v1r2m2 セッションで、X2（`init_stock_days`）について以下が確定・修正済み（`wom-v1r2m2` 上）：
- **`sc_tree_builder.py` の `init_stock_days` 配線を追加**（commit `23ae8ee`）。当初の X2 実装（`plan_node.py`＋`backward_planner.py`）は CSV 列を読む配線を欠き**休眠**していた（cap_soft 休眠と同型の「CSV→ローダ→ノード データ経路欠落」）。`tests/test_init_stock_days_wiring.py`（commit `4b2513f`、5件）で再発防止。
- soysauce-jpy の26週 warm-up 初版（手作業、commit `eb8691e`）。→ 本ブランチ Phase 3 で `planning_config.csv` 駆動に置換。

---

# v1r3m0（branch `wom-v1r3m0`、baseline = `wom-v1r2m3`）

## ask_global_allocation：生産配分地形（Phase 1 コアエンジン＋Phase 2 可視化、2026-08-06）

**設計文書（正典）**：`requests/global-allocation-request-letter.md`（Rev 3・§8 に実装記録）、`docs/design/ask_global_allocation_spec.md`（v0r3）。**参照実装**：`tools/proto_terrain2.py`（伝達式の解釈は文章より参照実装を優先）。

### 位置づけ
限られた醸造能力を **国内/米国/欧州の3市場にどう配分するか** を、配分比率単体 (x_JP,x_US,x_EU) の **231格子点を全数評価**して利益地形として描く **Management 層**モジュール。既存の soysauce-us/eu 2フォルダの手動比較を231点に自動拡張したもの。**Planning Engine・保護コアは一切不変**（`demand_forecast` の配分だけが変わる case1 方式の延長）。**LP 最適化はしない**（面を出す＝経営判断に要るのは最適点でなく地形。§1.1）。

### 検証ケース `data/sample/soysauce-jpy-2027-alloc/`（golden 対象外）
派生元 soysauce-jpy から **能力 1500→800/週**（充足率 82.8%）に絞った版。理由：能力1500だと全需要を満たす配分が多く**最適点が台地28点で不定**。800で配給が必要になり最適点が一意になる（醸造4週で短期増産不可＝業種として自然）。追加 CSV 3本（`ga_market_aggregation`/`ga_scenario_master`/`ga_fx_policy_master`）。原価ブロックは既存 CSV から**導出**（入力不要）。

### 実装（`wom/allocation/`＋`tools/`）
- `transmission.py`（Step 0-5・純関数）：`rates`/`CostBlock`/`unit_pnl`。単位マージン JP750/US1748/EU1832（FX150・$6）。
- `cost_block.py`（Step 0.5 導出器）：sc_tree 経路×`ppc_node/edge/supplier/tariff/market_price/transfer`＋`sku_master`＋`ga_market_aggregation` から市場別ブロックを導出（US は SF/NY 1:1平均で 15.65）。移転価格 = `sku_master.unit_cost 2400 ÷ base_fx 150 = $16` ×1.1 = **$17.6**。
- `grid.py`（Step 6-11）：`simplex_grid`(231点)・`evaluate_point`（Demand Anchored `min(x·Cap, D)`）・`demand_ceilings`（尾根）・`best_point`（台地）。**利益＝粗利**（§8.1 の逸脱記録参照。op_profit の wc/sga は未実装）。
- `analytics.py`：切替点（117/119円）・交互作用（`(s4−s1)−(s2−s1)−(s3−s1)`・5%超で層分解無効フラグ）・`robust_point`（台地ミニマックス）・`constraint_cost`（国内フロア）。
- `tools/run_allocation_map.py`：シナリオ駆動で §7 出力 CSV 7本（`ga_{cost_block_derived,profit_surface,fx_balance,plateau,switching_point,interaction,constraint_cost}.csv` → `output/allocation/`、gitignore）。s1-s7 定常、s8 時系列はスキップ、s6 は US 関税25%を上書き。
- `tools/plot_allocation_map.py`：**各シナリオの大判単独地形図**（直角三角形 X=x_US/Y=x_EU、利益等高線・尾根線・最適★・基準○・FXB=1.0線。`--each` で全シナリオ1枚ずつ＝訴求用の必須出力）＋層別断面（交互作用ハッチ・>5%警告）＋シナリオタイル（共通スケール）。

### 確定した回帰値（受入 #10-#16 / 付録A）
- 最大利益：s1=132.1M / s2(円安)=207.2M / s4(円安×原油)=176.7M(台地3・最適(0.00,0.45,0.55)) / s5(円高)=85.8M / s6(US関税25%)=121.0M / s7(金利)=132.1M(=s1 ＝金利は粗利に無影響)。
- 尾根 x_JP=0.362/x_US=0.423/x_EU=0.423（合計1.208＞1）。台地 800→1/1500→28。切替点 117/119円。
- FXB：基準配分 FCR0.588/FRR0.787/**FXB0.747**、FXB=1.0 は輸出42.4%。最大点(0.10,0.45,0.45)は FXB0.664＝**山頂は中立線の外側**。
- 交互作用：base −8.3M(−40.2%)/optimum −7.9M(−18%)/domestic-heavy −6.3M(符号反転)。**常に5%超＝規準が機能**。
- **テスト allocation 42件緑**。既存 golden 12件・Planning Engine 不変。

### 未対応（次 Rev）
`ga_fx_decision_gap`（§7.8・社内レート150 vs 実勢200の機会損失）／s8 時系列／`ga_sensitivity`・`ga_breakeven`／`vv.py`／Phase 3（`AllocationMapPanel` GUI 組込）／op_profit（wc/sga・Step9-10）の扱い確定。

### commit（wom-v1r3m0）
`c60f9aa`(transmission)→`fb5c67c`(cost_block)→`9931ef5`(grid)→`3bcf1cc`(analytics)→`388fe91`(CLI)→`a6bc99c`(plot)。設計文書は別途 `68801f5`（spec v0r3＋letter Rev3）、サンプル `48f3ba9`。

## Lot_ID トレーサビリティ可視化：三層設計（設計記録のみ・実装なし、2026-08-18）

**設計文書（正典）**：`docs/design/lot_id_traceability_and_coverage_views.md`

外部評価者の指摘（InBound/OutBound の網羅性 ＋ PSI の Lot_ID 連携の全体感）に対する三層構成（静的 lint / leaf_in × leaf_out マトリクス / スイムレーン・トレース）の設計記録。**コード変更なし・CSV スキーマ変更なし・禁足コア無変更。**

ただし §2「スキーマ意味論の固定」は**本日から拘束力を持つ**：

- `demand_forecast.csv` の `region` は**地理ではなく leaf_out ノード識別子**（ev-thailand の `Sales_TH_BKK` / `_i` は同一都市＝地理では区別できない）
- `(sku_id, region)` は leaf_out と **1:1 でなければならない**（重複＝`_build_leaf_index` が無言で上書き＝需要が消える）
- `edge_cost_master` / `route_master` の `region` は**地理**であり別物（`node_type` の二系統と同型の危険）
- lot_id の形式 `{sku_id}:{region}:{week}:{seq:05d}` は**変更しない**（D1）。パースは `LotIDGenerator.parse()` を使うこと

設計文書 §1 に、本検討中に発生し後に訂正された**三つの誤った前提**を記録している（lot_id に product がない／`split(":")[1]` はバグ／錨は InBound 側）。いずれも誤りで、コード確認により否定済み。実装前に必ず §1 を読むこと。

## apparel-us-2026：warm-up 未整備と GUI Planning Config の配線（既知事象、2026-08-18）

**症状**：S1〜S3 で CO が全期間に張り付く（ランプ残骸）。S4 以降は正常。

**原因と対処**：
- apparel-us-2026（旧定義）は `planning_config.csv` 未整備。
- **GUI 経路では `planning_config.csv` の `warmup_lt` が Start Week に反映されない**
  （読まれないか、配線欠落。headless 側は未検証）。CSV に 26 / 52 を書いても効かない。
- GUI の Planning Config に **Start Week=2025-W02 / #Weeks=126** を直接入力すれば
  全8シーズンが正常計画される（S1 の CO 消滅を確認）。
- 初期値の #Weeks=74 は需要期間（104週）より短く、2027年後半シーズンが
  計画期間外になっていた。126 で両方解消。
- 実効オフセットは sc_tree の `lt_wks` 合計（約20週）より大きい。
  `sku_master.csv` の `lead_time_wks`（12〜14週）が加算されるため、
  S1 には 40週以上の warm-up が必要。

**モデルフォルダ再読込で初期値に戻る可能性があるため、実行前に毎回確認すること。**

**三層設計との関係**：静的 lint は CSV しか読まないため、この種の GUI 配線欠落は
検出できない。lint の守備範囲外として記録する（`docs/design/lot_id_traceability_and_coverage_views.md` §3 第1層）。

## 三層生産配分：Management / Demand / Supply（設計記録のみ・実装なし、2026-08-20）

**設計文書（正典）**：`docs/design/three_layer_production_allocation.md`

Management（`ask_global_allocation`）/ Demand（`lane_assignment.csv`）/ Supply（`ForwardPlanner._actual_s`）の
三層に独立した配分機構が存在し、**互いに接続されていない**ことを特定した記録。**コード変更なし。**

**即座に有効な訂正：**

- 上記「WOM 指標 → 上位 KPI」表の **「Multi-MOM 配分比率」は不正確**。実装は**静的チャネル振分け**（1:1固定テーブル、`priority` 列は未実装、能力連動の動的再配分なし、重複行は無言で後勝ち）
- `lt_wks` は**エッジ属性**（親→自分への物流LT）であり、ノードの加工時間ではない
- **`parent_node` が空の MOM ノードの `lt_wks` は無視される**（ブリッジ区間は同一拠点の受け渡しで LT が定義されない）。値を置かないこと。apparel の `Factory_Import_CN` の `lt_wks=8` は実体としては物流LTで、正しい置き場所は DAD 側
- 静的 lint に4項目を追加（設計文書 §6）

## ForwardPlanner: 複数Tier-1部材（Multi-leaf_in BOM）× holiday_calendar 閉鎖の組み合わせで擬似COが発生（既知バグ、未修正、2026-08-20）

**症状**：MOMノードに複数の`leaf_in`が並行して部材供給する構成（例: `ev-europe-2026`のBattery_DE/Motor_DE/ECU_DE、`ev-thailand-2026_update`のPlatform_Unit_Assy/Motor_Unit_Assy）で、そのMOM自身が`holiday_calendar.csv`の`supply_closure`を受けると、**閉鎖週の直後から実在しないCOがMOM自身のPSIに固定量で発生し、次の閉鎖イベントごとに段階的に積み上がって残り続ける**。`ev-thailand-2026_update`のFactory_Local_THで確認：2026-W32閉鎖後にCO=150固定、2027-W32閉鎖後にCO=300へ倍増、以降ずっと固定。ヘッドレス（`tools/run_headless_from_folder.py`）・GUI実機の両方で再現確認済み。

**発生条件（両方満たす場合のみ）**：
1. MOMノードに`leaf_in`が2つ以上ある（複数Tier-1部材構成）
2. そのMOMノードに`holiday_calendar.csv`の`supply_closure`イベントが**有効に**かかる（`HolidayCalendarPlugin`がGUIでON、かつ`cap_hard`が上記「cap_hard<=0.0曖昧さバグ」を回避した正の値）

**発見の経緯**：`ev-thailand-2026_update`にPlatform_Unit_Assy/Motor_Unit_Assyの2 leaf_in構成を追加した際に発覚。同型の構成は`ev-europe-2026`（Battery/Motor/ECUの3 leaf_in）に既存だが、そちらの`holiday_calendar.csv`はvalue列が`0.0`のまま（cap_hard<=0.0曖昧さバグで既に無効化済み）だったため、条件2を一度も満たしたことがなく、潜在バグとして見過ごされていた——「片方のバグを直したら、隠れていたもう片方が露出した」ケース。

**原因（仮説、未確定・要engine調査）**：`forward_planner.py`の`_propagate_to_parent`は、各`leaf_in`子ノードが独立に親（MOM）の`psi4supply[w][P]`へ`extend()`する実装で、BOM的な「全部材が揃って初めて1台分」という組立セマンティクスを持たない。子が2つあると同じ週のPが単純加算され（`ev-thailand-2026_update`ではFactory_Local_THのPが毎週ほぼ正確にSの2倍）、通常時は`_match_by_identity`の重複ロット吸収ロジックによりI=0のまま辻褄が合って見える。しかしStep 0a（CapHardシーリング）が閉鎖週に生リスト（重複あり）をスライスして超過分をCO[w+1]へ積む処理と絡むと、以降の週での再マッチングが正しく解消されず、固定・階段状のCOとして残留する。

**影響範囲（確認済み）**：影響はMOMノード自身のPSI表示（Networkタブ・PSI List・Debugパネル）に限定。下流（DAD・leaf_out・PPCの金額）は完全にクリーン（`ppc_node_pl_summary.csv`・各Sales_*チャネルのCO=0・粗利率とも無影響を確認）。

**対応状況**：**未修正**。`forward_planner.py`は禁足コア対象ファイルのため、修正するには本ファイル冒頭「禁足ルール」の通りRequest Letter起票＋3層テスト緑＋オーナー差分レビューが必要（今回のセッションはスコープ外と判断し見送り）。`data/sample/ev-thailand-2026_update/`はこの既知の制約込みで現状のまま採用（Platform/Motor部材別コスト可視化という本来の目的自体は正しく機能しており、影響範囲がFactory_Local_TH自身のPSIパネルのみに限定されるため）。

**次回セッションでの検討候補**：`ev-europe-2026`のholiday_calendar.csv（value=0.0のまま）を今後修正する場合、同じ症状がFactory_Local_DE/Factory_Import_HUでも再現するはずなので、そこでの追加確認・修正着手時の参考ケースとして使える。

**【2026-08-21追記】発生条件はholiday_calendar閉鎖に限らない、もっと広い不具合と判明**：

`data/sample/india-ghee-2026`（新規、インド酪農ギー：国内 vs UAE輸出モデル）の構築中に、**上記条件2をholiday_calendar閉鎖なしでも満たしてしまう**ケースを発見した。Ghee_Domestic側（Gujarat集乳協同組合を2 leaf_in "Anand_Milk_Route"/"Kheda_Milk_Route"で表現、Ghee_Plant_Anandへ合流）に、holiday_calendar.csvの`demand_multiplier`（ディワリ需要スパイク、leaf_outにのみ作用しCapHardシーリングに一切触れない"安全"な仕組みのはず）を適用したところ、**閉鎖イベントが存在しないにもかかわらず同じ症状が再現した**（Ghee_Plant_AnandでCO=20,386固定、ディワリ前倒し生産週から発生し以降ずっと解消しない）。

一方、同じ2 leaf_in構成でもディワリのような需要の不連続点を持たないGhee_Export側（Anand_Export_Route/Mehsana_Milk_Route → Ghee_Plant_Export、需要は滑らかに推移するのみ）は**完全にクリーン**（CO=0、cap_hard_sealed=0）だった。

これにより、**真の発生条件は「holiday_calendar閉鎖」ではなく、より一般に「MOMノードの週次demand.Sに何らかの不連続（段差）が生じること」**だと判明した。demand_multiplierでもBackwardのLTオフセット経由でMOM自身のdemand.Sに段差が生じれば同じ経路（`_propagate_to_parent`の重複extend＋Step 0aのCapHardシーリング）を踏んでしまう。closure（cap_hard起点の段差）はこの一般条件の一例に過ぎなかった。

**india-ghee-2026での回避策**：Ghee_Domestic側は2 leaf_inを`Gujarat_Milk_Collective`という単一leaf_inに統合（需要スパイクがあるチェーンは単一leaf_inで安全運用）。Ghee_Export側は2 leaf_in構成のまま維持（需要が滑らかなので安全）。結果、GM=20.0%・cap_hard_sealed=0・trust_events=0・CO=0（全ノード）を確認。

**運用上の指針（次に多leaf_in構成を作るClaude君へ）**：MOMに2つ以上のleaf_inを持たせる場合、そのMOM側のチェーンには**閉鎖イベントだけでなく、需要側の段差（祭日デマンドスパイク、季節切替の急激な変化点等）も一切持ち込まないこと**。段差が必要なストーリーなら、その多leaf_inを持つMOMではなく、需要が滑らかな別チェーン側に多leaf_in構成を寄せるか、単一leaf_inに単純化すること。

## 設計メモ：合流と組立、そして自動チューニング（構想のみ・実装なし、2026-08-28）

**設計文書（正典）**：`docs/design/design_memo_confluence_assembly_autotuning.md`

三つの構想を記録したもの。**仕様確定なし・コード変更なし。**

- **A**：酪農の生乳生産者は `leaf_in` ではなく `MOM` として定義すべきではないか
- **B**：InBound の組立工程（全部材の Lot_ID が揃った時点で組み立て、Lot_ID を一つにして I に残す）
- **C**：sweep loop → auto-debug → auto-tuning の三段階

**整理された論点：**

- **合流型と組立型は別物**。酪農は「同じものが集まる（揃う必要がない）」、製造は「異なるものが揃って一つになる（揃わないと作れない）」。現在の `_propagate_to_parent` は**合流としては正しく、組立としては誤り**。A1 の原因仮説（重複 extend で P が過大）は、合流型なら成立しない
- **auto-debug は静的 lint とは別の層**。lint は実行前（CSV のみ）、auto-debug は実行後（結果の判定）。判定ルールは 2026-08-27 のスイープで見つかった症状から導出できる
- **auto-tuning は「モデルが動くための設定」（`warmup_lt` 等、正しい値が一意）と「経営が決めるべき選択」（配分比率、decoupling 位置等、地形を渡すべき）を分ければ、「最適化しない」という WOM の思想と矛盾しない**

**依存関係**：A1 の原因確定が A・B のボトルネック。**C は A1 と独立に進められる**（むしろ C② があれば A1 の検証が機械化できる）。

## Global Oil Model：三段階のモデル分割（設計判断・実装なし、2026-09-01）

**設計文書（正典）**：`docs/design/global_oil_model_three_steps.md`

`oil-global-2027` の単位定義を検証する過程で「WOM に単位変換・コプロダクト・歩留まりの
機能が必要か」という問いが生じたが、**不要**と判断した記録。**コード変更なし。**

**判断：モデルを三段階に分けることで回避する。**

| 段階 | 対象 | 1ロットの単位 | WOM |
|---|---|---|---|
| step 1 | 原油のサプライチェーン | タンカー1隻分の平均輸送量 | **扱う**（現行 WOM で可能） |
| step 2 | 原油 → 石油製品の生成計画（topper 通油スケジュール等） | 変換点 | **扱わない**（外側の別モデル） |
| step 3 | ガソリンのサプライチェーン | KL 等の製品単位 | **扱う**（現行 WOM で可能） |

step 1 と step 3 は**それぞれの中で単位が一貫**しており、
「`cpu_size` は計画上のすべてのノードが同一の値を持つ」という原則を両方で保てる。

**実装対象外（「未実装」ではない）：**

- **`cpu_size` の単位変換** — モデル分割により、ノード間で単位が変わる状況が発生しない
- **コプロダクト**（1投入 → 複数産出）— step 2 の問題。WOM の木構造は逆向きの分岐を持たない
- **歩留まり**（小数配分）— step 2 の問題。整数ロット単位の WOM とは粒度が違う

**将来課題**：step 2 の石油精製計画モデルを、WOM とは別の計画モデルとして新規定義する。
接続は step 2 の出力を step 3 の `demand_forecast.csv` / `capacity_plan.csv` として
渡す形で足りる（`ask_global_allocation` の case1 方式と同構造）。

**`oil-global-2027` は現状維持**（単価に桁を吸収させる方式は動作しており golden も緑）。
ただし Hormuz / RedSea の `uom` が実態（10万バレル相当）と食い違い `KL` のままである点は
lint 候補として別途整理する。

## InBound Safety Stock：設定ルールと粒度制約（実測記録、2026-09-01）

**設計文書（正典）**：`docs/design/inbound_safety_stock.md`

InBound Tree で在庫が正しく立つことを、初めて実測で確認した記録。**コード変更なし。**

**確定したルール：**

- **`ss_days` は「供給元の leaf_in」に設定する。** MOM 自身（InBound root）に設定しても**完全な no-op**
  （`B + X1 + X2` は `[OutBound Tree only]`、親→子オフセットは**子自身の** `ss_wks` を使うため）
- **在庫は供給先（MOM）の I バケットに現れる。** 設定した子ノード自身には立たない
- `ss_wks = ceil(ss_days / 7)`。**1週未満の滞留は表現できない**

**実測（`bom-test-2026` の `Tire_Supply` / `Battery_Supply` に設定）：**

| ss_days | Vehicle_Assy の I_max | 生産の前倒し |
|---|---|---|
| 0 | 0 | — |
| 3 | **10** | 1週 |
| 7 | **10**（ss3 と `series_md5` 完全一致） | 1週 |
| 14 | **20** | 2週 |

**粒度の制約**：実務では工程間に2〜3日の仕掛滞留があるが、
WOM では 0.3〜0.4週にあたるこれを表現できない。`ss_days=3` は**1週分（実態の2〜3倍）**として計上される。
**過大な在庫を表示する方が、ゼロと表示するより有害**であるため、これは仕様として受け入れる。

したがって **WOM の I=0 は、在庫が物理的に存在しないことを意味しない**。
これは V&V の粒度制約に属する（Weekly Granularity Thesis の境界）。

**未検証**：在庫が積み上がっても PPC 金額が無変化である点（CCC / 棚卸資産回転日数への反映）。

## Kitting List と Stock Yard：InBound 組立工程（設計のみ・実装なし、2026-09-01）

**設計文書（正典）**：`docs/design/kitting_list_assembly.md`
**段階1の Request Letter**：`requests/request_kitting_stage1.md`

**実証された問題**：`supply_role=assembly` は名前に反して合流と同じ挙動であり、
**部材が欠品しても完成品が作られたことになる。**

`bom-test-2026` で `Battery_Supply` の能力を絞った実測（`tools/sweep_specs/bom_test_shortage.json`）：

| | base | battery_zero（cap 1） |
|---|---|---|
| Battery_Supply CO_sum | 0 | **2,160** |
| Vehicle_Assy S_sum | 100 | **100**（変わらない） |
| Vehicle_Assy CO_sum | 0 | **0**（立たない） |
| 下流の `series_md5` | — | **完全一致** |
| PPC Revenue | $3.2M | **$3.2M**（1ドルも変わらない） |

原因は `_match_by_identity` の `supply_set = set(supply_lots)`。
Tire と Battery の Lot_ID が同一のため重複が吸収され、
**Tire が単独で全ロットを届けていれば「揃っている」と判定される。**

**影響範囲**：`ev-europe-2026`（Factory_Import_HU / Factory_Local_DE、各3部材）、
`ev-thailand-2026_update`、`bom-test-2026`。
**実運用の golden 2ケースが同じ隠蔽を起こしうる。**

**方針**：Lot_ID とその集合演算には触らない。

- `plan_node.kitting[assembly_week][lot_id] = {child_node_name: arrival_week}` を横に持つ
- 止めた部材は **Stock Yard ノード**の I に滞留させる（質量保存とロット総数の整合性が同時に保たれる）
- Yard の運用上の余裕は既存の `ss_days` 機構で表現できる（エンジン変更不要）

**段階**：

| | 内容 | golden |
|---|---|---|
| 段階1 | 記録のみ。判定するが P には通す | **無変化** |
| 段階2 | 可視化・auto-debug | 無変化 |
| 段階3a | Stock Yard 導入 + gate keeping 有効化 | **変わる** |

**カンバン方式は WOM の対象外**（§6）。払出が上流への納入指示になる＝**遡及**であり、
「Forward Planning は決して時間を遡及しない」という確立した設計原則に抵触する。
またカンバンが働くのは週内の日・時・分の単位であり、WOM の粒度の外。
**現場で選択的に運用する実行系の問題として、WOM の計画系の外に置く。**

石油精製（`global_oil_model_three_steps.md`）に続き、
**「WOM に何を入れないか」という判断が二度続けて設計を単純にしている。**

---

# v1r4m0（branch `wom-v1r4m0`、baseline = `wom-v1r3m0`）

## Merit Order 分析 Phase 1（エラーハンドリング強化）＋ Phase 2（Regime Map / Pareto Front）（完了、2026-09-06）

**設計文書（正典）**：`requests/Phase1_RequestLetter_to_CodeKun.md`、`requests/Phase2_DesignMD_RegimeMap.md`。

**位置づけ**：`wom/visualization/` 配下に新設した独立モジュール群。**Planning Engine・禁足コアには一切触れていない**（サプライヤー調達分析のための補助ツール、`ask_global_allocation` と同じく Management 層の分析機能）。

### Phase 1：`wom/visualization/merit_order.py`（`MeritOrderAnalyzer`）のエラーハンドリング強化
- `load_suppliers_from_csv()`：ファイル存在確認（`FileNotFoundError`）→ CSV解析エラー（`pandas.errors.ParserError` を `ValueError` にラップ）→ 必須カラム検証 → 空ファイル検証、の順で追加。
- `export_to_json()`：戻り値を `bool` に変更（成功 True / 失敗 False）。`IOError`/`TypeError`/その他例外を内部でハンドルし、例外を外に投げない設計に変更。
- `validate_suppliers()`：`exchange_rate` の `<=0` / NaN / 非数値を検証するロジックを追加（`math.isnan` 使用）。
- テスト12件追加（既存16件と合わせて `tests/test_merit_order.py` 計28件）、全PASS。

### Phase 2-A：`wom/visualization/regime_map.py`（`RegimeMapAnalyzer`）
Merit Order の週次結果（`fulfillment_rate`, `average_lead_time`）から、需要レベル（Low/Medium/High）×供給タイト度（Tight/Balanced/Surplus）の3×3マトリクスに分類し、9通りの組み合わせそれぞれに推奨戦略（`cost_lock`/`dual_source`/`safety_stock`/`lean`/`merit_order`/`lead_time_hedge`/`consolidation`/`price_negotiation`/`quality_upgrade`）をマッピングする。`classify_horizon()` で複数週のホライズン分析（`risk_weeks`＝Tight週、`opportunity_weeks`＝Surplus週、遷移確率行列）も提供。`regime_score`（demand_pressure/supply_risk、0-10スケール）の算出式は設計文書に未規定だったため今回定義（詳細は `docs/development/wom-v1r4m0_phase2_regime_pareto.md` §2.2）——大杉さんレビュー待ち。

### Phase 2-B：`wom/visualization/pareto_front.py`（`ParetoFrontAnalyzer`）
Merit Order の `recommended_allocation`（サプライヤー配分レコードのフラットなリスト）から、Cost（総額）・Quality・Lead Time の3目的について Pareto 支配関係を O(n²) 素朴実装で判定し、Pareto Front（非支配解の集合、cost昇順）とランク間トレードオフ比率を算出する。

**既知の注意点（次回セッションで検討）**：`cost` を「配分量×単価の総額」で定義しているため、配分量が小さいレコードほど総コストが小さくなり Pareto Front 上で無条件に有利になる。複数週・複数サプライヤーの配分レコードをフラットに集約すると「小ロット・高品質・短納期」の1レコードのみが他の全レコードを支配し、Pareto Front が1点に収束する現象を実データで確認済み（設計文書 3.3.2 の使用例通りの操作で発生、バグではなく定義上の帰結）。詳細・補正案は `docs/development/wom-v1r4m0_phase2_regime_pareto.md` §3.3 を参照。

### テスト
`tests/test_regime_map.py`（12件）・`tests/test_pareto_front.py`（8件）を新規追加。リポジトリ全体 325件全PASS（既存305件に回帰なし）。

### 未対応・次回検討事項
- Pareto Front の cost 定義（総額 vs 単価、フラットな個別レコード vs 配分セット全体）の扱い方針の確定
- Regime Map の `regime_score` 算出式の大杉さんレビュー
- Merit Order → Regime Map → Pareto Front を GUI（Management タブ等）へ統合するかどうかの検討

## v1r4m0: Phase 2 - Regime Map & Pareto Front Analysis（完了、2026-09-06）

**概要**: Merit Order 結果から、市場環境分類と多目標最適化を実施。

### 実装内容

#### Phase 2-A: RegimeMapAnalyzer
- 需要レベル（Low/Medium/High） × 供給タイト度（Tight/Balanced/Surplus）の 3×3 マトリクス分類
- 9 つの推奨戦略（cost_lock, dual_source, safety_stock, lean, merit_order, lead_time_hedge, consolidation, price_negotiation, quality_upgrade）
- 12 週ホライズン分析と risk_weeks/opportunity_weeks の自動抽出
- **ファイル**: `wom/visualization/regime_map.py`

#### Phase 2-B: ParetoFrontAnalyzer
- 複数目標最適化（Cost, Quality, Lead Time）
- 非支配解の列挙アルゴリズム（O(n²) 素朴実装）
- トレードオフ比率の定量化
- **ファイル**: `wom/visualization/pareto_front.py`

### テスト結果
- `test_regime_map.py`: 12 PASS
- `test_pareto_front.py`: 8 PASS
- `test_merit_order.py`: 28 PASS（Phase 1、回帰なし）
- **リポジトリ全体**: 325 PASS（既存 305 + 新規 20）

### 既知の問題（Phase 3 で評価予定）

**Pareto Front の cost 定義**:
現在「配分量 × 単価」（総コスト）で比較しているため、小ロット案が無条件に有利になる。

補正候補:
1. Cost を「単価そのもの」に変更（規模非依存）
2. 「必要数量を満たす配分セット全体」を 1 解として比較（粒度変更）

詳細は `docs/development/wom-v1r4m0_phase2_regime_pareto.md` §3.3 を参照。

### ドキュメント
- `docs/development/merit_order_code_review_ja.md` - Phase 1 コード品質分析
- `docs/development/wom-v1r4m0_phase2_regime_pareto.md` - Phase 2 実装ガイド
- `requests/Phase2_DesignMD_RegimeMap.md` - 設計仕様書

---