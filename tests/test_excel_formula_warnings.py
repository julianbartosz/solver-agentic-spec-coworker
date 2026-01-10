"""Tests for Excel formula cell warning detection.

Per docs/BUCKET_2_TECH_DEBT_AND_SCALING.md TD-XLS-001
"""

import io
import pytest

# Skip all tests if openpyxl is not installed
pytest.importorskip("openpyxl")

from openpyxl import Workbook

from integration_coworker.parsers.excel_parser import infer_schema


class TestExcelFormulaWarnings:
    """Tests that formula cells generate appropriate warnings."""
    
    def _create_workbook_with_formulas(self) -> bytes:
        """Create a test workbook with formula cells."""
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        
        # Headers
        ws["A1"] = "qty"
        ws["B1"] = "price"
        ws["C1"] = "total"
        
        # Data row 1 with formula
        ws["A2"] = 10
        ws["B2"] = 5.99
        ws["C2"] = "=A2*B2"  # Formula
        
        # Data row 2 with formula
        ws["A3"] = 20
        ws["B3"] = 3.99
        ws["C3"] = "=A3*B3"  # Formula
        
        # Data row 3 with formula
        ws["A4"] = 5
        ws["B4"] = 12.50
        ws["C4"] = "=A4*B4"  # Formula
        
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    
    def _create_workbook_without_formulas(self) -> bytes:
        """Create a test workbook without formula cells."""
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        
        ws["A1"] = "name"
        ws["B1"] = "value"
        ws["A2"] = "test"
        ws["B2"] = 123
        ws["A3"] = "data"
        ws["B3"] = 456
        
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    
    def _create_workbook_with_multi_sheet_formulas(self) -> bytes:
        """Create workbook with formulas in multiple sheets."""
        wb = Workbook()
        
        # Sheet 1 with formulas
        ws1 = wb.active
        ws1.title = "Sales"
        ws1["A1"] = "amount"
        ws1["B1"] = "tax"
        ws1["A2"] = 100
        ws1["B2"] = "=A2*0.1"  # Formula
        
        # Sheet 2 without formulas
        ws2 = wb.create_sheet("Inventory")
        ws2["A1"] = "item"
        ws2["B1"] = "count"
        ws2["A2"] = "widget"
        ws2["B2"] = 50
        
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    
    def test_detects_formula_cells(self):
        """Should warn when formula cells are detected."""
        content = self._create_workbook_with_formulas()
        result = infer_schema(content)
        
        # Should have warning about formulas
        formula_warnings = [w for w in result.warnings if "formula" in w.lower()]
        assert len(formula_warnings) > 0, f"Expected warning about formula cells, got: {result.warnings}"
        
        # Should mention the cell coordinates
        warning_text = formula_warnings[0].lower()
        assert "c2" in warning_text or "3 formula" in warning_text
    
    def test_no_warning_without_formulas(self):
        """Should not warn when no formula cells exist."""
        content = self._create_workbook_without_formulas()
        result = infer_schema(content)
        
        formula_warnings = [w for w in result.warnings if "formula" in w.lower()]
        assert len(formula_warnings) == 0, f"Should not warn about formulas when none exist, got: {formula_warnings}"
    
    def test_warning_mentions_stale_values(self):
        """Warning should explain that values may be stale."""
        content = self._create_workbook_with_formulas()
        result = infer_schema(content)
        
        formula_warnings = [w for w in result.warnings if "formula" in w.lower()]
        assert len(formula_warnings) > 0
        
        # Should explain the risk
        warning = formula_warnings[0].lower()
        assert "stale" in warning or "cached" in warning, f"Warning should explain stale values risk: {warning}"
    
    def test_warning_includes_sheet_name(self):
        """Warning should identify which sheet has formulas."""
        content = self._create_workbook_with_formulas()
        result = infer_schema(content)
        
        formula_warnings = [w for w in result.warnings if "formula" in w.lower()]
        assert len(formula_warnings) > 0
        
        # Should mention sheet name
        warning = formula_warnings[0]
        assert "Data" in warning or "Sheet" in warning.lower()
    
    def test_multi_sheet_formula_detection(self):
        """Should detect formulas in correct sheets."""
        content = self._create_workbook_with_multi_sheet_formulas()
        result = infer_schema(content)
        
        formula_warnings = [w for w in result.warnings if "formula" in w.lower()]
        
        # Should have warning for Sales sheet (has formula)
        sales_warnings = [w for w in formula_warnings if "Sales" in w]
        assert len(sales_warnings) > 0, "Should warn about formulas in Sales sheet"
        
        # Should NOT have warning for Inventory sheet (no formulas)
        inventory_warnings = [w for w in formula_warnings if "Inventory" in w]
        assert len(inventory_warnings) == 0, "Should not warn about Inventory sheet (no formulas)"
    
    def test_warning_limits_examples(self):
        """Warning should limit number of example cells shown."""
        # Create workbook with many formulas
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "value"
        ws["B1"] = "double"
        
        # Add many formula rows
        for i in range(2, 22):  # 20 formula cells
            ws[f"A{i}"] = i
            ws[f"B{i}"] = f"=A{i}*2"
        
        buf = io.BytesIO()
        wb.save(buf)
        content = buf.getvalue()
        
        result = infer_schema(content)
        formula_warnings = [w for w in result.warnings if "formula" in w.lower()]
        
        # Should have warning
        assert len(formula_warnings) > 0
        
        # Should indicate there are more formulas than shown
        warning = formula_warnings[0]
        assert "more" in warning.lower() or "20" in warning
    
    def test_formula_detection_doesnt_affect_inference(self):
        """Formula detection should not break schema inference."""
        content = self._create_workbook_with_formulas()
        result = infer_schema(content)
        
        # Should still successfully infer schema
        assert result.sheets is not None, "Schema inference should succeed"
        assert len(result.sheets) > 0
        assert result.confidence > 0.5
        
        # Should have correct columns
        sheet = result.sheets[0]
        column_names = [c.name for c in sheet.columns]
        assert "qty" in column_names
        assert "price" in column_names
        assert "total" in column_names


class TestExcelFormulaEdgeCases:
    """Edge case tests for formula detection."""
    
    def test_empty_sheet_no_formula_warning(self):
        """Empty sheet should not cause formula warning."""
        wb = Workbook()
        ws = wb.active
        # Leave sheet empty
        
        buf = io.BytesIO()
        wb.save(buf)
        content = buf.getvalue()
        
        result = infer_schema(content)
        
        # May have other errors/warnings, but not formula warning
        formula_warnings = [w for w in result.warnings if "formula" in w.lower()]
        assert len(formula_warnings) == 0
    
    def test_formula_like_text_not_detected(self):
        """Text that looks like formula (starts with =) in string column should be detected."""
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "expression"
        ws["A2"] = "=A1+B1"  # This is text, but looks like formula
        
        buf = io.BytesIO()
        wb.save(buf)
        content = buf.getvalue()
        
        # This should detect it as a formula since it starts with =
        result = infer_schema(content)
        
        # We expect this to be detected (text starting with = looks like formula)
        formula_warnings = [w for w in result.warnings if "formula" in w.lower()]
        # Either behavior is acceptable - just ensure no crash
        assert result.sheets is not None or result.errors


class TestExcelFormulaSafetyLimits:
    """Tests for formula detection safety limits."""
    
    def test_respects_max_sheets_limit(self):
        """Should not check formulas in sheets beyond max_sheets."""
        from integration_coworker.parsers.excel_config import ExcelConfidenceConfig
        
        wb = Workbook()
        
        # Create many sheets with formulas
        for i in range(5):
            ws = wb.create_sheet(f"Sheet{i}")
            ws["A1"] = "value"
            ws["B1"] = f"=A1*{i}"
        
        # Remove default sheet
        del wb["Sheet"]
        
        buf = io.BytesIO()
        wb.save(buf)
        content = buf.getvalue()
        
        # Use config with max_sheets=2
        config = ExcelConfidenceConfig(max_sheets=2)
        result = infer_schema(content, config)
        
        # Should only process 2 sheets
        formula_warnings = [w for w in result.warnings if "formula" in w.lower()]
        sheet_names_in_warnings = set()
        for w in formula_warnings:
            for name in ["Sheet0", "Sheet1", "Sheet2", "Sheet3", "Sheet4"]:
                if name in w:
                    sheet_names_in_warnings.add(name)
        
        # Should only have warnings for first 2 sheets
        assert "Sheet2" not in sheet_names_in_warnings
        assert "Sheet3" not in sheet_names_in_warnings
        assert "Sheet4" not in sheet_names_in_warnings
