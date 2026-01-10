import re
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
import logging

from integration_coworker.graph.state import WorkflowState
from integration_coworker.api.types import IntegrationOptions

logger = logging.getLogger(__name__)


def _init_run_status(run_id: str) -> bool:
    """
    Create initial run_status record with 'running' status.
    
    This must happen BEFORE any checkpoints are saved, because
    run_checkpoints has a FK reference to run_status.
    
    Returns True if successful, False if failed (e.g., dry run or DB unavailable).
    """
    try:
        from integration_coworker.persistence.db import get_connection, get_engine_type
        
        engine = get_engine_type()
        # V30-P03 Fix: Use context manager to prevent connection leaks
        with get_connection() as conn:
            cur = conn.cursor()
            
            started_at = datetime.now(timezone.utc).isoformat()
            
            if engine == "postgres":
                cur.execute("""
                    INSERT INTO integration_gold.run_status 
                        (run_id, status, started_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (run_id) DO NOTHING
                """, (run_id, "running", started_at))
            else:
                cur.execute("""
                    INSERT OR IGNORE INTO run_status 
                        (run_id, status, started_at)
                    VALUES (?, ?, ?)
                """, (run_id, "running", started_at))
            
            conn.commit()
        logger.debug(f"Initialized run_status for run_id={run_id}")
        return True
        
    except Exception as e:
        logger.warning(f"Failed to init run_status (non-fatal): {e}")
        return False


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
    except Exception as e:
        # Item G: Log rather than silently swallow
        logger.debug(f"URL domain parsing failed for provider inference: {e}")

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
    
    V1.2: Improved handling of temp directories and generic filenames.
    V43-003: For data files (CSV, Excel, text), use file-type-based provider codes
             instead of filename to avoid misleading provider names like "airports".
    
    Examples:
    - "tests/fixtures/mock_payments_openapi.yaml" → "mock_payments"
    - "/path/to/stripe_api.json" → "stripe"
    - "/tmp/tmpxyz123/payments_openapi.yaml" → "payments" (skips temp dir)
    - "/tmp/tmpxyz123/spec.yaml" → "unknown_api" (generic filename fallback)
    - "/path/to/airports.csv" → "csv_import" (V43-003: file-type provider)
    - "/path/to/report.xlsx" → "excel_import" (V43-003: file-type provider)
    """
    try:
        path = Path(filepath)
        stem = path.stem.lower()
        suffix = path.suffix.lower()
        
        # V43-003: For data files, use file-type-based provider codes
        # This prevents misleading provider names like "airports" from "airports.csv"
        data_file_providers = {
            '.csv': 'csv_import',
            '.tsv': 'csv_import',  # Tab-separated values
            '.xlsx': 'excel_import',
            '.xls': 'excel_import',
            '.txt': 'file_import',  # Fixed-width or other text
            '.dat': 'file_import',
            '.fixed': 'file_import',
        }
        
        if suffix in data_file_providers:
            return data_file_providers[suffix]

        # Remove common suffixes
        for suffix_pattern in ["_openapi", "-openapi", "_api", "-api", "_spec", "-spec"]:
            stem = stem.replace(suffix_pattern, "")

        # Normalize
        slug = re.sub(r'[^a-z0-9]+', '_', stem)
        slug = slug.strip('_')

        # V1.2: Check if result is too generic (e.g., from temp dir or generic filename)
        generic_names = {
            'tmp', 'temp', 'spec', 'openapi', 'api', 'swagger', 'schema',
            'input', 'output', 'data', 'file', 'test', 'example', 'sample',
            'document', 'doc', 'config', 'definition'
        }
        
        if slug in generic_names or not slug:
            # Try parent directory name (might have meaningful name)
            parent_name = path.parent.name.lower()
            
            # Skip if parent is also a temp directory pattern
            if not (parent_name.startswith('tmp') or 
                    parent_name.startswith('temp') or 
                    re.match(r'^[a-z0-9]{8,}$', parent_name)):  # Skip random hashes
                parent_slug = re.sub(r'[^a-z0-9]+', '_', parent_name).strip('_')
                if parent_slug and parent_slug not in generic_names:
                    return parent_slug
            
            # Fallback: return "unknown_api" for generic names
            return "unknown_api"

        return slug
    except Exception as e:
        # Item G: Log rather than silently return fallback
        logger.debug(f"Filepath provider inference failed: {e}")
        return "unknown_api"


def infer_provider_code(
    spec_ref: str,
    parsed_spec: Optional[dict] = None,
    override: Optional[str] = None,
) -> str:
    """
    Infer provider_code from spec content and/or spec reference.
    
    M5/V2 Architecture: Dynamic inference with priority cascade.
    
    Priority order (V2 - Section 3.10.4):
    1. Explicit override (from options.override_provider_code)
    2. spec.info['x-provider-code'] extension (OpenAPI extension)
    3. spec.servers[0].url domain (most reliable for real APIs)
    4. spec.info.title (good for descriptive specs)
    5. Spec URL/path inference (fallback)
    6. Filepath stem normalization (last resort)
    
    Args:
        spec_ref: Path or URL to the spec
        parsed_spec: Optional parsed OpenAPI dict (if available)
        override: Optional explicit provider code override
    
    Returns:
        Inferred provider_code string (never None)
    """
    # Priority 1: Explicit override
    if override:
        normalized = re.sub(r'[^a-z0-9]+', '_', override.lower())
        return normalized.strip('_') or "unknown"
    
    if parsed_spec:
        info = parsed_spec.get("info", {})
        
        # Priority 2: x-provider-code extension
        x_provider = info.get("x-provider-code")
        if x_provider and isinstance(x_provider, str):
            normalized = re.sub(r'[^a-z0-9]+', '_', x_provider.lower())
            result = normalized.strip('_')
            if result:
                return result
        
        # Priority 3: Infer from spec servers URL
        servers = parsed_spec.get("servers", [])
        if servers and isinstance(servers, list):
            server_url = servers[0].get("url", "") if isinstance(servers[0], dict) else ""
            provider = _infer_provider_from_url(server_url)
            if provider:
                return provider
        
        # Priority 4: Infer from spec info.title
        title = info.get("title", "")
        provider = _infer_provider_from_title(title)
        if provider:
            return provider

    # Priority 5: Infer from spec URL/path
    if "://" in spec_ref:
        provider = _infer_provider_from_url(spec_ref)
        if provider:
            return provider

    # Priority 6: Infer from filepath
    return _infer_provider_from_filepath(spec_ref)


def plan_run(state: WorkflowState) -> WorkflowState:
    """
    Reads: task_description, spec_refs, provider_code, options
    Writes: run_id, plan["use_repo"], plan["provider_code"], plan["primary_spec_ref"], 
            plan["supporting_spec_refs"], plan["steps"], pending_specs
    
    Contract per Appendix C.3.1:
    - Supports multiple spec_refs (Phase 4 / M5 / V2)
    - spec_refs[0] is primary, spec_refs[1:] are supporting
    - Honors options.override_provider_code
    - Sets plan["use_repo"] only if repo_root is set AND repo_integration_enabled
    - Generates run_id for tracking
    - Populates pending_specs list for multi-spec processing
    - Creates initial run_status record for checkpoint FK constraints
    
    M5/V2 Enhancement:
    - Provider inference is now dynamic (no hardcoded list)
    - Uses spec content (servers URL, info.title) when available
    - Falls back to filepath-based inference
    """
    # Bug #88 fix: Convert dict options to IntegrationOptions object
    # When options is passed as a dict (e.g., from tests or direct API calls),
    # it needs to be converted to an IntegrationOptions dataclass
    if state.options is not None and isinstance(state.options, dict):
        logger.info("Converting dict options to IntegrationOptions object")
        state.options = IntegrationOptions(**state.options)

    # 0. Generate run_id
    if not state.run_id:
        state.run_id = str(uuid.uuid4())

    # 0b. Initialize run_status record BEFORE any checkpoints can be saved
    # This is required because run_checkpoints has a FK to run_status
    is_dry_run = state.options.dry_run if state.options else False
    if not is_dry_run:
        _init_run_status(state.run_id)

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

    # 2. Populate pending_specs list (V2 Section 3.12)
    override = state.options.override_provider_code if state.options else None
    for i, ref in enumerate(state.spec_refs):
        # Initial provider inference from ref only (full inference after parsing)
        provider = infer_provider_code(ref, parsed_spec=None, override=override if i == 0 else None)
        state.pending_specs.append({
            "index": i,
            "ref": ref,
            "provider_code": provider,
            "is_primary": i == 0,
        })

    # 3. Honor override_provider_code from options for primary spec
    if state.options and state.options.override_provider_code:
        # Normalize: lowercase, replace non-alphanumeric with underscore
        normalized = re.sub(r'[^a-z0-9]+', '_', state.options.override_provider_code.lower())
        state.provider_code = normalized.strip('_')
    elif not state.provider_code:
        # M5/V2: Dynamic provider inference (no hardcoded list)
        # Note: Full inference with parsed_spec happens in detect_and_parse_spec
        # Here we do preliminary inference from the spec ref path/URL
        state.provider_code = infer_provider_code(primary_ref, parsed_spec=None)

    # 3. Fix plan["use_repo"] logic per spec
    # Must have both repo_root AND repo_integration_enabled
    repo_integration_enabled = getattr(state.options, "repo_integration_enabled", True) if state.options else True
    
    # Bug #55 fix: Convert string repo_profile to RepoProfile object.
    # Users may pass a string like "typescript" or "python" for convenience.
    # This must happen before the eager load check below.
    if state.repo_profile is not None and isinstance(state.repo_profile, str):
        from integration_coworker.repo.models import RepoProfile
        profile_name = state.repo_profile
        # Create a basic RepoProfile from the string
        # Language detection from common profile names
        language = "python"
        if profile_name.lower() in ("typescript", "javascript", "ts", "js", "node", "nodejs"):
            language = "typescript"
        elif profile_name.lower() in ("python", "py", "django", "flask", "fastapi"):
            language = "python"
        
        state.repo_profile = RepoProfile(
            name=profile_name,
            language=language,
            integrations_root="src/integrations" if language == "python" else "src/integrations",
            tests_root="tests/integrations" if language == "python" else "tests/integrations",
            profile_source="string_parameter",
        )
        logger.info(f"Converted string repo_profile '{profile_name}' to RepoProfile object (language={language})")
    
    # Bug #22 fix: Eagerly load repo_profile from config file when repo_root is provided.
    # This ensures the profile is available during code generation, even though
    # attach_repo_context runs later in the workflow for repo wiring.
    if state.repo_root and state.repo_profile is None:
        try:
            from integration_coworker.graph.nodes.attach_repo_context import _get_profile_config_first
            state.repo_profile = _get_profile_config_first(
                state.repo_root, 
                use_llm_fallback=False  # Only use config file, don't invoke LLM here
            )
            logger.info(f"Eagerly loaded repo_profile: {state.repo_profile.name} (source: {state.repo_profile.profile_source})")
        except Exception as e:
            logger.debug(f"Could not eagerly load repo_profile: {e}")
            # Will be loaded later by attach_repo_context
    
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
