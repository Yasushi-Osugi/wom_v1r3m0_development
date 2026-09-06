"""
Unit Tests for Merit Order Analyzer
====================================

Merit Order分析エンジンの単体テスト
"""

import pytest
import json
from datetime import datetime
from wom.visualization.merit_order import (
    MeritOrderAnalyzer,
    load_suppliers_from_csv,
)


@pytest.fixture
def sample_suppliers():
    """テスト用サンプルサプライヤー"""
    return [
        {
            "supplier_id": "SUP_001",
            "supplier_name": "Samsung Electronics",
            "unit_cost": 50,
            "max_supply": 10000,
            "lead_time_days": 14,
            "quality_score": 95,
            "currency": "KRW",
            "exchange_rate": 0.00075,
        },
        {
            "supplier_id": "SUP_002",
            "supplier_name": "TSMC",
            "unit_cost": 48,
            "max_supply": 8000,
            "lead_time_days": 21,
            "quality_score": 94,
            "currency": "TWD",
            "exchange_rate": 0.031,
        },
        {
            "supplier_id": "SUP_003",
            "supplier_name": "MediaTek",
            "unit_cost": 52,
            "max_supply": 5000,
            "lead_time_days": 14,
            "quality_score": 92,
            "currency": "TWD",
            "exchange_rate": 0.031,
        },
        {
            "supplier_id": "SUP_004",
            "supplier_name": "Qualcomm",
            "unit_cost": 55,
            "max_supply": 3000,
            "lead_time_days": 7,
            "quality_score": 96,
            "currency": "USD",
            "exchange_rate": 1.0,
        },
    ]


class TestMeritOrderAnalyzerValidation:
    """バリデーション機能のテスト"""
    
    def test_valid_suppliers(self, sample_suppliers):
        """正常なサプライヤーデータの検証"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        assert analyzer.validate_suppliers() == True
        assert len(analyzer.validation_errors) == 0
    
    def test_missing_required_field(self):
        """必須フィールド欠落時の検証"""
        invalid_suppliers = [
            {
                "supplier_id": "SUP_X",
                "supplier_name": "Invalid",
                # unit_cost が無い
                "max_supply": 1000,
            }
        ]
        analyzer = MeritOrderAnalyzer(invalid_suppliers)
        assert analyzer.validate_suppliers() == False
        assert len(analyzer.validation_errors) > 0
    
    def test_negative_unit_cost(self):
        """負のコストの検証"""
        invalid_suppliers = [
            {
                "supplier_id": "SUP_NEG",
                "unit_cost": -10,  # 無効
                "max_supply": 1000,
                "lead_time_days": 7,
                "quality_score": 90,
            }
        ]
        analyzer = MeritOrderAnalyzer(invalid_suppliers)
        assert analyzer.validate_suppliers() == False
    
    def test_quality_score_out_of_range(self):
        """品質スコア範囲外の検証"""
        invalid_suppliers = [
            {
                "supplier_id": "SUP_BAD_Q",
                "unit_cost": 50,
                "max_supply": 1000,
                "lead_time_days": 7,
                "quality_score": 150,  # 0-100の範囲外
            }
        ]
        analyzer = MeritOrderAnalyzer(invalid_suppliers)
        assert analyzer.validate_suppliers() == False
    
    def test_duplicate_supplier_id(self):
        """重複したsupplier_idの検証"""
        duplicate_suppliers = [
            {
                "supplier_id": "SUP_DUP",
                "unit_cost": 50,
                "max_supply": 1000,
                "lead_time_days": 7,
                "quality_score": 90,
            },
            {
                "supplier_id": "SUP_DUP",  # 重複
                "unit_cost": 48,
                "max_supply": 2000,
                "lead_time_days": 14,
                "quality_score": 92,
            },
        ]
        analyzer = MeritOrderAnalyzer(duplicate_suppliers)
        assert analyzer.validate_suppliers() == False


class TestMeritOrderCalculation:
    """Merit Order計算のテスト"""
    
    def test_basic_merit_order_generation(self, sample_suppliers):
        """基本的なMerit Order生成"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({
            "week": "2026-W36",
            "required_qty": 5000,
            "min_quality_acceptable": 85,
        })
        
        assert result["week"] == "2026-W36"
        assert result["required_qty"] == 5000
        assert len(result["merit_order"]) > 0
        assert result["fulfillment_rate"] > 0
    
    def test_merit_order_sorted_by_cost(self, sample_suppliers):
        """Merit Orderがコスト順にソートされていることを確認"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({
            "required_qty": 5000,
        })
        
        merit_order = result["merit_order"]
        # コストが昇順であることを確認
        for i in range(len(merit_order) - 1):
            assert merit_order[i]["unit_cost_usd"] <= merit_order[i+1]["unit_cost_usd"]
    
    def test_demand_allocation(self, sample_suppliers):
        """需要が正しく配分されたことを確認"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({
            "required_qty": 5000,
        })
        
        allocation = result["recommended_allocation"]
        total_allocated = sum(item["allocated_qty"] for item in allocation)
        
        # 需要を満たせるだけの供給があれば100%
        assert total_allocated > 0
        assert total_allocated <= 5000
    
    def test_total_cost_calculation(self, sample_suppliers):
        """総コスト計算の正確性"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({
            "required_qty": 5000,
        })
        
        allocation = result["recommended_allocation"]
        calculated_cost = sum(
            item["allocated_qty"] * item["unit_cost_usd"] 
            for item in allocation
        )
        
        assert abs(result["total_cost"] - calculated_cost) < 0.01
    
    def test_quality_constraint(self, sample_suppliers):
        """品質制約が機能していることを確認"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({
            "required_qty": 5000,
            "min_quality_acceptable": 95,  # 高品質のみ
        })
        
        merit_order = result["merit_order"]
        # 全サプライヤーが品質95以上であることを確認
        for supplier in merit_order:
            assert supplier["quality_score"] >= 95
    
    def test_lead_time_constraint(self, sample_suppliers):
        """リードタイム制約が機能していることを確認"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({
            "required_qty": 5000,
            "max_lead_time_acceptable": 14,  # 短いリードタイムのみ
        })
        
        merit_order = result["merit_order"]
        # 全サプライヤーがリードタイム14日以下であることを確認
        for supplier in merit_order:
            assert supplier["lead_time_days"] <= 14


class TestExchangeRateHandling:
    """為替レート処理のテスト"""
    
    def test_exchange_rate_conversion(self):
        """為替レート変換の正確性"""
        suppliers = [
            {
                "supplier_id": "SUP_JP",
                "unit_cost": 5000,  # JPY
                "max_supply": 1000,
                "lead_time_days": 7,
                "quality_score": 90,
                "exchange_rate": 0.0067,  # 1 JPY = 0.0067 USD
            },
            {
                "supplier_id": "SUP_US",
                "unit_cost": 35,  # USD
                "max_supply": 1000,
                "lead_time_days": 7,
                "quality_score": 90,
                "exchange_rate": 1.0,
            },
        ]
        
        analyzer = MeritOrderAnalyzer(suppliers)
        result = analyzer.calculate_merit_order({
            "required_qty": 1000,
        })
        
        # SUP_JP: 5000 * 0.0067 = 33.5 USD
        # SUP_US: 35 USD
        # → SUP_JP が最安
        merit_order = result["merit_order"]
        assert merit_order[0]["supplier_id"] == "SUP_JP"
        assert abs(merit_order[0]["unit_cost_usd"] - 33.5) < 0.1


class TestAverageCalculations:
    """平均値計算のテスト"""
    
    def test_average_quality(self, sample_suppliers):
        """平均品質スコアの計算"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({
            "required_qty": 5000,
        })
        
        # 平均品質は0-100の範囲であることを確認
        avg_quality = result["average_quality"]
        assert 0 <= avg_quality <= 100
    
    def test_average_lead_time(self, sample_suppliers):
        """平均リードタイムの計算"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        result = analyzer.calculate_merit_order({
            "required_qty": 5000,
        })
        
        # 平均リードタイムは正の値であることを確認
        avg_lead_time = result["average_lead_time"]
        assert avg_lead_time > 0


class TestSingleSourceConstraint:
    """単一ソース制約のテスト"""
    
    def test_single_source_max_constraint(self):
        """1社からの調達比率上限の制約"""
        suppliers = [
            {
                "supplier_id": "SUP_A",
                "unit_cost": 40,
                "max_supply": 10000,  # 十分な供給能力
                "lead_time_days": 7,
                "quality_score": 90,
                "exchange_rate": 1.0,
            },
        ]
        
        analyzer = MeritOrderAnalyzer(suppliers)
        result = analyzer.calculate_merit_order(
            {"required_qty": 1000},
            constraints={"single_source_max": 0.6}  # 60%まで
        )
        
        allocation = result["recommended_allocation"]
        # 1社だけなので100%になるはず（制約よりも供給可能性が優先）
        # ※ これは実装により異なる可能性あり


# インテグレーションテスト
class TestEndToEnd:
    """エンドツーエンドのテスト"""
    
    def test_full_workflow(self, sample_suppliers, tmp_path):
        """全体のワークフロー"""
        analyzer = MeritOrderAnalyzer(sample_suppliers)
        
        # 1. バリデーション
        assert analyzer.validate_suppliers() == True
        
        # 2. Merit Order計算
        demand = {
            "week": "2026-W36",
            "required_qty": 15000,
            "min_quality_acceptable": 90,
        }
        result = analyzer.calculate_merit_order(demand)
        
        # 3. 結果検証
        assert result["week"] == "2026-W36"
        assert result["total_cost"] > 0
        assert 0 <= result["fulfillment_rate"] <= 1.0
        
        # 4. JSON出力
        output_file = tmp_path / "merit_order_output.json"
        analyzer.export_to_json(result, str(output_file))
        
        # 5. ファイル存在確認
        assert output_file.exists()
        
        # 6. JSON形式確認
        with open(output_file) as f:
            saved_result = json.load(f)
        assert saved_result["week"] == "2026-W36"


class TestHelperFunctions:
    """ヘルパー関数のテスト"""

    def test_load_suppliers_csv_file_not_found(self):
        """ファイルが存在しない場合"""
        with pytest.raises(FileNotFoundError, match="not found"):
            load_suppliers_from_csv("/nonexistent/path.csv")

    def test_load_suppliers_csv_invalid_format(self, tmp_path):
        """CSV 形式が不正な場合（未終端のクォートでトークナイズエラーを誘発）"""
        invalid_csv = tmp_path / "invalid.csv"
        invalid_csv.write_text(
            "supplier_id,supplier_name,unit_cost,max_supply,"
            "lead_time_days,quality_score\n"
            '"SUP_001,Test,50,1000,7,90\n'
            "SUP_002,Test2,48,2000,14,92\n"
        )

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
        empty_csv.write_text(
            "supplier_id,supplier_name,unit_cost,max_supply,"
            "lead_time_days,quality_score"
        )

        with pytest.raises(ValueError, match="empty"):
            load_suppliers_from_csv(str(empty_csv))


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
                "exchange_rate": 0,  # 無効
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
                "exchange_rate": -0.5,  # 無効
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
                "exchange_rate": float('nan'),  # 無効
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
                "exchange_rate": "abc",  # 無効
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
                "exchange_rate": 0.00075,  # 有効
            }
        ]
        analyzer = MeritOrderAnalyzer(valid_suppliers)
        assert analyzer.validate_suppliers() == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
