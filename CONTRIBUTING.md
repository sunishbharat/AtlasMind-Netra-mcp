# Contributing to AtlasMind-Netra-mcp

Thanks for your interest! This guide covers everything you need to go from zero to a merged PR.

If you have a question before diving in, open a [GitHub Discussion](https://github.com/sunishbharat/AtlasMind-Netra-mcp/discussions) - it's the best place to ask.

---

## Table of Contents

- [What we're working on](#what-were-working-on)
- [Good first issues](#good-first-issues)
- [Dev setup](#dev-setup)
- [Running tests](#running-tests)
- [Code style](#code-style)
- [How to submit a PR](#how-to-submit-a-pr)
- [Commit message format](#commit-message-format)
- [Project structure](#project-structure)
- [Design principles](#design-principles)

---

## What we're working on

The project is organised into milestones:

| Milestone | Status | Description |
|-----------|--------|-------------|
| M1 - Core query loop | ✅ Complete | `query_jira`: clarification loop → JQL → results |
| M2 - Analysis engine | ✅ Complete | `IssueAnalyser`, `RankingEngine`, `get_issue_details` implemented and tested |
| M3 - Briefing | ✅ Complete | `generate_briefing` end-to-end; `get_report`; markdown report delivery |
| Docker / CF deploy | ✅ Complete | Multi-arch Docker image (GHCR); Cloud Foundry manifest; git-tag release workflow |
| M4 - Auth | 📋 Planned | Per-session credential binding; auth for the streamable-HTTP transport |
| M5 - `get_jira_context` | 🚧 In progress | Live Jira field/project metadata tool; GET /fields endpoint; PyPI package |

Check the [Issues tab](https://github.com/sunishbharat/AtlasMind-Netra-mcp/issues) for tasks tagged with their milestone.

---

## Good first issues

Look for issues tagged [`good first issue`](https://github.com/sunishbharat/AtlasMind-Netra-mcp/issues?q=label%3A%22good+first+issue%22). These are concrete, scoped tasks that don't require deep knowledge of the full system. Good examples:

- Adding a new `BaseDeliveryChannel` subclass (Slack, Teams, email) - `briefings/delivery.py` shows the pattern; just add a new subclass.
- Adding a GitHub Actions CI workflow (runs `pytest`, `ruff check`, `mypy` on every push/PR).
- Adding a Pydantic model and version field to `data/conventions.json` for schema validation.
- Implementing `get_jira_context` - the tool is registered but currently raises `ToolError`; it should return live Jira field/project metadata from the backend.

If nothing in the issue list fits but you have an idea, open a Discussion before writing code - a quick alignment check saves everyone time.

---

## Dev setup

**Prerequisites:** Python 3.12+, [`uv`](https://docs.astral.sh/uv/)

```bash
# 1. Fork and clone
git clone https://github.com/<your-username>/AtlasMind-Netra-mcp.git
cd AtlasMind-Netra-mcp

# 2. Install all runtime + dev dependencies
uv sync

# 3. Configure environment
cp .env.example .env
# Open .env and set at least GROQ_API_KEY
# All other settings are optional for running tests offline
```

You do **not** need a live Jira instance or the atlasMind backend to run the test suite - see below.

---

## Running tests

### Offline unit + integration tests (no API keys required)

The test suite fakes the LLM (`PydanticAI TestModel`), HTTP calls (`httpx.MockTransport`), and MCP transport (in-memory FastMCP client), so everything runs fully offline:

```bash
uv run python -m pytest --cov=core --cov=memory --cov=models --cov=config
```

The coverage gate is **85%**. PRs that drop coverage below this threshold will be flagged.

### Lint and type checks

Both must stay clean before a PR is merged:

```bash
uv run ruff check .          # linting
uv run ruff format --check . # formatting
uv run mypy .                # type checking (strict mode)
```

To auto-fix formatting and safe lint issues:

```bash
uv run ruff format .
uv run ruff check --fix .
```

### Interactive testing (live server)

```bash
# MCP Inspector - opens a browser UI to call tools manually
uv run fastmcp dev server.py:create_server
```

See the README for the full set of manual testing flows (Claude Desktop, streamable-HTTP, UI injection, end-to-end with the backend).

---

## Code style

- **Formatter:** `ruff format` (line length 100).
- **Linter:** `ruff` with `E, W, F, I, UP, B, SIM, N, RUF` rules - see `pyproject.toml`.
- **Types:** `mypy --strict`. All new code must be fully typed. No `# type: ignore` comments without a comment explaining why.
- **Logging:** Use `structlog` throughout - `logger = structlog.get_logger(__name__)`. Never use `print()` for diagnostics.
- **Dependency injection:** Wire dependencies in `build_orchestrator` / `build_briefing_orchestrator` in `server.py`. Don't reach into `Settings` from inside `core/` modules - pass what they need through constructors.
- **Tests:** New behaviour needs tests. The existing patterns use `PydanticAI TestModel` for LLM fakes and `httpx.MockTransport` for HTTP fakes - follow those rather than hitting real endpoints.

---

## How to submit a PR

1. **Open an issue or discussion first** for anything non-trivial. Agreeing on the approach before writing code avoids wasted effort.
2. Create a branch: `git checkout -b feat/my-feature` or `fix/short-description`.
3. Make your changes, keeping commits focused and the history readable.
4. Ensure all checks pass locally: `pytest`, `ruff check`, `ruff format --check`, `mypy`.
5. Open a PR against `main`. Fill in the PR template (what changed, why, how to test).
6. A maintainer will review within a few days. Please respond to review comments - PRs with no activity for 2 weeks may be closed.

**PR checklist:**
- [ ] Tests added or updated for the changed behaviour
- [ ] Coverage stays at or above 85%
- [ ] `ruff check`, `ruff format --check`, and `mypy` all pass
- [ ] No new `print()` calls - use `structlog`
- [ ] `.env.example` updated if new environment variables were added

---

## Commit message format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

[optional body]
[optional footer]
```

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`.

Examples:
```
feat(delivery): add Slack delivery channel
fix(clarifier): handle empty allowed_values list gracefully
docs: add CONTRIBUTING.md
test(orchestrator): add coverage for elicitation fallback path
```

---

## Project structure

```
AtlasMind-Netra-mcp/
├── server.py              # FastMCP entrypoint; all dependency wiring lives here
├── core/                  # Business logic (orchestrators, clarifier, LLM provider, etc.)
├── memory/                # Session stores, conventions store, Valkey backends
├── models/                # Pydantic response models (QueryResponse, BriefingResponse, …)
├── config/                # Settings (Pydantic-Settings, env-var driven)
├── briefings/             # Delivery channels (BaseDeliveryChannel + implementations)
├── prompts/               # Prompt templates (clarifier, agenda decomposer, analyser)
├── tests/                 # pytest tests; mirrors the source layout
├── docker/                # Dockerfile (multi-platform) and entrypoint.sh
├── scripts/               # cf-deploy.sh, docker-build-local.sh, cf-copy-env.sh
├── docs/                  # Design docs, API contracts, deployment runbooks
├── manifest.yml.template  # Cloud Foundry application manifest template
├── docker-compose.yml     # Local dev stack (server + Valkey)
├── .env.example           # All supported NETRA_* env vars with descriptions
└── pyproject.toml         # Dependencies, ruff, mypy, pytest config
```

Key design rule: **`server.py` is the only composition root.** Modules in `core/`, `memory/`, and `briefings/` receive dependencies through constructors - they never import from `config/` or instantiate their own collaborators.

---

## Design principles

A few decisions to understand before making architectural changes:

**Single renderer.** Charts and tables are always rendered in the AtlasMind frontend UI. The MCP server returns `chart_spec` and `display_fields` - it never produces rendered HTML or images itself. Don't add rendering logic to the server.

**Clarification transport fallback.** The clarifier tries MCP elicitation first (one round-trip if the host supports it) and falls back to the session round-trip pattern if the host declines or errors. New clarification behaviour should preserve both paths.

**Offline-first tests.** The test suite must run with no network access and no API keys. If your feature requires a real external call, write a fake/mock for it and test through that.

**Best-effort delivery.** Delivery channel failures (writing a report, pushing to Slack) must never fail the query itself. Follow the pattern in `briefings/delivery.py`: catch and log, set `errors`, return the response.

---

## Questions?

Open a [GitHub Discussion](https://github.com/sunishbharat/AtlasMind-Netra-mcp/discussions) - it's the best place for questions, ideas, and design conversations.
