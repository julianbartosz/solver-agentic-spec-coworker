import re
import uuid
from pathlib import Path
from typing import Optional
from integration_coworker.graph.state import WorkflowState


def _infer_provider_from_url(url: str) -> Optional[str]:
    """
    Infer provider code from a URL (spec server URL or spec URL).
    
    Examples:
    - "https://api.stripe.com/v1" → "stripe"
    - "https://api.hubspot.com" → "hubspot"
    - "https://my-api.acme.io" → "acme"
    """
    if not url or "://" not in url:
        return None

    try:
        # Extract domain from URL
        domain = url.split("://")[1].split("/")[0].lower()

        # Remove common prefixes/suffixes
        domain = domain.replace("api.", "").replace("www.", "")

        # Take first part before TLD
        parts = domain.split(".")
        if parts:
            # Filter out common TLDs and hosting suffixes
            filtered = [p for p in parts if p not in ("com", "io", "org", "net", "dev", "co", "app")]
            if filtered:
                return filtered[0]
    except Exception:
        pass

    return None


def _infer_provider_from_title(title: str) -> Optional[str]:
    """
    Infer provider code from OpenAPI info.title.
    
    Examples:
    - "Stripe API" → "stripe"
    - "Acme Widget API v2.0" → "acme_widget"
    - "Mock Payments Service" → "mock_payments"
    """
    if not title:
        return None

    # Clean up the title
    title = title.lower()

    # Remove common suffixes
    for suffix in [" api", " service", " v1", " v2", " v3", " v1.0", " v2.0", " v3.0"]:
        title = title.replace(suffix, "")

    # Convert to snake_case
    # Replace non-alphanumeric with underscore
    slug = re.sub(r'[^a-z0-9]+', '_', title.strip())
    slug = slug.strip('_')

    # Limit length
    if len(slug) > 30:
        slug = slug[:30].rstrip('_')

    return slug if slug else None


def _infer_provider_from_filepath(filepath: str) -> str:
    """
    Infer provider code from file path as last resort.
    
    Examples:
    - "tests/fixtures/mock_payments_openapi.yaml" → "mock_payments"
    - "/path/to/stripe_api.json" → "stripe"
    """
    try:
        stem = Path(filepath).stem.lower()

        # Remove common suffixes
        for suffix in ["_openapi", "-openapi", "_api", "-api", "_spec", "-spec"]:
            stem = stem.replace(suffix, "")

        # Normalize
        slug = re.sub(r'[^a-z0-9]+', '_', stem)
        slug = slug.strip('_')

        return slug if slug else "unknown"
    except Exception:
        return "unknown"


def infer_provider_code(
    spec_ref: str,
    parsed_spec: Optional[dict] = None,
) -> str:
    """
    Infer provider_code from spec content and/or spec reference.
    
    M5 Architecture: Dynamic inference, no hardcoded provider list.
    
    Priority order:
    1. spec.servers[0].url domain (most reliable for real APIs)
    2. spec.info.title (good for descriptive specs)
    3. Filepath/URL of spec itself (fallback)
    
    Args:
        spec_ref: Path or URL to the spec
        parsed_spec: Optional parsed OpenAPI dict (if available)
    
    Returns:
        Inferred provider_code string (never None)
    """
    # Strategy 1: Infer from spec servers URL
    if parsed_spec:
        servers = parsed_spec.get("servers", [])
        if servers and isinstance(servers, list):
            server_url = servers[0].get("url", "") if isinstance(servers[0], dict) else ""
            provider = _infer_provider_from_url(server_url)
            if provider:
                return provider

    # Strategy 2: Infer from spec info.title
    if parsed_spec:
        title = parsed_spec.get("info", {}).get("title", "")
        provider = _infer_provider_from_title(title)
        if provider:
            return provider

    # Strategy 3: Infer from spec URL/path
    if "://" in spec_ref:
        provider = _infer_provider_from_url(spec_ref)
        if provider:
            return provider

    # Strategy 4: Infer from filepath
    return _infer_provider_from_filepath(spec_ref)


def plan_run(state: WorkflowState) -> WorkflowState:
    """
    Reads: task_description, spec_refs, provider_code, options
    Writes: run_id, plan["use_repo"], plan["provider_code"], plan["primary_spec_ref"], 
            plan["supporting_spec_refs"], plan["steps"]
    
    Contract per Appendix C.3.1:
    - Supports multiple spec_refs (Phase 4 / M5)
    - spec_refs[0] is primary, spec_refs[1:] are supporting
    - Honors options.override_provider_code
    - Sets plan["use_repo"] only if repo_root is set AND repo_integration_enabled
    - Generates run_id for tracking
    
    M5 Enhancement:
    - Provider inference is now dynamic (no hardcoded list)
    - Uses spec content (servers URL, info.title) when available
    - Falls back to filepath-based inference
    """
    # 0. Generate run_id
    if not state.run_id:
        state.run_id = str(uuid.uuid4())

    # 1. Validate spec_refs (require at least one)
    if not state.spec_refs or len(state.spec_refs) < 1:
        error_msg = "At least one spec_ref is required"
        state.errors.append(error_msg)
        state.plan["failed"] = True
        state.completed_steps.append("plan_run")
        raise ValueError(error_msg)

    # Per design doc Appendix C.1.1: first spec_ref is primary, rest are supporting
    primary_ref = state.spec_refs[0]
    supporting_refs = state.spec_refs[1:] if len(state.spec_refs) > 1 else []

    # 2. Honor override_provider_code from options
    if state.options and state.options.override_provider_code:
        # Normalize: lowercase, replace non-alphanumeric with underscore
        normalized = re.sub(r'[^a-z0-9]+', '_', state.options.override_provider_code.lower())
        state.provider_code = normalized.strip('_')
    elif not state.provider_code:
        # M5: Dynamic provider inference (no hardcoded list)
        # Note: Full inference with parsed_spec happens in detect_and_parse_spec
        # Here we do preliminary inference from the spec ref path/URL
        state.provider_code = infer_provider_code(primary_ref, parsed_spec=None)

    # 3. Fix plan["use_repo"] logic per spec
    # Must have both repo_root AND repo_integration_enabled
    repo_integration_enabled = getattr(state.options, "repo_integration_enabled", True) if state.options else True
    state.plan = {
        "provider_code": state.provider_code,
        "primary_spec_ref": primary_ref,
        "supporting_spec_refs": supporting_refs,  # Per design doc Appendix C.1.1
        "spec_count": len(state.spec_refs),  # For multi-spec tracking
        "use_repo": bool(state.repo_root) and bool(repo_integration_enabled),
        "steps": [
            "ingest_spec",
            "detect_and_parse_spec",
            "build_silver_api_model",
            "embed_spec_chunks",
            "persist_silver_checkpoint",  # Per design doc Section 5.4
            "understand_task",
            "align_task_with_kg",
            "plan_integration_flow",
            "attach_policies_and_patterns",
        ],
    }

    # Add repo-specific steps if enabled
    if state.plan["use_repo"]:
        state.plan["steps"].extend([
            "attach_repo_context",
            "analyze_repo_layout",
            "apply_repo_integration_changes",
        ])

    # Code generation happens after policies (and repo context if enabled)
    state.plan["steps"].append("generate_code_and_tests")

    # Gold checkpoint after code generation (per design doc Section 5.4)
    state.plan["steps"].append("persist_gold_checkpoint")

    # Always validate and report
    state.plan["steps"].extend([
        "validate_integration_design",
        "build_report",
        "persist_run_outcome",  # Per design doc Section 5.4
    ])

    state.completed_steps.append("plan_run")
    return state
