# Phase 1 改善実装 Request Letter
**宛先**: Code君  
**作成日**: 2026年9月6日  
**担当**: Claude君（設計・仕様）→ 大杉さん（レビュー承認）→ Code君（実装）  
**優先度**: HIGH（1-2日で完了推奨）

---

## 概要

Merit Order Analyzer（Phase 1）の実装品質を向上させるため、3つの改善を実施します。全てエラーハンドリング・堅牢性の向上を目的とした軽量な改善で、既存の 16 テストケースは全て PASS を維持する想定です。

**対象ファイル**:
- `wom/visualization/merit_order.py` メイン実装
- `tests/test_merit_order.py` テストスイート

**実装工数**: 約 2-3 時間（テスト含む）  
**リスク**: 低い（既存ロジックの変更なし）

---

## H1: `load_suppliers_from_csv()` エラーハンドリング強化

### 現状

```python
def load_suppliers_from_csv(filepath: str) -> List[Dict]:
    """CSVからサプライヤーデータを読み込み"""
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("pandas is required...")
    
    df = pd.read_csv(filepath)
    return df.to_dict(orient='records')
```

**問題点**:
1. ❌ ファイル存在確認なし → FileNotFoundError が pandas から出て、メッセージ不明確
2. ❌ CSV 形式エラーがキャッチされない → ParserError そのまま
3. ❌ 必須カラムの確認なし → 後で KeyError になる可能性
4. ❌ 戻り値の型チェックなし

### 実装仕様

#### 1-1. ファイル存在確認

```python
import os

if not os.path.exists(filepath):
    raise FileNotFoundError(
        f"Supplier data file not found: {filepath}"
    )
```

**理由**: ファイルパスの誤り（最も多い実運用エラー）を最初に検出  
**メッセージ**: ユーザーにとって明確

#### 1-2. CSV 解析エラーハンドリング

```python
try:
    df = pd.read_csv(filepath)
except pd.errors.ParserError as e:
    raise ValueError(
        f"CSV format error in {filepath}: {str(e)[:100]}"
    ) from e
except Exception as e:
    raise ValueError(
        f"Failed to read CSV {filepath}: {str(e)[:100]}"
    ) from e
```

**理由**: 
- ParserError は ValueError にラップ（統一的なエラー体系）
- メッセージは 100 文字に制限（ユーザー表示用）
- `from e` で原因の追跡可能性確保

#### 1-3. 必須カラム検証

```python
required_columns = [
    "supplier_id", "supplier_name", "unit_cost", "max_supply",
    "lead_time_days", "quality_score"
]

missing_cols = [c for c in required_columns if c not in df.columns]
if missing_cols:
    raise ValueError(
        f"CSV is missing required columns: {', '.join(missing_cols)}"
    )
```

**理由**: 
- CSV スキーマ定義の明確化
- 後続の KeyError を防止
- ユーザーに何をフィックスすべきか明示

#### 1-4. 戻り値検証（オプション）

```python
result = df.to_dict(orient='records')
if not result:
    raise ValueError(
        f"CSV file {filepath} is empty (no data rows)"
    )
return result
```

**理由**: ヘッダのみの空 CSV を検出

### テスト仕様

**新規追加テスト**: `tests/test_merit_order.py` の `TestHelperFunctions` クラスに追加

```python
class TestHelperFunctions:
    """ヘルパー関数のテスト"""
    
    def test_load_suppliers_csv_file_not_found(self):
        """ファイルが存在しない場合"""
        with pytest.raises(FileNotFoundError, match="not found"):
            load_suppliers_from_csv("/nonexistent/path.csv")
    
    def test_load_suppliers_csv_invalid_format(self, tmp_path):
        """CSV 形式が不正な場合"""
        invalid_csv = tmp_path / "invalid.csv"
        invalid_csv.write_text("supplier_id,unit_cost\nSUP_001,abc")  # 数値ではなく文字列
        
        with pytest.raises(ValueError, match="CSV format error"):
            load_suppliers_from_csv(str(invalid_csv))
    
    def test_load_suppliers_csv_missing_columns(self, tmp_path):
        """必須カラムが足りない場合"""
        incomplete_csv = tmp_path / "incomplete.csv"
        incomplete_csv.write_text("supplier_id,supplier_name\nSUP_001,Test")
        
        with pytest.raises(ValueError, match="missing required columns"):
            load_suppliers_from_csv(str(incomplete_csv))
    
    def test_load_suppliers_csv_empty_file(self, tmp_path):
        """ファイルが空の場合"""
        empty_csv = tmp_path / "empty.csv"
        empty_csv.write_text("supplier_id,supplier_name,unit_cost,max_supply,lead_time_days,quality_score")
        
        with pytest.raises(ValueError, match="empty"):
            load_suppliers_from_csv(str(empty_csv))
```

**期待結果**: 4 つの新規テスト全て PASS

---

## H2: `export_to_json()` 戻り値管理

### 現状

```python
def export_to_json(self, result: Dict, filepath: str):
    """結果をJSON形式で保存"""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"✅ Results exported to {filepath}")
```

**問題点**:
1. ❌ 戻り値なし → 呼び出し側で成功/失敗を判定できない
2. ❌ 例外がキャッチされない → IOError/TypeError で不正終了
3. ❌ 出力ファイルの確認ができない → 実際に書き込まれたのか不明

### 実装仕様

#### 2-1. 戻り値型の追加

```python
def export_to_json(self, result: Dict, filepath: str) -> bool:
    """結果をJSON形式で保存
    
    Args:
        result: Merit Order 分析結果辞書
        filepath: 出力ファイルパス
        
    Returns:
        bool: 成功時 True、失敗時 False
        
    Raises:
        なし（内部で全エラーをハンドル）
    """
```

#### 2-2. エラーハンドリング実装

```python
def export_to_json(self, result: Dict, filepath: str) -> bool:
    """結果をJSON形式で保存"""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        # 書き込み確認
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            print(f"✅ Results exported to {filepath}")
            return True
        else:
            print(f"❌ Export failed: file not created or empty")
            return False
            
    except IOError as e:
        print(f"❌ IOError: Cannot write to {filepath}: {e}")
        return False
        
    except TypeError as e:
        print(f"❌ TypeError: Result contains non-serializable data: {e}")
        return False
        
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False
```

#### 2-3. 呼び出し側の使用例

```python
# メイン処理での使用
if analyzer.export_to_json(result, "output.json"):
    print("Processing complete")
else:
    print("Export failed - check file path and permissions")
```

### テスト仕様

```python
class TestExportToJSON:
    """JSON エクスポート機能のテスト"""
    
    def test_export_to_json_success(self, sample_suppliers, tmp_path):
        """正常な出力"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({"required_qty": 5000})
        
        output_file = tmp_path / "output.json"
        success = analyzer.export_to_json(result, str(output_file))
        
        assert success == True
        assert output_file.exists()
        assert output_file.stat().st_size > 0
        
        # JSON 形式確認
        with open(output_file) as f:
            saved = json.load(f)
        assert saved["week"] == result["week"]
    
    def test_export_to_json_invalid_path(self, sample_suppliers):
        """出力パスが不正な場合"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({"required_qty": 5000})
        
        # 存在しないディレクトリへの出力
        invalid_path = "/invalid/nonexistent/path/output.json"
        success = analyzer.export_to_json(result, invalid_path)
        
        assert success == False
    
    def test_export_to_json_non_serializable(self, sample_suppliers, tmp_path):
        """シリアライズ不可のデータが含まれている場合"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({"required_qty": 5000})
        
        # 日付オブジェクトを無理やり挿入（テスト用）
        result["test_date"] = datetime.now()
        
        output_file = tmp_path / "output.json"
        success = analyzer.export_to_json(result, str(output_file))
        
        # TypeError をハンドルして False を返すはず
        assert success == False
```

**期待結果**: 3 つのテスト全て PASS

---

## H3: 為替レート値の妥当性チェック追加

### 現状

```python
def validate_suppliers(self) -> bool:
    """サプライヤーデータの整合性をチェック"""
    # ... 既存チェック ...
    
    if supplier.get("unit_cost", 0) <= 0:
        # unit_cost チェック
    
    # 為替レート（exchange_rate）はチェックなし
    # デフォルト値 1.0 を使用
```

**問題点**:
1. ❌ exchange_rate の値が 0 以下でもスルー
2. ❌ exchange_rate が NaN や負の値でも検出されない
3. ❌ 多通貨対応の信頼性が低い

### 実装仕様

#### 3-1. exchange_rate 検証ロジック

```python
def validate_suppliers(self) -> bool:
    """サプライヤーデータの整合性をチェック"""
    # ... 既存の検証コード ...
    
    for idx, supplier in enumerate(self.suppliers):
        # 既存の unit_cost, max_supply, quality_score チェック
        
        # 新規追加: exchange_rate チェック
        exchange_rate = supplier.get("exchange_rate", 1.0)
        
        if exchange_rate <= 0:
            self.validation_errors.append(
                f"Supplier #{idx} ({supplier.get('supplier_id')}): "
                f"exchange_rate must be > 0, got {exchange_rate}"
            )
        
        # NaN チェック（Python では math.isnan() を使用）
        try:
            if math.isnan(float(exchange_rate)):
                self.validation_errors.append(
                    f"Supplier #{idx} ({supplier.get('supplier_id')}): "
                    f"exchange_rate is NaN"
                )
        except (ValueError, TypeError):
            self.validation_errors.append(
                f"Supplier #{idx} ({supplier.get('supplier_id')}): "
                f"exchange_rate must be a number, got {type(exchange_rate).__name__}"
            )
```

#### 3-2. 必要なインポート追加

```python
import math  # 既存のインポート群に追加
```

#### 3-3. SupplierInfo dataclass の型ヒント確認

```python
@dataclass
class SupplierInfo:
    """サプライヤー情報"""
    supplier_id: str
    supplier_name: str
    unit_cost: float
    max_supply: int
    lead_time_days: int
    quality_score: float
    currency: str = "USD"
    exchange_rate: float = 1.0  # ← デフォルト 1.0 を明示
```

**既に定義済みなので変更不要**。ただし docstring に「exchange_rate は > 0」の制約を追記：

```python
@dataclass
class SupplierInfo:
    """サプライヤー情報
    
    Attributes:
        exchange_rate: 為替レート（必ず > 0、デフォルト 1.0）
            例: KRW は 0.00075、USD は 1.0
    """
    exchange_rate: float = 1.0
```

### テスト仕様

```python
class TestExchangeRateValidation:
    """為替レート検証のテスト"""
    
    def test_exchange_rate_zero(self):
        """為替レートが 0 の場合"""
        invalid_suppliers = [
            {
                "supplier_id": "SUP_ZERO",
                "supplier_name": "Test",
                "unit_cost": 50,
                "max_supply": 1000,
                "lead_time_days": 7,
                "quality_score": 90,
                "exchange_rate": 0,  # ❌ 無効
            }
        ]
        analyzer = MeritOrderAnalyzer(invalid_suppliers)
        assert analyzer.validate_suppliers() == False
        assert any("exchange_rate" in e for e in analyzer.validation_errors)
    
    def test_exchange_rate_negative(self):
        """為替レートが負の場合"""
        invalid_suppliers = [
            {
                "supplier_id": "SUP_NEG",
                "supplier_name": "Test",
                "unit_cost": 50,
                "max_supply": 1000,
                "lead_time_days": 7,
                "quality_score": 90,
                "exchange_rate": -0.5,  # ❌ 無効
            }
        ]
        analyzer = MeritOrderAnalyzer(invalid_suppliers)
        assert analyzer.validate_suppliers() == False
    
    def test_exchange_rate_nan(self):
        """為替レートが NaN の場合"""
        invalid_suppliers = [
            {
                "supplier_id": "SUP_NAN",
                "supplier_name": "Test",
                "unit_cost": 50,
                "max_supply": 1000,
                "lead_time_days": 7,
                "quality_score": 90,
                "exchange_rate": float('nan'),  # ❌ 無効
            }
        ]
        analyzer = MeritOrderAnalyzer(invalid_suppliers)
        assert analyzer.validate_suppliers() == False
    
    def test_exchange_rate_string(self):
        """為替レートが文字列の場合"""
        invalid_suppliers = [
            {
                "supplier_id": "SUP_STR",
                "supplier_name": "Test",
                "unit_cost": 50,
                "max_supply": 1000,
                "lead_time_days": 7,
                "quality_score": 90,
                "exchange_rate": "abc",  # ❌ 無効
            }
        ]
        analyzer = MeritOrderAnalyzer(invalid_suppliers)
        assert analyzer.validate_suppliers() == False
        assert any("must be a number" in e for e in analyzer.validation_errors)
    
    def test_exchange_rate_valid(self):
        """為替レートが有効な場合"""
        valid_suppliers = [
            {
                "supplier_id": "SUP_VALID",
                "supplier_name": "Test",
                "unit_cost": 50,
                "max_supply": 1000,
                "lead_time_days": 7,
                "quality_score": 90,
                "exchange_rate": 0.00075,  # ✅ 有効
            }
        ]
        analyzer = MeritOrderAnalyzer(valid_suppliers)
        assert analyzer.validate_suppliers() == True
```

**期待結果**: 5 つのテスト全て PASS

---

## 実装チェックリスト

### ファイル: `wom/visualization/merit_order.py`

- [ ] `load_suppliers_from_csv()`
  - [ ] ファイル存在確認追加
  - [ ] CSV 解析エラーハンドリング追加
  - [ ] 必須カラム検証追加
  - [ ] 空ファイルチェック追加
  - [ ] docstring 更新

- [ ] `export_to_json()`
  - [ ] 戻り値型を `bool` に変更
  - [ ] IOError ハンドリング追加
  - [ ] TypeError ハンドリング追加
  - [ ] ファイル存在確認追加
  - [ ] docstring 更新

- [ ] `validate_suppliers()`
  - [ ] exchange_rate 検証ロジック追加
  - [ ] math モジュール import 追加
  - [ ] エラーメッセージ形式統一
  - [ ] docstring 更新

### ファイル: `tests/test_merit_order.py`

- [ ] `TestHelperFunctions` クラス新規作成（4 テスト）
- [ ] `TestExportToJSON` クラス新規作成（3 テスト）
- [ ] `TestExchangeRateValidation` クラス新規作成（5 テスト）
- [ ] 既存 16 テストが全て PASS であることを確認
- [ ] 新規 12 テスト + 既存 16 テスト = 計 28 テスト PASS を確認

### テスト実行コマンド

```bash
# 全テスト実行
python -m pytest tests/test_merit_order.py -v

# テストカバレッジ確認（推奨）
python -m pytest tests/test_merit_order.py --cov=wom.visualization.merit_order --cov-report=term-missing
```

---

## 実装後の確認事項

### 1. 既存テスト互換性

```bash
# 既存 16 テストが全て PASS であることを確認
pytest tests/test_merit_order.py::TestMeritOrderAnalyzerValidation -v
pytest tests/test_merit_order.py::TestMeritOrderCalculation -v
pytest tests/test_merit_order.py::TestExchangeRateHandling -v
pytest tests/test_merit_order.py::TestAverageCalculations -v
pytest tests/test_merit_order.py::TestSingleSourceConstraint -v
pytest tests/test_merit_order.py::TestEndToEnd -v
```

**期待結果**: 全 16 テスト PASS（変更なし）

### 2. サンプル実行確認

```bash
# メイン実行（新しいエラーハンドリングが有効か確認）
python -m wom.visualization.merit_order

# エラーケースのテスト
python -c "
from wom.visualization.merit_order import load_suppliers_from_csv
try:
    load_suppliers_from_csv('/nonexistent/path.csv')
except FileNotFoundError as e:
    print(f'✅ FileNotFoundError caught: {e}')
"
```

### 3. 統合テスト（実データ）

```python
# Phase 1 最終確認スクリプト
from wom.visualization.merit_order import MeritOrderAnalyzer, load_suppliers_from_csv

# ✅ H1: CSV 読み込みエラーハンドリング
try:
    suppliers = load_suppliers_from_csv("data/sample/rice-japan-2027-2028/supplier_master.csv")
    print(f"✅ H1 OK: Loaded {len(suppliers)} suppliers")
except Exception as e:
    print(f"❌ H1 Error: {e}")

# ✅ H2: JSON エクスポート戻り値
analyzer = MeritOrderAnalyzer(suppliers)
result = analyzer.calculate_merit_order({"required_qty": 5000})
success = analyzer.export_to_json(result, "output.json")
print(f"✅ H2 OK: export_to_json returned {success}")

# ✅ H3: 為替レート検証
print(f"✅ H3 OK: All suppliers validated with exchange_rate checks")
```

---

## Phase 2 への進出条件

以下の条件を全て満たしたら、Phase 2 (Regime Map) の実装に進みます：

1. ✅ 新規 12 テスト全て PASS
2. ✅ 既存 16 テスト全て PASS（変更なし）
3. ✅ 実データでのサンプル実行確認
4. ✅ 大杉さんの最終確認
5. ✅ Code君の実装完了報告

---

## 補足・参考

### エラーメッセージの設計思想

全ての新規エラーメッセージは以下の形式に統一：
```
❌ [エラータイプ]: [具体的な問題] at [ファイル/ノード]
```

例：
```
❌ FileNotFoundError: Supplier data file not found: /path/to/file.csv
❌ ValueError: CSV is missing required columns: unit_cost, max_supply
❌ TypeError: exchange_rate must be a number, got <class 'str'>
```

### 既知の非対象項目

以下の項目は Phase 1 スコープ外（Phase 2 以降で検討）：
- パフォーマンス最適化（大規模データセット対応）
- 為替レート履歴管理
- 複数目標最適化への拡張

---

## Git コミット情報

### コミットメッセージ形式

```
Phase 1 改善: エラーハンドリング強化 (H1/H2/H3)

- H1: load_suppliers_from_csv() エラーハンドリング強化
  - ファイル存在確認
  - CSV 解析エラーキャッチ
  - 必須カラム検証
  - 空ファイルチェック

- H2: export_to_json() 戻り値管理
  - 戻り値型を bool に変更
  - IOError/TypeError ハンドリング

- H3: validate_suppliers() 為替レート検証
  - exchange_rate 妥当性チェック
  - NaN/負数/非数値検出

新規テスト: 12個
既存テスト: 16個（全て PASS）
計: 28個テスト全て PASS

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
```

---

**Request Letter 完成日**: 2026年9月6日  
**設計責任**: Claude君  
**レビュー承認**: （大杉さんの確認待ち）  
**実装責任**: Code君

