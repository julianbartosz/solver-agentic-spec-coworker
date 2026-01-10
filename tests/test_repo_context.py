"""
Tests for repository profile and context detection.
"""


def test_repo_profile_has_archetype():
    """
    P2.1: Test that RepoProfile has archetype field and SUBATOMIC_MOCK_PROFILE uses it.
    """
    from integration_coworker.repo.profiles import SUBATOMIC_MOCK_PROFILE
    
    assert hasattr(SUBATOMIC_MOCK_PROFILE, 'archetype'), "RepoProfile should have archetype field"
    assert SUBATOMIC_MOCK_PROFILE.archetype == "fastapi_service", \
        "Mock profile should have fastapi_service archetype"
    assert SUBATOMIC_MOCK_PROFILE.name == "subatomic_mock_service"
