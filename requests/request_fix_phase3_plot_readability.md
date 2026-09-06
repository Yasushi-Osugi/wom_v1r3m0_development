# Phase 3 修正依頼 — 図の判読性（需要線・凡例重なり・単複表記）

**宛先**: Code君
**作成日**: 2026年9月6日
**担当**: Claude君（指摘・仕様）→ 大杉さん（承認済）→ Code君（実装）
**優先度**: LOW（1-2時間）
**ブランチ**: `wom-v1r4m0`
**対象**: `tools/plot_merit_order_suite.py`
**関連**: `requests/Phase3_DesignMD_Visualization.md`（§3.2 / §4.1 / §6.1 を rev.4 で改訂）

---

## 概要

Phase 3 の実装（338件全PASS）を受領し、`--demo` 出力6枚を目視 QA した結果、**判読性に関する3点**の修正を依頼する。いずれもロジックの誤りではなく、図の読み取りやすさの問題。

**F1 は私（Claude君）の設計書の記述漏れが原因**であり、実装ミスではない。設計書側も同時に改訂する。

| # | 対象関数 | 内容 | 種別 |
|---|---|---|---|
| **F1** | `plot_merit_order_shift()` | 需要線（`required_qty` の垂直線）がない | 設計書の記述漏れ |
| **F2** | `plot_merit_order_shift()` / `plot_regime_timeline()` | 凡例がデータに重なる | 体裁 |
| **F3** | `plot_regime_matrix()` | `(1 weeks)` の単複表記 | 体裁 |

**実装工数**: 約 1-2 時間（テスト含む）
**リスク**: 極めて低い（描画のみ。ロジック・API・既存テストに影響しない）

**Phase 3 の絶対制約（Request Letter §C1-C7）は引き続き全て適用される。** 特に C1（matplotlib のみ）・C4（図中テキストは全て英語）・C5（出力パスを返す）・C7（乱数不使用）。

---

## F1: `plot_merit_order_shift()` に需要線を追加する

### 現状

λ の水平線は正しく引かれている（`plot_merit_order_shift()` 内）。

```python
if lam_before is not None:
    ax.axhline(lam_before, color="#888888", linestyle=":", linewidth=1.0)
    legend_extra.append(f"lambda_before = {lam_before:.2f}")
if lam_after is not None:
    ax.axhline(lam_after, color="#C1432B", linestyle=":", linewidth=1.0)
    legend_extra.append(f"lambda_after = {lam_after:.2f}")
```

しかし `axvline` の呼び出しが無く、**λ を決めている `required_qty` の垂直線が描かれていない**。

### なぜ問題か

この図の主旨は「為替・関税の変化でサプライヤーの順位が入れ替わり、その結果 λ が 50 → 55 に動く」ことを示すことである。

λ は **需要線と階段曲線の交点の高さ**として定義される。需要線が無いと、読み手には「2本の階段」と「2本の水平線」が別々に置かれているだけに見え、**なぜ λ がその値になるのかが図から読み取れない**。単独図（`plot_merit_order_curve()`）には需要線があるので、そちらとの一貫性も欠く。

**これは設計書 §3.2 に「各曲線の λ を水平線で示し」としか書かなかった私の記述漏れである。** 実装は設計書どおりであり、Code君の判断ミスではない。

### 実装仕様

#### F1-1. 需要線を引く

`legend_extra` を組み立てる箇所の**直後**、`ax.get_legend_handles_labels()` を呼ぶ**前**に挿入すること（凡例に載せるため）。

```python
req_before = before.get("required_qty", 0) or 0
req_after = after.get("required_qty", 0) or 0

if req_before and req_after and abs(req_before - req_after) < 1e-9:
    # 通常ケース：同じ需要量で before/after を比較している
    ax.axvline(req_before, color="black", linestyle="--", linewidth=1.4,
               label=f"required = {req_before:,.0f} units")
else:
    # 需要量が異なる場合はそれぞれの色で引く
    if req_before:
        ax.axvline(req_before, color="#888888", linestyle="--", linewidth=1.2,
                   label=f"required ({labels[0]}) = {req_before:,.0f}")
    if req_after:
        ax.axvline(req_after, color="#C1432B", linestyle="--", linewidth=1.2,
                   label=f"required ({labels[1]}) = {req_after:,.0f}")
```

**単独図と同じ黒破線**にするのは、2枚を並べて見たときに「同じ意味の線」だと分かるようにするため。

#### F1-2. 交点にマーカーを打つ

需要線と各階段の交点 `(required_qty, λ)` に丸マーカーを置く。これが「λ は交点の高さである」ことを視覚的に成立させる。

```python
if lam_before is not None and req_before:
    ax.plot([req_before], [lam_before], marker="o", markersize=6,
            color="#888888", zorder=5)
if lam_after is not None and req_after:
    ax.plot([req_after], [lam_after], marker="o", markersize=6,
            color="#C1432B", zorder=5)
```

マーカーは凡例に載せないこと（`label` を付けない）。凡例が混み合うため。

#### F1-3. λ が定義できない場合

`_lambda_of()` が `None` を返す（総供給量が `required_qty` に届かない）場合、**水平線もマーカーも描かない**。これは現状の実装どおりで変更不要。需要線（`axvline`）自体は `required_qty` があれば引く。

---

## F2: 凡例がデータに重なる（2箇所）

### F2-1. `plot_merit_order_shift()`

**現状**: `ax.legend(handles, labels_, fontsize=8, loc="upper right")`

After 曲線の右端（最も高価なサプライヤーの区間、y ≈ 90）が凡例ボックスの下に隠れる。

**修正**: `loc="upper left"` に変更する。

```python
ax.legend(handles, labels_, fontsize=8, loc="upper left")
```

階段曲線は左端が最安（y が最も低い）ため、**左上は構造的に必ず空く**。この図に限っては安定した配置である。

`legend_extra` に `lambda_before` / `lambda_after` が入るぶん凡例が縦に伸びるので、`framealpha=0.9` を付けて背後の線が透けないようにすること。

### F2-2. `plot_regime_timeline()`

**現状**: `ax_top.legend(fontsize=8, loc="upper right")`

`supply_risk` が 9〜10 に達する週（demo では H10 / H12）のマーカーが凡例の下に隠れる。

**制約**: Y軸レンジは `[0, 10]` 固定（設計書 §4.2）。**上部に余白を作って逃がすことはできない。** 週ごとに軸が伸縮すると週間比較ができなくなるため、この固定は維持する。

また、`demand_pressure` は下端付近（0 前後）を走るため `lower right` も使えず、`center left` は `supply_risk` の走行域（5〜8）と干渉する。**軸の内側に安全な場所がない。**

**修正**: 凡例を軸の**外側・上**に、横並びで出す。

```python
ax_top.legend(fontsize=8, loc="lower left", bbox_to_anchor=(0.0, 1.01),
              ncol=2, frameon=False)
```

これにより Y軸 `[0, 10]` 固定を保ったまま、描画領域を一切消費しない。

**注意**: 凡例が軸の上に出るぶん、タイトルと重なる可能性がある。`ax_top.set_title(...)` を使っている場合は `fig.suptitle()` へ移すか、`pad` を調整すること。下段の戦略帯の凡例（`ax_bottom.legend(...)`）は既に軸外にあるので**変更不要**。

---

## F3: `(1 weeks)` → `(1 week)`

### 現状

`plot_regime_matrix()` 内：

```python
label = f"{strategy}\n({n} weeks)"
```

`n == 1` のとき `(1 weeks)` と表示される。

### 実装仕様

単複を判定するヘルパーをモジュールレベルに切り出し、テスト対象とする。

```python
def _weeks_label(n: int) -> str:
    """週数を単複を考慮した英語表記にする（例: 0 weeks / 1 week / 9 weeks）"""
    return f"{n} week" if n == 1 else f"{n} weeks"
```

呼び出し側：

```python
label = f"{strategy}\n({_weeks_label(n)})"
```

**`n == 0` は `0 weeks`**（英語の慣用に従い複数形）。

---

## テスト仕様

### 追加するテスト（1件）

`tests/test_merit_order_plot.py` に追記する。

```python
def test_weeks_label_singular_plural():
    """F3: 週数の単複表記"""
    from tools.plot_merit_order_suite import _weeks_label
    assert _weeks_label(0) == "0 weeks"
    assert _weeks_label(1) == "1 week"
    assert _weeks_label(2) == "2 weeks"
    assert _weeks_label(12) == "12 weeks"
```

### F1 / F2 のテストについて

**新規テストは追加しない。**

- **F1**：既存の `test_plot_merit_order_shift` が「例外なく画像が生成されること」を担保する。需要線が実際に引かれているかは**目視 QA** で確認する。描画関数は Figure ではなく出力パスを返す契約（Request Letter §C5）のため、Axes の内部を検査するテストは書けない。
- **F2**：純粋な体裁であり、自動判定になじまない。目視 QA で確認する。

これは `tests/test_allocation_plot.py` の既存方針（「中身の見た目は人手 QA。ここは "コード経路が壊れていない" ことの網」）と同じ立場である。

### 合計

| | 件数 |
|---|---|
| Phase 3 完了時点 | 338 |
| 本修正での追加 | 1 |
| **合計** | **339 件 全PASS** |

既存 338 件に回帰が無いことを確認すること。

---

## 実装チェックリスト

### `tools/plot_merit_order_suite.py`

- [ ] **F1-1**: `plot_merit_order_shift()` に `axvline`（需要線）を追加。`get_legend_handles_labels()` より**前**に置き、凡例に載せる
- [ ] **F1-1**: `required_qty` が before/after で一致する場合は黒破線1本、異なる場合はそれぞれの色で2本
- [ ] **F1-2**: 交点 `(required_qty, λ)` に丸マーカー。**凡例には載せない**
- [ ] **F1-3**: λ が `None` のときは水平線・マーカーとも描かない（現状維持）。需要線は引く
- [ ] **F2-1**: `plot_merit_order_shift()` の凡例を `loc="upper left"` に変更、`framealpha=0.9` を付与
- [ ] **F2-2**: `plot_regime_timeline()` の上段凡例を軸外・上・横並びに変更（`bbox_to_anchor=(0.0, 1.01)`, `ncol=2`, `frameon=False`）
- [ ] **F2-2**: 凡例とタイトルが重なっていないこと
- [ ] **F2-2**: **Y軸 `[0, 10]` 固定を変更していないこと**
- [ ] **F3**: `_weeks_label()` をモジュールレベルに追加し、`plot_regime_matrix()` から呼ぶ
- [ ] 図中テキストが引き続き**すべて英語**であること
- [ ] 各関数が引き続き**出力パスを返す**こと、`plt.close(fig)` していること

### `tests/`

- [ ] `tests/test_merit_order_plot.py` に `test_weeks_label_singular_plural` を追加
- [ ] 既存 338 件に回帰がないこと

### テスト実行コマンド

```bash
python -m pytest tests/test_merit_order_plot.py -v
python -m pytest tests/ -q
python -m pytest tests/test_golden.py -v
```

---

## 実装後の確認事項

```bash
python -m tools.plot_merit_order_suite --demo
```

生成された6枚を目視で確認する。

- [ ] `merit_order_shift.png` — **需要線（黒破線）が引かれ、2本の階段との交点に丸マーカーが乗っている**
- [ ] `merit_order_shift.png` — 凡例が左上にあり、After 曲線と重なっていない
- [ ] `merit_order_shift.png` — λ_before / λ_after の水平線が、それぞれ交点マーカーの高さを通っている
- [ ] `regime_timeline.png` — 凡例が軸の外（上）にあり、`supply_risk` のピーク（9〜10）が隠れていない
- [ ] `regime_timeline.png` — Y軸が `0` から `10` のままである
- [ ] `regime_timeline.png` — 凡例とタイトルが重なっていない
- [ ] `regime_matrix.png` — 週数 1 のセルが `(1 week)` と表示されている
- [ ] 全6枚に日本語の豆腐（□）が無い
- [ ] 他の4枚（`merit_order_curve` / `pareto_scatter` / `parallel_coordinates` / `regime_matrix` の配置）に意図しない変化が無い

---

## 補足：今回の目視 QA で「問題なし」と確認できた項目

修正不要だが、記録として残す。

- **`plot_regime_timeline()` の off-by-one**：`risk_weeks` は週ラベルではなく1始まりの位置（`regime_map.py` L365）。実装は `pos - 1` で正しく変換しており、赤帯は H05 / H10 / H12 に出て、下段の戦略帯（cost_lock / dual_source / safety_stock）と正確に一致している。**ずれていない。**
- **`plot_regime_matrix()` の右側ラベル帯**：縮小画像では Tight 行と Surplus 行の色が右端まで伸びているように見えたが、ピクセル値を確認したところ**全行で白**（255,255,255）。`np.zeros((3, 3))` のとおり正しく3列であり、`set_xlim(-0.5, 3.3)` で確保した余白は正しくデータ領域の外にある。**Claude君の誤認であり、修正不要。**
- **`plot_merit_order_curve()` の需要線をまたぐブロック分割**：Alpha Semi（3000〜6000）が需要線 5000 で濃淡に正しく分割されている。
- **V0 の受入条件**：Pareto Front に3点が非支配のまま残っており、1点への収束は解消されている。
- **R3 の軸設計**：share 軸を Merit Order 順に並べた結果、#1 は左（安価なサプライヤー）に山、#3 は最右の SUP_004 に 100% と、**折れ線の形状だけで戦略の性格が読める**状態になっている。設計意図どおり。

### 未確認の描画経路

- **支配解（グレー線）の描画**：`--demo` のデータは3案すべてが非支配のため、`plot_pareto_scatter()` / `plot_parallel_coordinates()` のグレー表示経路は smoke test でしか通っていない。**本修正での対応は不要**だが、将来シナリオを増やした際に目視確認する価値がある。

---

## Git コミット情報

**git 操作は大杉さんが Windows 側ターミナルで実施する**（CLAUDE.md L267）。Code君は実装とテストまでを行い、コミットメッセージ案を提示すること。

```
Phase 3 fix: 図の判読性（需要線・凡例重なり・単複表記）

- F1: plot_merit_order_shift() に需要線を追加
  - required_qty の垂直線（before/after 同値なら黒破線1本）
  - 需要線と各階段の交点 (required_qty, λ) に丸マーカー
  - λ が交点の高さであることを図から読み取れるようにした
  - ※ 設計書 §3.2 の記述漏れに起因（実装ミスではない）

- F2: 凡例がデータに重なる問題を解消
  - plot_merit_order_shift(): loc="upper right" → "upper left"
  - plot_regime_timeline(): 凡例を軸外・上・横並びへ
    （Y軸 [0,10] 固定を維持するため軸内に逃がさない）

- F3: plot_regime_matrix() の週数表記を単複対応
  - _weeks_label() を新設（0 weeks / 1 week / 9 weeks）

新規テスト: 1個（test_weeks_label_singular_plural）
既存テスト: 338個（全て PASS、回帰なし）
計: 339個テスト全て PASS

設計書 requests/Phase3_DesignMD_Visualization.md を rev.4 に改訂
（§3.2 需要線の要件、§4.1 単複表記、§6.1 凡例配置の指針を追記）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EEihXkBiSxhPNk83Uw6CKB
```

---

**修正依頼 完成日**: 2026年9月6日
**指摘・仕様**: Claude君
**承認**: 大杉さん（2026-09-06）
**実装責任**: Code君
