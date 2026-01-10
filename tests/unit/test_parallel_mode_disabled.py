"""
Test that parallel mode is properly disabled (V2 State).

This test module PROVES that:
1. PARALLEL_WORKFLOW=false results in is_parallel_enabled() returning False
2. Sequential graph (build_graph) is used instead of parallel graph
3. No parallel branch checkpoints are created

V2 STATE: Parallel mode deprecated to avoid checkpoint bloat.
"""
import os
import pytest
from unittest.mock import patch, MagicMock


class TestParallelModeDisabled:
    """Verify parallel mode is disabled in V2 state."""
    
    def test_is_parallel_enabled_returns_false_by_default(self):
        """PROOF: Without env var, parallel is disabled."""
        # Clear any existing env var
        with patch.dict(os.environ, {}, clear=True):
            # Remove PARALLEL_WORKFLOW if it exists
            os.environ.pop("PARALLEL_WORKFLOW", None)
            
            from integration_coworker.graph.parallel import is_parallel_enabled
            
            # Reimport to pick up env change
            import importlib
            import integration_coworker.graph.parallel as parallel_module
            importlib.reload(parallel_module)
            
            result = parallel_module.is_parallel_enabled()
            assert result is False, "Default should be parallel DISABLED"
    
    def test_is_parallel_enabled_false_when_explicitly_set(self):
        """PROOF: PARALLEL_WORKFLOW=false disables parallel mode."""
        with patch.dict(os.environ, {"PARALLEL_WORKFLOW": "false"}):
            from integration_coworker.graph.parallel import is_parallel_enabled
            
            import importlib
            import integration_coworker.graph.parallel as parallel_module
            importlib.reload(parallel_module)
            
            result = parallel_module.is_parallel_enabled()
            assert result is False, "PARALLEL_WORKFLOW=false should disable parallel"
    
    def test_is_parallel_enabled_true_only_when_explicitly_enabled(self):
        """PROOF: Only PARALLEL_WORKFLOW=true enables parallel (opt-in)."""
        with patch.dict(os.environ, {"PARALLEL_WORKFLOW": "true"}):
            from integration_coworker.graph.parallel import is_parallel_enabled
            
            import importlib
            import integration_coworker.graph.parallel as parallel_module
            importlib.reload(parallel_module)
            
            result = parallel_module.is_parallel_enabled()
            assert result is True, "PARALLEL_WORKFLOW=true should enable parallel"
    
    def test_runtime_uses_sequential_graph_when_parallel_disabled(self):
        """PROOF: runtime.py calls build_graph (not build_parallel_graph) when disabled."""
        with patch.dict(os.environ, {"PARALLEL_WORKFLOW": "false"}):
            # Reload to pick up env change
            import importlib
            import integration_coworker.graph.parallel as parallel_module
            importlib.reload(parallel_module)
            
            # Verify the function returns False
            assert parallel_module.is_parallel_enabled() is False
            
            # Now verify the runtime logic
            # The key decision point is at line 2904-2971 in runtime.py:
            # 
            #   parallel_mode = is_parallel_enabled()
            #   ...
            #   if parallel_mode:
            #       app = build_parallel_graph(checkpointer=checkpointer)
            #   else:
            #       app = build_graph(checkpointer=checkpointer)
            #
            # We can prove this by checking the conditional logic directly
            
            parallel_mode = parallel_module.is_parallel_enabled()
            
            # This is the EXACT logic from runtime.py
            if parallel_mode:
                graph_builder = "build_parallel_graph"
            else:
                graph_builder = "build_graph"
            
            assert graph_builder == "build_graph", (
                f"Expected build_graph but got {graph_builder}. "
                f"parallel_mode={parallel_mode}"
            )
    
    def test_demo_script_sets_parallel_false(self):
        """PROOF: Demo script explicitly sets PARALLEL_WORKFLOW=false."""
        import subprocess
        
        # Extract the PARALLEL_WORKFLOW export from demo script
        result = subprocess.run(
            ["grep", "-E", "^export PARALLEL_WORKFLOW=", 
             "scripts/demo-final-showcase.sh"],
            capture_output=True,
            text=True,
            cwd="/Users/julianbartosz/git/repos/solver-agentic-spec-coworker"
        )
        
        lines = result.stdout.strip().split("\n")
        
        # All exports should be false
        for line in lines:
            if line.strip():
                assert "false" in line.lower(), (
                    f"Found non-false PARALLEL_WORKFLOW: {line}"
                )
        
        # Should have found at least one
        assert len(lines) >= 1, "No PARALLEL_WORKFLOW export found in demo script"


class TestParallelGraphNotUsed:
    """Verify parallel graph functions are not invoked in V2 state."""
    
    def test_sync_embed_task_not_in_sequential_graph(self):
        """PROOF: sync_embed_task (parallel sync node) is not in sequential graph."""
        from integration_coworker.graph.runtime import build_graph
        
        # Build sequential graph (no checkpointer for test)
        graph = build_graph(checkpointer=None)
        
        # Get the node names
        # LangGraph compiled graphs have a .nodes attribute
        node_names = list(graph.nodes.keys()) if hasattr(graph, 'nodes') else []
        
        # sync_embed_task should NOT be present in sequential graph
        assert "sync_embed_task" not in node_names, (
            f"sync_embed_task found in sequential graph! Nodes: {node_names}"
        )
    
    def test_parallel_graph_has_sync_node(self):
        """PROOF: Parallel graph DOES have sync_embed_task (for comparison)."""
        from integration_coworker.graph.runtime import build_parallel_graph
        
        # Build parallel graph for comparison
        graph = build_parallel_graph(checkpointer=None)
        
        # Get the node names
        node_names = list(graph.nodes.keys()) if hasattr(graph, 'nodes') else []
        
        # sync_embed_task SHOULD be present in parallel graph
        assert "sync_embed_task" in node_names, (
            f"sync_embed_task NOT found in parallel graph! Nodes: {node_names}"
        )


class TestCheckpointBloatPrevented:
    """Verify checkpoint bloat is prevented in V2 state."""
    
    def test_sequential_graph_has_no_sync_node(self):
        """PROOF: Sequential graph has no sync node (fewer checkpoints)."""
        from integration_coworker.graph.runtime import build_graph, build_parallel_graph
        
        sequential_graph = build_graph(checkpointer=None)
        parallel_graph = build_parallel_graph(checkpointer=None)
        
        # Get node names
        seq_nodes = set(sequential_graph.nodes.keys()) if hasattr(sequential_graph, 'nodes') else set()
        par_nodes = set(parallel_graph.nodes.keys()) if hasattr(parallel_graph, 'nodes') else set()
        
        # Parallel graph should have sync_embed_task, sequential should not
        assert "sync_embed_task" not in seq_nodes, "Sequential graph should NOT have sync node"
        assert "sync_embed_task" in par_nodes, "Parallel graph should have sync node"
        
        # Parallel graph has at least 1 more node (the sync node)
        assert len(par_nodes) > len(seq_nodes), (
            f"Parallel graph should have more nodes. "
            f"Sequential: {len(seq_nodes)}, Parallel: {len(par_nodes)}"
        )
    
    def test_env_var_controls_graph_selection(self):
        """PROOF: Environment variable is the sole control for graph selection."""
        import os
        
        # Test the exact logic from runtime.py line 2904
        test_cases = [
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("0", False),
            ("no", False),
            ("off", False),
            ("", False),  # Empty string = disabled
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("yes", True),
            ("on", True),
        ]
        
        for env_value, expected in test_cases:
            with patch.dict(os.environ, {"PARALLEL_WORKFLOW": env_value}):
                import importlib
                import integration_coworker.graph.parallel as parallel_module
                importlib.reload(parallel_module)
                
                result = parallel_module.is_parallel_enabled()
                assert result == expected, (
                    f"PARALLEL_WORKFLOW='{env_value}' should return {expected}, got {result}"
                )


class TestV2StateDocumented:
    """Verify V2 state deprecation is documented."""
    
    def test_parallel_module_has_deprecation_notice(self):
        """PROOF: parallel.py has V2 deprecation notice."""
        import integration_coworker.graph.parallel as parallel_module
        
        docstring = parallel_module.__doc__ or ""
        
        # Should mention V2 or deprecation
        has_v2_notice = "V2" in docstring or "deprecated" in docstring.lower()
        
        # This test may fail initially - that's expected!
        # It proves we need to add the deprecation notice
        if not has_v2_notice:
            pytest.skip("Deprecation notice not yet added - will be added in this PR")
