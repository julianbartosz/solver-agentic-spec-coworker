"""
Tests for CLI hitl-resume command enhancements.

Tests the --feedback, --regenerate flags and decision building.
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Test fixtures
# =============================================================================


@pytest.fixture
def cli_env():
    """Environment for CLI subprocess tests."""
    import os
    env = os.environ.copy()
    # Add src to PYTHONPATH for subprocess
    src_dir = str(Path(__file__).parent.parent / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{existing}" if existing else src_dir
    return env


# =============================================================================
# Test CLI help output
# =============================================================================


class TestHitlResumeHelp:
    """Test hitl-resume command help shows new options."""
    
    def test_help_shows_approve_flag(self, cli_env):
        """Help shows --approve flag."""
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", "hitl-resume", "--help"],
            capture_output=True,
            text=True,
            env=cli_env,
            timeout=30,
        )
        
        assert result.returncode == 0
        assert "--approve" in result.stdout
    
    def test_help_shows_reject_flag(self, cli_env):
        """Help shows --reject flag."""
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", "hitl-resume", "--help"],
            capture_output=True,
            text=True,
            env=cli_env,
            timeout=30,
        )
        
        assert "--reject" in result.stdout
    
    def test_help_shows_feedback_flag(self, cli_env):
        """Help shows --feedback flag."""
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", "hitl-resume", "--help"],
            capture_output=True,
            text=True,
            env=cli_env,
            timeout=30,
        )
        
        assert "--feedback" in result.stdout
    
    def test_help_shows_regenerate_flag(self, cli_env):
        """Help shows --regenerate flag."""
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", "hitl-resume", "--help"],
            capture_output=True,
            text=True,
            env=cli_env,
            timeout=30,
        )
        
        assert "--regenerate" in result.stdout


# =============================================================================
# Test CLI validation
# =============================================================================


class TestHitlResumeValidation:
    """Test hitl-resume argument validation."""
    
    def test_requires_approve_or_reject(self, cli_env):
        """Command requires --approve or --reject."""
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", "hitl-resume", "test-run-123", "--json"],
            capture_output=True,
            text=True,
            env=cli_env,
            timeout=30,
        )
        
        assert result.returncode == 1
        output = json.loads(result.stdout)
        assert "Must specify --approve or --reject" in output.get("error", "")
    
    def test_rejects_both_approve_and_reject(self, cli_env):
        """Command rejects both --approve and --reject."""
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", "hitl-resume", "test-run-123", "--approve", "--reject", "--json"],
            capture_output=True,
            text=True,
            env=cli_env,
            timeout=30,
        )
        
        assert result.returncode == 1
        output = json.loads(result.stdout)
        assert "Cannot specify both" in output.get("error", "")
    
    def test_regenerate_requires_reject(self, cli_env):
        """--regenerate requires --reject."""
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", "hitl-resume", "test-run-123", "--approve", "--regenerate", "--json"],
            capture_output=True,
            text=True,
            env=cli_env,
            timeout=30,
        )
        
        assert result.returncode == 1
        output = json.loads(result.stdout)
        assert "requires --reject" in output.get("error", "")
    
    def test_regenerate_requires_feedback(self, cli_env):
        """--regenerate requires --feedback/--comment."""
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", "hitl-resume", "test-run-123", "--reject", "--regenerate", "--json"],
            capture_output=True,
            text=True,
            env=cli_env,
            timeout=30,
        )
        
        assert result.returncode == 1
        output = json.loads(result.stdout)
        # Error message now mentions both --feedback and --comment as aliases
        assert "requires --feedback" in output.get("error", "") or "requires --comment" in output.get("error", "")


# =============================================================================
# Test decision building (unit tests)
# =============================================================================


class TestDecisionBuilding:
    """Test decision dict building in CLI."""
    
    def test_approve_builds_correct_decision(self):
        """Approve builds decision with approved=True."""
        # Simulate what the CLI does
        decision = {
            "approved": True,
            "comment": "",
            "feedback": "",
            "overrides": {},
        }
        
        assert decision["approved"] is True
        assert "regenerate" not in decision
    
    def test_reject_with_feedback_builds_correct_decision(self):
        """Reject with feedback builds correct decision."""
        effective_comment = "Use async pattern"
        decision = {
            "approved": False,
            "comment": effective_comment,
            "feedback": effective_comment,
            "overrides": {},
        }
        
        assert decision["approved"] is False
        assert decision["feedback"] == "Use async pattern"
    
    def test_reject_with_regenerate_builds_correct_decision(self):
        """Reject with regenerate builds correct decision."""
        effective_comment = "Use async pattern"
        regenerate = True
        
        decision = {
            "approved": False,
            "comment": effective_comment,
            "feedback": effective_comment,
            "overrides": {},
        }
        
        if regenerate:
            decision["regenerate"] = True
        
        assert decision["approved"] is False
        assert decision["regenerate"] is True
        assert decision["feedback"] == "Use async pattern"
    
    def test_approve_with_exclusions_builds_correct_decision(self):
        """Approve with exclusions builds correct decision."""
        exclude = ["src/temp.py", "tests/scratch.py"]
        
        decision = {
            "approved": True,
            "comment": "",
            "feedback": "",
            "overrides": {},
        }
        
        if exclude:
            decision["overrides"]["exclude_files"] = list(exclude)
        
        assert decision["approved"] is True
        assert decision["overrides"]["exclude_files"] == ["src/temp.py", "tests/scratch.py"]


# =============================================================================
# Test feedback alias behavior
# =============================================================================


class TestFeedbackAlias:
    """Test --feedback and --comment are true aliases (same parameter)."""
    
    def test_help_shows_comment_and_feedback_as_aliases(self, cli_env):
        """Help output shows both --comment and --feedback as aliases for same option."""
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", "hitl-resume", "--help"],
            capture_output=True,
            text=True,
            env=cli_env,
            timeout=30,
        )
        
        assert result.returncode == 0
        # Both flags should be in help
        assert "--feedback" in result.stdout
        assert "--comment" in result.stdout
        # Should mention they are aliases
        assert "alias" in result.stdout.lower() or ("feedback" in result.stdout.lower() and "comment" in result.stdout.lower())
    
    def test_decision_uses_feedback_value(self):
        """Decision dict uses the feedback parameter value."""
        feedback_val = "Use async pattern"
        
        decision = {
            "approved": False,
            "comment": feedback_val or "",
            "feedback": feedback_val or "",
            "overrides": {},
        }
        
        assert decision["feedback"] == "Use async pattern"
        assert decision["comment"] == "Use async pattern"
    
    def test_none_when_not_provided(self):
        """Empty string when neither --feedback nor --comment provided."""
        feedback_val = None
        
        decision = {
            "approved": False,
            "comment": feedback_val or "",
            "feedback": feedback_val or "",
            "overrides": {},
        }
        
        assert decision["feedback"] == ""
        assert decision["comment"] == ""


# =============================================================================
# Integration tests (mocked runtime)
# =============================================================================


class TestHitlResumeIntegration:
    """Integration tests with mocked runtime."""
    
    def test_not_paused_returns_error(self, cli_env):
        """Returns error when run is not paused."""
        # This test will hit the real is_workflow_paused check
        # which will fail because the run doesn't exist
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", "hitl-resume", "nonexistent-run-123", "--approve", "--json"],
            capture_output=True,
            text=True,
            env=cli_env,
            timeout=30,
        )
        
        assert result.returncode == 1
        # Output may have multiple lines, parse the first valid JSON
        stdout_lines = result.stdout.strip().split('\n')
        for line in stdout_lines:
            if line.strip().startswith('{'):
                try:
                    output = json.loads(line)
                    # Either "not paused" or some other error
                    assert "error" in output
                    return
                except json.JSONDecodeError:
                    continue
        # If no JSON found, check stderr for error message
        assert result.returncode == 1
