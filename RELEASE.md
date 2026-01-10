# Release Process

This document describes how to version, changelog, and release Integration Co-Worker.

---

## Versioning

We follow [Semantic Versioning 2.0.0](https://semver.org/):

- **MAJOR**: Breaking changes to public API or CLI
- **MINOR**: New features, backward-compatible
- **PATCH**: Bug fixes, backward-compatible

Current version is defined in `pyproject.toml` under `[project].version`.

---

## Changelog

We follow [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.

### Categories
- **Added**: New features
- **Changed**: Changes to existing functionality
- **Deprecated**: Features marked for removal
- **Removed**: Features removed
- **Fixed**: Bug fixes
- **Security**: Security-related changes

### Rules
- Write entries as you merge PRs, not at release time
- Use past tense ("Added X" not "Add X")
- Link to relevant issues/PRs where helpful

---

## Release Steps

### Prerequisites
- [ ] `main` branch is green in CI
- [ ] All planned features for this release are merged
- [ ] CHANGELOG.md has entries under `[Unreleased]`

### Process

```bash
# 1. Ensure you're on main and up to date
git checkout main && git pull

# 2. Run full test suite
USE_SQLITE=true pytest tests/ -v

# 3. Verify docs build
mkdocs build --strict

# 4. Update version in pyproject.toml
# Edit: version = "X.Y.Z"

# 5. Move CHANGELOG entries from [Unreleased] to [X.Y.Z] - YYYY-MM-DD

# 6. Commit version bump
git add pyproject.toml CHANGELOG.md
git commit -m "chore: release vX.Y.Z"

# 7. Create annotated tag
git tag -a vX.Y.Z -m "Release vX.Y.Z"

# 8. Push commit and tag
git push origin main
git push origin vX.Y.Z

# 9. Build distribution (if publishing to PyPI)
python -m build

# 10. Create GitHub release with CHANGELOG entry as notes
```

---

## Release Checklist

Copy this checklist into your release PR:

```markdown
## Release vX.Y.Z Checklist

### Pre-release
- [ ] CI green on main
- [ ] Docs build passes (`mkdocs build --strict`)
- [ ] Demo flow passes (`USE_SQLITE=true USE_MOCK_LLM=true python -m integration_coworker demo`)
- [ ] No critical issues in backlog

### Release
- [ ] Version bumped in pyproject.toml
- [ ] CHANGELOG.md updated with release date
- [ ] Tag created and pushed
- [ ] GitHub release created with notes

### Post-release
- [ ] Verify tag appears in GitHub releases
- [ ] Announce in relevant channels
- [ ] Update any dependent projects
```

---

## Rollback Plan

If a release introduces critical bugs:

1. **Immediate**: Advise users to pin previous version
2. **Short-term**: Publish patch release with fix
3. **If fix is complex**: 
   - Delete the bad tag: `git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`
   - Delete GitHub release
   - Communicate rollback to users

---

## Hotfix Process

For critical fixes that can't wait for normal release:

1. Branch from the release tag: `git checkout -b hotfix/vX.Y.Z vX.Y.Z`
2. Apply minimal fix
3. Bump patch version
4. Follow normal release steps
5. Cherry-pick fix to main if applicable

---

## Version History

See [CHANGELOG.md](CHANGELOG.md) for full release history.
