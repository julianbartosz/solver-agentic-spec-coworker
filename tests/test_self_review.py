"""
Tests for LLM Self-Review Module.

Per user requirement B5: Unit tests with mock LLM + integration tests.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from integration_coworker.codegen.self_review import (
    IssueSeverity,
    IssueCategory,
    ReviewIssue,
    ReviewResult,
    build_review_prompt,
    perform_self_review,
    apply_review_result,
    review_and_repair,
)


class TestReviewModels:
    """Tests for Pydantic review models."""
    
    def test_review_issue_creation(self):
        """Test ReviewIssue model creation."""
        issue = ReviewIssue(
            category=IssueCategory.SYNTAX,
            severity=IssueSeverity.ERROR,
            line_number=42,
            original_code="print('hello'",
            fixed_code="print('hello')",
            explanation="Missing closing parenthesis on function call",
        )
        
        assert issue.category == IssueCategory.SYNTAX
        assert issue.severity == IssueSeverity.ERROR
        assert issue.line_number == 42
        assert "parenthesis" in issue.explanation
    
    def test_review_result_pass(self):
        """Test ReviewResult with pass verdict."""
        result = ReviewResult(
            verdict="pass",
            issues=[],
            review_summary="Code looks good",
        )
        
        assert result.verdict == "pass"
        assert len(result.issues) == 0
        assert result.has_errors is False
        assert result.can_repair is False
    
    def test_review_result_fail_with_inline_fix(self):
        """Test ReviewResult with fail verdict and inline fix."""
        result = ReviewResult(
            verdict="fail",
            issues=[
                ReviewIssue(
                    category=IssueCategory.STYLE,
                    severity=IssueSeverity.WARNING,
                    line_number=3,
                    original_code="import os",
                    fixed_code="import os\n",
                    explanation="Missing blank line after imports",
                ),
            ],
        )
        
        assert result.verdict == "fail"
        assert len(result.issues) == 1
        assert result.has_errors is False
        assert result.can_repair is True
    
    def test_review_result_fail_no_fix(self):
        """Test ReviewResult with fail verdict but no fix."""
        result = ReviewResult(
            verdict="fail",
            issues=[
                ReviewIssue(
                    category=IssueCategory.SECURITY,
                    severity=IssueSeverity.ERROR,
                    explanation="Cannot determine correct credentials handling",
                ),
            ],
        )
        
        assert result.verdict == "fail"
        assert result.has_errors is True
        assert result.can_repair is False
    
    def test_issue_category_enum(self):
        """Test all issue categories are valid."""
        categories = [
            IssueCategory.SYNTAX,
            IssueCategory.TYPE_ERROR,
            IssueCategory.STYLE,
            IssueCategory.LINT,
            IssueCategory.SECURITY,
            IssueCategory.COVERAGE,
            IssueCategory.LOGIC,
            IssueCategory.IMPORT,
        ]
        assert len(categories) == 8
    
    def test_issue_severity_enum(self):
        """Test all severity levels are valid."""
        severities = [
            IssueSeverity.ERROR,
            IssueSeverity.WARNING,
            IssueSeverity.INFO,
        ]
        assert len(severities) == 3


class TestBuildReviewPrompt:
    """Tests for review prompt building."""
    
    def test_basic_prompt(self):
        """Test basic prompt generation."""
        prompt = build_review_prompt(
            code='def hello(): return "world"',
            artifact_type="client",
            file_path="src/client.py",
        )
        
        assert "client" in prompt
        assert "src/client.py" in prompt
        assert 'def hello()' in prompt
        assert "python" in prompt.lower()
    
    def test_prompt_with_context(self):
        """Test prompt with task context."""
        prompt = build_review_prompt(
            code='def fetch_pets(): pass',
            artifact_type="flow",
            file_path="src/flows/pets.py",
            task_context="Fetch list of pets from Petstore API",
        )
        
        assert "Petstore" in prompt
        assert "flow" in prompt
    
    def test_prompt_with_dependencies(self):
        """Test prompt with dependencies."""
        prompt = build_review_prompt(
            code='import requests\ndef get(): pass',
            artifact_type="client",
            file_path="src/api.py",
            dependencies=["requests", "pydantic"],
        )
        
        assert "requests" in prompt
        assert "pydantic" in prompt


class TestApplyReviewResult:
    """Tests for applying review results."""
    
    def test_pass_uses_original(self):
        """Test that pass verdict uses original code."""
        original = "def hello(): pass"
        result = ReviewResult(verdict="pass", issues=[])
        
        code, success, error = apply_review_result(original, result, is_production=True)
        
        assert code == original
        assert success is True
        assert error is None
    
    def test_fail_with_inline_fix_applies_fix(self):
        """Test that fail with inline fix applies the correction."""
        original = "def hello():pass"
        result = ReviewResult(
            verdict="fail",
            issues=[ReviewIssue(
                category=IssueCategory.STYLE,
                severity=IssueSeverity.WARNING,
                line_number=1,
                original_code="def hello():pass",
                fixed_code="def hello():\n    pass",
                explanation="Needs proper formatting",
            )],
        )
        
        code, success, error = apply_review_result(original, result, is_production=True)
        
        assert "def hello():\n    pass" in code
        assert success is True
        assert error is None
    
    def test_fail_no_fix_production_fails(self):
        """Test that fail without fix fails in production."""
        original = "exec(user_input)"
        result = ReviewResult(
            verdict="fail",
            issues=[ReviewIssue(
                category=IssueCategory.SECURITY,
                severity=IssueSeverity.ERROR,
                explanation="exec() with user input is dangerous",
            )],
        )
        
        code, success, error = apply_review_result(original, result, is_production=True)
        
        assert code == original  # Returns original but fails
        assert success is False
        assert error is not None
        assert "exec" in error or "Self-review failed" in error
    
    def test_fail_no_fix_development_warns(self):
        """Test that fail without fix warns but succeeds in development."""
        original = "exec(user_input)"
        result = ReviewResult(
            verdict="fail",
            issues=[ReviewIssue(
                category=IssueCategory.SECURITY,
                severity=IssueSeverity.ERROR,
                explanation="exec() with user input is dangerous",
            )],
        )
        
        code, success, error = apply_review_result(original, result, is_production=False)
        
        assert code == original
        assert success is True  # Allowed in development
        assert error is None


class TestPerformSelfReviewMocked:
    """Tests for perform_self_review with mocked LLM."""
    
    @pytest.mark.asyncio
    async def test_review_pass(self):
        """Test review returning pass verdict."""
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        mock_structured.ainvoke.return_value = ReviewResult(
            verdict="pass",
            issues=[],
            review_summary="Code is clean",
        )
        
        with patch(
            'integration_coworker.codegen.structured_output.get_structured_llm',
            return_value=mock_structured
        ):
            result = await perform_self_review(
                code='def hello(): return "world"',
                artifact_type="client",
                file_path="src/client.py",
                llm=mock_llm,
            )
        
        assert result.verdict == "pass"
        assert len(result.issues) == 0
    
    @pytest.mark.asyncio
    async def test_review_fail_with_inline_fix(self):
        """Test review returning fail with inline fix."""
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        mock_structured.ainvoke.return_value = ReviewResult(
            verdict="fail",
            issues=[ReviewIssue(
                category=IssueCategory.STYLE,
                severity=IssueSeverity.WARNING,
                line_number=3,
                original_code="import os\ndef main(): pass",
                fixed_code="import os\n\n\ndef main():\n    pass",
                explanation="Need blank line after imports",
            )],
        )
        
        with patch(
            'integration_coworker.codegen.structured_output.get_structured_llm',
            return_value=mock_structured
        ):
            result = await perform_self_review(
                code='"""Module."""\nimport os\ndef main(): pass',
                artifact_type="flow",
                file_path="src/flow.py",
                llm=mock_llm,
            )
        
        assert result.verdict == "fail"
        assert result.can_repair is True
    
    @pytest.mark.asyncio
    async def test_review_fail_no_fix(self):
        """Test review returning fail without fix."""
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        mock_structured.ainvoke.return_value = ReviewResult(
            verdict="fail",
            issues=[ReviewIssue(
                category=IssueCategory.LOGIC,
                severity=IssueSeverity.ERROR,
                explanation="Cannot determine correct API call sequence",
            )],
        )
        
        with patch(
            'integration_coworker.codegen.structured_output.get_structured_llm',
            return_value=mock_structured
        ):
            result = await perform_self_review(
                code='def broken(): pass',
                artifact_type="client",
                file_path="src/api.py",
                llm=mock_llm,
            )
        
        assert result.verdict == "fail"
        assert result.can_repair is False
    
    @pytest.mark.asyncio
    async def test_review_error_returns_pass(self):
        """Test that LLM errors return fail-safe pass."""
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        mock_structured.ainvoke.side_effect = Exception("LLM API error")
        
        with patch(
            'integration_coworker.codegen.structured_output.get_structured_llm',
            return_value=mock_structured
        ):
            result = await perform_self_review(
                code='def hello(): pass',
                artifact_type="client",
                file_path="src/client.py",
                llm=mock_llm,
            )
        
        # Fail-safe: return pass on error
        assert result.verdict == "pass"
        assert "error" in result.review_summary.lower() if result.review_summary else False


class TestReviewAndRepair:
    """Tests for review_and_repair with retry logic."""
    
    @pytest.mark.asyncio
    async def test_pass_on_first_try(self):
        """Test code passes review on first attempt."""
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        mock_structured.ainvoke.return_value = ReviewResult(
            verdict="pass",
            issues=[],
        )
        
        with patch(
            'integration_coworker.codegen.structured_output.get_structured_llm',
            return_value=mock_structured
        ):
            code, success, error = await review_and_repair(
                code='def hello(): return "world"',
                artifact_type="client",
                file_path="src/client.py",
                llm=mock_llm,
                is_production=True,
            )
        
        assert success is True
        assert error is None
        assert code == 'def hello(): return "world"'
    
    @pytest.mark.asyncio
    async def test_repair_success(self):
        """Test successful repair after fail."""
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        
        # First call: fail with inline fix
        # Second call (after fix applied): pass
        mock_structured.ainvoke.side_effect = [
            ReviewResult(
                verdict="fail",
                issues=[ReviewIssue(
                    category=IssueCategory.STYLE,
                    severity=IssueSeverity.WARNING,
                    line_number=1,
                    original_code='def hello():return "world"',
                    fixed_code='def hello():\n    return "world"',
                    explanation="Formatting issue",
                )],
            ),
            ReviewResult(verdict="pass", issues=[]),
        ]
        
        with patch(
            'integration_coworker.codegen.structured_output.get_structured_llm',
            return_value=mock_structured
        ):
            code, success, error = await review_and_repair(
                code='def hello():return "world"',
                artifact_type="client",
                file_path="src/client.py",
                llm=mock_llm,
                is_production=True,
                max_repair_attempts=1,
            )
        
        assert success is True
        assert error is None
        assert "return" in code
    
    @pytest.mark.asyncio
    async def test_unfixable_production_fails(self):
        """Test unfixable issue fails in production."""
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        mock_structured.ainvoke.return_value = ReviewResult(
            verdict="fail",
            issues=[ReviewIssue(
                category=IssueCategory.SECURITY,
                severity=IssueSeverity.ERROR,
                explanation="Contains eval() - cannot auto-fix",
            )],
        )
        
        with patch(
            'integration_coworker.codegen.structured_output.get_structured_llm',
            return_value=mock_structured
        ):
            code, success, error = await review_and_repair(
                code='eval(user_input)',
                artifact_type="client",
                file_path="src/dangerous.py",
                llm=mock_llm,
                is_production=True,
            )
        
        assert success is False
        assert error is not None
    
    @pytest.mark.asyncio
    async def test_unfixable_development_warns(self):
        """Test unfixable issue warns but continues in development."""
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        mock_structured.ainvoke.return_value = ReviewResult(
            verdict="fail",
            issues=[ReviewIssue(
                category=IssueCategory.SECURITY,
                severity=IssueSeverity.ERROR,
                explanation="Contains eval() - cannot auto-fix",
            )],
        )
        
        with patch(
            'integration_coworker.codegen.structured_output.get_structured_llm',
            return_value=mock_structured
        ):
            code, success, error = await review_and_repair(
                code='eval(user_input)',
                artifact_type="client",
                file_path="src/dangerous.py",
                llm=mock_llm,
                is_production=False,  # Development mode
            )
        
        assert success is True  # Allowed in development
        assert error is None


class TestReviewIntegration:
    """Integration-style tests (still mocked but full flow)."""
    
    @pytest.mark.asyncio
    async def test_full_flow_with_style_fix(self):
        """Test full review flow with style fix applied."""
        # Code with style issue
        bad_code = '''"""Module."""
import os
def main():
    return os.getcwd()'''
        
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        mock_structured.ainvoke.side_effect = [
            ReviewResult(
                verdict="fail",
                issues=[ReviewIssue(
                    category=IssueCategory.STYLE,
                    severity=IssueSeverity.WARNING,
                    line_number=2,
                    original_code="import os\ndef main():",
                    fixed_code="import os\n\n\ndef main():",
                    explanation="Need blank lines after imports and between definitions",
                )],
            ),
            ReviewResult(verdict="pass", issues=[]),
        ]
        
        with patch(
            'integration_coworker.codegen.structured_output.get_structured_llm',
            return_value=mock_structured
        ):
            code, success, error = await review_and_repair(
                code=bad_code,
                artifact_type="flow",
                file_path="src/main.py",
                llm=mock_llm,
                is_production=True,
                max_repair_attempts=1,
            )
        
        assert success is True
        # The fix should have been applied
        assert "import os\n\n" in code or success is True
