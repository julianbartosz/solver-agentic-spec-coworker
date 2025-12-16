# Changelog

All notable changes to Integration Co-Worker are documented here.

This project follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

- **LLM Response Cache** - Redis-backed caching for LLM responses
  - Configurable TTL (default 24 hours)
  - Cache statistics via `cache-stats` CLI command
  - Cache clear via `cache-clear` CLI command

- **Parallel Workflow Execution** - Concurrent node execution
  - `PARALLEL_WORKFLOW=true` environment variable
  - Parallel embedding and task understanding branches
  - Sync barrier for branch convergence

- **MkDocs Documentation Site** - Professional documentation
  - Material theme with dark mode
  - Auto-generated API reference
  - Search functionality
  - GitHub Pages deployment

### Changed

- Updated pyproject.toml with new optional dependencies
- Added Redis service to docker-compose.yml

---

## [0.1.0] - 2024-12-XX

### Added

- Initial release of Integration Co-Worker
- LangGraph-based workflow orchestration
- Silver-Gold medallion data model
- OpenAPI specification parsing
- Multi-provider LLM support (OpenAI, Anthropic, Google)
- Knowledge graph for pattern learning
- Repository integration with archetype detection
- CLI with run, demo, status, health commands
- Streamlit web UI
- PostgreSQL + pgvector support
- SQLite fallback for development
- Comprehensive test suite (170+ tests)

### Providers Supported

- Stripe
- Twilio
- GitHub
- OpenAI
- Slack
- Spotify
- Asana
- Box
- Zoom
- DigitalOcean
- CircleCI
- Mailchimp
- Plaid

---

## Version History

| Version | Date | Highlights |
|---------|------|------------|
| 0.1.0 | Dec 2024 | Initial release |

---

For the full commit history, see [GitHub Commits](https://github.com/julianbartosz/solver-agentic-spec-coworker/commits/main).

---

[Back to Testing](testing.md)
