"""
Showcase specs configuration.

Single source of truth for the multi-spec showcase sequence.
Matches demo-final-showcase.sh for parity.

Each spec entry is (spec_file, task_description, expected_provider).
"""
from typing import List, Tuple, NamedTuple
from pathlib import Path


class ShowcaseSpec(NamedTuple):
    """A spec in the showcase sequence."""
    spec_file: str  # Relative to repo root or absolute
    task_description: str
    expected_provider: str  # For display/validation


# Full showcase sequence (matches demo-final-showcase.sh)
SHOWCASE_SPECS: List[ShowcaseSpec] = [
    ShowcaseSpec(
        spec_file="specs/stripe_api.json",
        task_description="Create a checkout session for a one-time payment",
        expected_provider="stripe",
    ),
    ShowcaseSpec(
        spec_file="specs/twilio_messaging_v1.json",
        task_description="Send an SMS message to a phone number",
        expected_provider="twilio",
    ),
    ShowcaseSpec(
        spec_file="specs/github_api.json",
        task_description="Create a new issue in a repository",
        expected_provider="github",
    ),
]

# Quick showcase (subset for faster testing)
QUICK_SHOWCASE_SPECS: List[ShowcaseSpec] = SHOWCASE_SPECS[:2]

# Minimal showcase (single spec for smoke tests)
MINIMAL_SHOWCASE_SPECS: List[ShowcaseSpec] = SHOWCASE_SPECS[:1]


def get_showcase_specs(mode: str = "full") -> List[ShowcaseSpec]:
    """
    Get showcase specs based on mode.
    
    Args:
        mode: "full", "quick", or "minimal"
        
    Returns:
        List of ShowcaseSpec tuples
    """
    if mode == "quick":
        return QUICK_SHOWCASE_SPECS
    elif mode == "minimal":
        return MINIMAL_SHOWCASE_SPECS
    else:
        return SHOWCASE_SPECS


def resolve_spec_path(spec_file: str, repo_root: Path) -> str:
    """
    Resolve a spec file path to absolute.
    
    If already absolute, returns as-is.
    If relative, resolves from repo_root.
    """
    path = Path(spec_file)
    if path.is_absolute():
        return str(path)
    
    resolved = repo_root / spec_file
    if resolved.exists():
        return str(resolved.resolve())
    
    # Try from current working directory as fallback
    cwd_path = Path.cwd() / spec_file
    if cwd_path.exists():
        return str(cwd_path.resolve())
    
    # Return original and let caller handle missing file
    return spec_file
