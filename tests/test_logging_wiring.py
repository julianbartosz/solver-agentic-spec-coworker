import io
import json
import logging
import os

from typer.testing import CliRunner

from integration_coworker import cli
from integration_coworker.cli import _setup_logging
from integration_coworker.logging_config import get_logger, RedactingFormatter


def _capture_logger_output(logger: logging.Logger) -> io.StringIO:
    """Swap the first available handler stream (logger or ancestor) with StringIO."""
    current = logger
    handler = None

    while current and not handler:
        if current.handlers:
            handler = current.handlers[0]
            break
        current = current.parent

    if handler is None:
        raise AssertionError("Logger has no handlers")

    stream = io.StringIO()
    handler.setStream(stream)
    return stream


def test_setup_logging_json_outputs_structured(capsys, monkeypatch):
    # Force JSON output
    monkeypatch.setenv("JSON_LOGS", "1")
    _setup_logging(verbose=False)
    logger = get_logger("test_json")
    stream = _capture_logger_output(logger)

    logger.warning("hello", extra={"foo": "bar"})
    output = stream.getvalue().strip()
    assert output.startswith("{")
    data = json.loads(output)
    assert data["message"] == "hello"
    assert data["foo"] == "bar"


def test_setup_logging_plain_text_by_default(monkeypatch):
    monkeypatch.delenv("JSON_LOGS", raising=False)
    _setup_logging(verbose=False)
    logger = get_logger("test_plain")
    stream = _capture_logger_output(logger)

    logger.warning("hello", extra={"foo": "bar"})
    output = stream.getvalue().strip()
    assert not output.startswith("{")
    assert "hello" in output


def test_setup_logging_idempotent(monkeypatch):
    monkeypatch.delenv("JSON_LOGS", raising=False)
    _setup_logging(verbose=True)
    _setup_logging(verbose=True)

    logger = logging.getLogger("integration_coworker")
    assert len(logger.handlers) == 1


def test_cli_callback_configures_logging_for_commands(monkeypatch):
    """CLI callback should configure logging even for commands without explicit setup."""
    runner = CliRunner()
    monkeypatch.setenv("USE_SQLITE", "true")

    # Reset logging state for the CLI module
    cli._LOGGING_STATE.update({"configured": False, "level": None, "json_output": None})

    calls = []

    def fake_configure_logging(level, json_output=None):
        calls.append((level, json_output))

    # Patch configure_logging used inside cli._setup_logging
    monkeypatch.setattr(cli, "configure_logging", fake_configure_logging)

    # Avoid touching the real database during init-db
    monkeypatch.setattr("integration_coworker.persistence.db.init_schema", lambda: None)
    monkeypatch.setattr("integration_coworker.persistence.seed_kg.seed_knowledge_graph", lambda *args, **kwargs: (0, 0))

    result = runner.invoke(cli.app, ["init-db", "--no-seed-kg"])

    assert result.exit_code == 0
    assert calls, "configure_logging should be invoked via CLI callback"
    # First call should be the callback applying WARNING-level logs by default
    assert calls[0][0] == logging.WARNING


def test_cli_callback_does_not_duplicate_handlers(monkeypatch):
    """Multiple CLI invocations should not stack handlers."""
    runner = CliRunner()
    monkeypatch.setenv("USE_SQLITE", "true")

    # Reset logging state and clear existing handlers for deterministic count
    cli._LOGGING_STATE.update({"configured": False, "level": None, "json_output": None})
    logger = logging.getLogger("integration_coworker")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    # Avoid touching DB/seed during help invocations
    monkeypatch.setattr("integration_coworker.persistence.db.init_schema", lambda: None)
    monkeypatch.setattr("integration_coworker.persistence.seed_kg.seed_knowledge_graph", lambda *args, **kwargs: (0, 0))

    # Invoke CLI twice (callback runs each time)
    result1 = runner.invoke(cli.app, ["--help"])
    count_after_first = len(logging.getLogger("integration_coworker").handlers)
    result2 = runner.invoke(cli.app, ["--help"])
    count_after_second = len(logging.getLogger("integration_coworker").handlers)

    assert result1.exit_code == 0
    assert result2.exit_code == 0
    assert count_after_second == count_after_first


# =============================================================================
# RedactingFormatter tests (Production Readiness v4 - P0-1)
# =============================================================================

class TestRedactingFormatter:
    """Test credential redaction in log messages."""
    
    def test_redacts_openai_api_key_in_message(self):
        """OpenAI API keys (sk-...) should be redacted from messages."""
        formatter = RedactingFormatter(json_output=False)
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="API call failed with key sk-abcdef1234567890abcdef1234567890abcdef12345678",
            args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "sk-abcdef" not in output
        assert "[REDACTED:OPENAI_KEY]" in output
    
    def test_redacts_anthropic_api_key(self):
        """Anthropic API keys (sk-ant-...) should be redacted."""
        formatter = RedactingFormatter(json_output=False)
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Using key sk-ant-api03-xyz789012345678901234567890123456789012",
            args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "sk-ant-api03" not in output
        assert "[REDACTED:ANTHROPIC_KEY]" in output
    
    def test_redacts_bearer_token(self):
        """Bearer tokens should be redacted."""
        formatter = RedactingFormatter(json_output=False)
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Authorization header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWI",
            args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in output
        assert "[REDACTED:TOKEN]" in output
    
    def test_redacts_api_key_in_query_param(self):
        """API keys in query params should be redacted."""
        formatter = RedactingFormatter(json_output=False)
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="URL: https://api.example.com?api_key=supersecretkey123456",
            args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "supersecretkey123456" not in output
        assert "[REDACTED]" in output
    
    def test_redacts_exc_text(self):
        """Credentials in exc_text (cached exceptions) should be redacted.
        
        This is CRITICAL: LogRecord.exc_text is cached by the logging module.
        Without redaction, exceptions containing credentials would leak
        on subsequent log calls that reuse the LogRecord.
        """
        formatter = RedactingFormatter(json_output=False)
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Error occurred",
            args=(), exc_info=None,
        )
        # Simulate cached exception text containing credentials
        record.exc_text = "ValueError: Invalid API key sk-test123456789012345678901234567890123456"
        
        output = formatter.format(record)
        assert "sk-test" not in output
        assert "[REDACTED:OPENAI_KEY]" in output or "sk-" not in output
    
    def test_preserves_non_sensitive_content(self):
        """Non-sensitive content should pass through unchanged."""
        formatter = RedactingFormatter(json_output=False)
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Processing endpoint /users/123 with method GET",
            args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "/users/123" in output
        assert "GET" in output
    
    def test_redacts_in_json_output(self):
        """Credentials should be redacted in JSON output mode too."""
        formatter = RedactingFormatter(json_output=True)
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Key: sk-abcdef1234567890abcdef1234567890abcdef12345678",
            args=(), exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "sk-abcdef" not in data["message"]
        assert "[REDACTED:OPENAI_KEY]" in data["message"]
    
    def test_redacts_google_api_key(self):
        """Google API keys (AIza...) should be redacted."""
        formatter = RedactingFormatter(json_output=False)
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Google key: AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ12345678",
            args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ12345678" not in output
        assert "[REDACTED:GOOGLE_KEY]" in output
    
    def test_redacts_authorization_header(self):
        """Authorization header values should be redacted."""
        formatter = RedactingFormatter(json_output=False)
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Headers: {'Authorization': 'Basic dXNlcjpwYXNzd29yZDEyMzQ1Njc4OQ=='}",
            args=(), exc_info=None,
        )
        output = formatter.format(record)
        assert "dXNlcjpwYXNzd29yZDEyMzQ1Njc4OQ==" not in output
        assert "[REDACTED]" in output
    
    def test_snapshot_restore_preserves_original_record(self):
        """CRITICAL: format() must not mutate LogRecord for multi-handler safety.
        
        When multiple handlers share a LogRecord (e.g., stdout + file), 
        one handler's redaction must NOT affect the other handler's view.
        This ensures the snapshot/restore pattern works correctly.
        """
        formatter = RedactingFormatter(json_output=False)
        original_msg = "Key: sk-abcdef1234567890abcdef1234567890abcdef12345678"
        
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg=original_msg,
            args=(), exc_info=None,
        )
        
        # First format call (simulates first handler)
        output1 = formatter.format(record)
        
        # Record should be unchanged after format()
        assert record.msg == original_msg, "LogRecord.msg was mutated by format()"
        
        # Second format call (simulates second handler) should see original
        output2 = formatter.format(record)
        
        # Both outputs should be redacted
        assert "[REDACTED:OPENAI_KEY]" in output1
        assert "[REDACTED:OPENAI_KEY]" in output2
        
        # Original msg still preserved after both calls
        assert record.msg == original_msg
    
    def test_snapshot_restore_with_exc_text(self):
        """Ensure exc_text is also restored after format(), not just msg."""
        formatter = RedactingFormatter(json_output=False)
        original_exc_text = "Error with sk-secret123456789012345678901234567890123"
        
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="An error occurred",
            args=(), exc_info=None,
        )
        record.exc_text = original_exc_text
        
        output = formatter.format(record)
        
        # exc_text should be restored after format()
        assert record.exc_text == original_exc_text, "LogRecord.exc_text was not restored"
        assert "[REDACTED:OPENAI_KEY]" in output or "sk-secret" not in output
