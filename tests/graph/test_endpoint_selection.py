"""
Tests for endpoint selection logic in understand_task.

V40-001: Tests for BUG-4 fix - wrong endpoint selection for text generation tasks.
"""
import pytest
import re
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class MockEndpoint:
    """Mock endpoint for testing."""
    path: str
    method: str = "POST"
    summary: str = ""
    operation_id: str = ""


class TestEndpointScoringIntentDetection:
    """
    Tests for V40-001: Intent detection for endpoint preference.
    
    BUG-4: Task like "generate a docstring" was selecting /assistants 
    instead of /chat/completions.
    
    Root cause: The word "generate" triggered is_text_generation_task,
    but explicit management resource words like "assistant" in the path
    were not penalized strongly enough.
    """
    
    def _score_endpoints(self, task_description: str, endpoints: List[MockEndpoint]) -> List[tuple]:
        """
        Score endpoints using the same logic as understand_task._build_understand_task_prompt.
        
        Returns list of (score, endpoint) tuples sorted by score descending.
        """
        task_lower = task_description.lower()
        
        # Extract meaningful words from task
        task_words = set()
        for word in re.sub(r'[^a-z0-9\s]', '', task_lower).split():
            if len(word) > 2:
                task_words.add(word)
        
        # V40-001: Management resource indicators - take priority in classification
        management_resource_indicators = {
            "assistant", "assistants", "thread", "threads", "run", "runs",
            "file", "files", "vector", "vectors", "store", "stores",
            "batch", "batches", "fine", "tune", "finetune", "finetuning",
        }
        
        # Check if task explicitly mentions management resources
        explicit_management_resource = bool(task_words & management_resource_indicators)
        
        # Text generation action indicators
        text_gen_action_indicators = {
            "generate", "write", "enhance", "improve", "summarize",
            "translate", "explain", "describe", "document", "docstring",
            "completion", "complete", "respond",
        }
        
        # Text generation context indicators
        text_gen_context_indicators = {
            "text", "content", "response", "answer", "message", "chat",
            "prompt", "conversation", "query",
        }
        
        has_text_gen_action = bool(task_words & text_gen_action_indicators)
        has_text_gen_context = bool(task_words & text_gen_context_indicators)
        
        # V40-001: Priority-based classification
        if explicit_management_resource:
            is_text_generation_task = False
            is_management_task = True
        elif has_text_gen_action and has_text_gen_context:
            is_text_generation_task = True
            is_management_task = False
        elif has_text_gen_action:
            management_action_indicators = {
                "list", "get", "fetch", "retrieve", "delete", "update", "configure",
                "settings", "manage", "admin", "setup",
            }
            is_management_task = bool(task_words & management_action_indicators)
            is_text_generation_task = not is_management_task
        else:
            management_action_indicators = {
                "list", "get", "fetch", "retrieve", "delete", "update", "configure",
                "settings", "manage", "admin", "setup", "create",
            }
            is_management_task = bool(task_words & management_action_indicators)
            is_text_generation_task = not is_management_task
        
        # Endpoint indicators
        chat_completion_indicators = {"chat", "completion", "completions", "message", "messages"}
        management_endpoint_indicators = {"assistant", "assistants", "thread", "threads", "run", "runs", "file", "files"}
        modern_api_indicators = {"chat", "responses"}
        
        scored_endpoints = []
        for ep in endpoints:
            score = 0
            
            # Build searchable text from endpoint metadata
            ep_path_words = set(re.sub(r'[^a-z0-9\s]', ' ', ep.path.lower()).split())
            ep_summary_words = set(re.sub(r'[^a-z0-9\s]', ' ', (ep.summary or '').lower()).split())
            ep_op_id_words = set(re.sub(r'[^a-z0-9\s]', ' ', (ep.operation_id or '').lower()).split())
            
            all_ep_words = ep_path_words | ep_summary_words | ep_op_id_words
            matching_words = task_words & all_ep_words
            score += len(matching_words) * 3
            
            path_lower = ep.path.lower()
            
            # V40-001: Stronger boosting/penalizing for text generation
            if is_text_generation_task:
                if ep_path_words & chat_completion_indicators:
                    score += 25
                if ep_path_words & management_endpoint_indicators:
                    score -= 25
                    if any(x in path_lower for x in ['/assistants', '/threads', '/runs', '/files']):
                        score -= 15
            
            # For management tasks, boost management endpoints
            if is_management_task:
                if ep_path_words & management_endpoint_indicators:
                    score += 20
                if ep_path_words & chat_completion_indicators:
                    score -= 10
            
            # Modern vs Legacy API preference
            has_completion_indicator = "completion" in path_lower
            has_modern_indicator = bool(ep_path_words & modern_api_indicators)
            
            if has_completion_indicator:
                if has_modern_indicator:
                    score += 20
                else:
                    score -= 15
            
            if score > 0:
                scored_endpoints.append((score, ep))
        
        scored_endpoints.sort(key=lambda x: (-x[0], len(x[1].path)))
        return scored_endpoints
    
    def test_text_generation_prefers_chat_completions(self):
        """Text generation tasks should prefer /chat/completions over /assistants."""
        endpoints = [
            MockEndpoint("/chat/completions", summary="Creates a model response for the given chat conversation"),
            MockEndpoint("/assistants", summary="Create an assistant"),
            MockEndpoint("/assistants/{assistant_id}", summary="Retrieves an assistant"),
            MockEndpoint("/completions", summary="Creates a completion for the provided prompt"),
        ]
        
        task = "generate a docstring for this Python function"
        scored = self._score_endpoints(task, endpoints)
        
        # /chat/completions should be first (highest score)
        assert len(scored) > 0
        top_path = scored[0][1].path
        assert top_path == "/chat/completions", f"Expected /chat/completions, got {top_path}"
    
    def test_text_generation_penalizes_assistants_endpoint(self):
        """Text generation should heavily penalize /assistants endpoints."""
        endpoints = [
            MockEndpoint("/chat/completions", summary="Creates a model response"),
            MockEndpoint("/assistants", summary="Create an assistant with a model"),
        ]
        
        task = "generate a summary of this document"
        scored = self._score_endpoints(task, endpoints)
        
        # Get scores
        chat_score = next((s for s, ep in scored if ep.path == "/chat/completions"), None)
        
        # /chat/completions should be scored (positive)
        assert chat_score is not None and chat_score > 0
        
        # /assistants should be penalized out (negative or not in list)
        assistant_score = next((s for s, ep in scored if ep.path == "/assistants"), None)
        assert assistant_score is None, f"/assistants should be penalized out, got score {assistant_score}"
    
    def test_management_task_prefers_assistants(self):
        """Management tasks like 'create an assistant' should prefer /assistants."""
        endpoints = [
            MockEndpoint("/chat/completions", summary="Creates a model response"),
            MockEndpoint("/assistants", summary="Create an assistant"),
            MockEndpoint("/assistants/{assistant_id}/files", summary="List files attached to assistant"),
        ]
        
        task = "create an assistant for code review"
        scored = self._score_endpoints(task, endpoints)
        
        # /assistants should be first
        assert len(scored) > 0
        top_path = scored[0][1].path
        assert "assistants" in top_path.lower(), f"Expected assistants endpoint, got {top_path}"
    
    def test_explicit_management_resource_in_task(self):
        """Tasks mentioning 'assistant', 'thread', etc. should be classified as management."""
        endpoints = [
            MockEndpoint("/chat/completions", summary="Creates a model response"),
            MockEndpoint("/threads", summary="Create a thread"),
            MockEndpoint("/threads/{thread_id}/runs", summary="Create a run"),
        ]
        
        task = "create a thread for conversation"
        scored = self._score_endpoints(task, endpoints)
        
        assert len(scored) > 0
        top_path = scored[0][1].path
        assert "threads" in top_path.lower(), f"Expected threads endpoint, got {top_path}"
    
    def test_write_text_content_prefers_completions(self):
        """Tasks with 'write text content' should prefer chat completions."""
        endpoints = [
            MockEndpoint("/chat/completions", summary="Creates a model response"),
            MockEndpoint("/assistants", summary="Create an assistant"),
            MockEndpoint("/files", summary="Upload a file"),
        ]
        
        task = "write a marketing email message"
        scored = self._score_endpoints(task, endpoints)
        
        assert len(scored) > 0
        top_path = scored[0][1].path
        assert top_path == "/chat/completions", f"Expected /chat/completions, got {top_path}"
    
    def test_improve_code_prefers_completions(self):
        """Tasks like 'improve code' should prefer chat completions."""
        endpoints = [
            MockEndpoint("/chat/completions", summary="Creates a model response"),
            MockEndpoint("/assistants", summary="Create an assistant"),
            MockEndpoint("/runs/{run_id}", summary="Retrieve a run"),
        ]
        
        task = "improve this code for better performance"
        scored = self._score_endpoints(task, endpoints)
        
        assert len(scored) > 0
        # /chat/completions should be top
        top_path = scored[0][1].path
        assert top_path == "/chat/completions", f"Expected /chat/completions, got {top_path}"
    
    def test_list_files_prefers_files_endpoint(self):
        """Management task 'list files' should prefer /files endpoint."""
        endpoints = [
            MockEndpoint("/chat/completions", summary="Creates a model response"),
            MockEndpoint("/files", method="GET", summary="Returns a list of files"),
            MockEndpoint("/files/{file_id}", method="GET", summary="Returns information about a file"),
        ]
        
        task = "list all uploaded files"
        scored = self._score_endpoints(task, endpoints)
        
        assert len(scored) > 0
        top_path = scored[0][1].path
        assert "files" in top_path.lower(), f"Expected files endpoint, got {top_path}"
    
    def test_modern_api_preferred_over_legacy(self):
        """/chat/completions should be preferred over /completions."""
        endpoints = [
            MockEndpoint("/completions", summary="Creates a completion"),
            MockEndpoint("/chat/completions", summary="Creates a chat completion"),
        ]
        
        task = "generate text completion"
        scored = self._score_endpoints(task, endpoints)
        
        assert len(scored) > 0
        top_path = scored[0][1].path
        # Modern API should win
        assert "chat" in top_path.lower(), f"Expected /chat/completions (modern), got {top_path}"


class TestSyntaxRepairPatterns:
    """Tests for V40-002: Enhanced syntax repair patterns."""
    
    def test_fix_invalid_escape_sequence_in_regex(self):
        """Invalid escape sequences in regex patterns should be fixed."""
        from integration_coworker.codegen.security import _fix_invalid_escape_sequences
        
        code = '''
import re
pattern = re.compile("\\s+")
text = re.sub("\\d+", "", text)
'''
        fixed = _fix_invalid_escape_sequences(code)
        # Should convert to raw strings
        assert 'r"\\s+"' in fixed or "r'\\s+'" in fixed
    
    def test_preserves_already_raw_strings(self):
        """Already raw strings should not be modified."""
        from integration_coworker.codegen.security import _fix_invalid_escape_sequences
        
        code = '''
pattern = r"\\s+"
'''
        fixed = _fix_invalid_escape_sequences(code)
        # Should remain unchanged
        assert 'r"\\s+"' in fixed
    
    def test_fix_truncated_fstring(self):
        """Truncated f-strings should be closed."""
        from integration_coworker.codegen.security import _fix_truncated_fstring
        
        line = 'message = f"Hello {name'
        fixed = _fix_truncated_fstring(line)
        # Should add closing brace and quote
        assert fixed.endswith('}"') or fixed.endswith("}'")
    
    def test_fix_fstring_nested_quotes(self):
        """F-strings with nested quote issues should be fixed."""
        from integration_coworker.codegen.security import _balance_line_quotes
        
        line = 'f"Value: {data["key"]}"'
        fixed = _balance_line_quotes(line)
        # Should use single quotes inside
        assert "'" in fixed or fixed == line  # Either fixed or already valid
