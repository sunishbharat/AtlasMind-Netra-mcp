# AtlasMind-Netra-mcp

AtlasMind-Netra-mcp is the AI agent and MCP server layer of the AtlasMind platform. It sits between any MCP-compatible client (Claude Desktop, Cursor, or a custom UI) and the `atlasMind-Lite` JQL execution backend, running a multi-turn clarification loop before dispatching any query to Jira.

Jira queries written by an LLM routinely fail or return wrong results because natural language is ambiguous - "escalation" could be a label, a custom field, or a priority value; "today" could mean created, updated, or due today; "my team" has no JQL equivalent. This agent fixes that by detecting ambiguous terms, asking one targeted clarifying question using real field names from the live Jira instance, learning team conventions so the same question is never asked twice, and only calling `atlasMind-Lite` once the intent is fully resolved.

Five MCP tools are exposed publicly: `query_jira`, `generate_briefing`, `get_report`, `get_jira_context`, and `search_context`. Everything else - the clarification loop, session store, intent classifier, and conventions store - is internal. All five tools are fully wired: `query_jira` runs the clarification-to-dispatch loop; `generate_briefing` decomposes a meeting agenda into topics, runs per-topic analysis, and returns a ranked, cited briefing optionally enriched with Confluence context; `get_report` fetches a stored briefing by ID; `get_jira_context` returns Jira instance metadata; `search_context` searches Confluence via CQL and returns ranked page excerpts for a natural-language query. The server is production-ready for cloud deployment (streamable-http with `stateless_http=True`, bounded in-memory cache, and an optional Valkey session store for horizontal scaling).

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- An LLM API key - choose one:
  - [Groq](https://console.groq.com/keys) (free tier, default): `GROQ_API_KEY`
  - [Google AI Studio](https://aistudio.google.com/apikey) (free tier): `GOOGLE_API_KEY`
  - Anthropic, AWS Bedrock, or any OpenAI-compatible endpoint (see [LLM providers](#llm-providers) below)
- Optional for end-to-end runs: the atlasMind backend running on `http://localhost:8000`
- Optional for Confluence research (`search_context` tool and briefing enrichment): a Confluence Cloud or Data Center instance with API token auth

## Setup

```powershell
# install all runtime and dev dependencies
uv sync

# configure environment
copy .env.example .env
# edit .env: set your LLM API key (GROQ_API_KEY or GOOGLE_API_KEY) and NETRA_LLM__MODEL
```

Every setting is overridable via `NETRA_*` environment variables; see `.env.example` for the full list (backend URL, LLM model, session TTL, clarification limits, transport).

## LLM providers

All five AI agents (clarifier, agenda decomposer, issue analyser, query intent analyser, and context extractor) share the same LLM, configured with two env vars:

| Provider | `NETRA_LLM__MODEL` | API key env var | Notes |
|---|---|---|---|
| Groq (default) | `groq:llama-3.3-70b-versatile` | `GROQ_API_KEY` | Free tier; fast |
| Google Gemini | `google:gemini-2.0-flash` | `GOOGLE_API_KEY` | Free tier; get key at aistudio.google.com |
| Google Gemini (alt) | `google:gemini-1.5-flash` | `GOOGLE_API_KEY` | Stable free-tier alternative |
| Anthropic | `anthropic:claude-haiku-4-5-20251001` | `ANTHROPIC_API_KEY` | Paid |
| AWS Bedrock | `bedrock:anthropic.claude-sonnet-4-5` | AWS credential chain | IAM role or key pair |
| OpenAI-compatible | `openai:<model-name>` | `NETRA_LLM__API_KEY` or `OPENAI_API_KEY` | Set `NETRA_LLM__BASE_URL` to the provider endpoint |

Example `.env` for Google Gemini free tier:

```
NETRA_LLM__MODEL=google:gemini-2.0-flash
GOOGLE_API_KEY=your_key_here
```

The provider is detected automatically from the model string prefix - no other code changes needed when switching.

## Confluence research

Two of the five MCP tools support Confluence:

| Tool | Confluence role |
|---|---|
| `search_context` | Direct Confluence search - call it explicitly to investigate pages by topic, version, or keyword |
| `generate_briefing` | Internally searches Confluence per agenda topic (when configured) and passes found page references into issue analysis, surfacing Confluence evidence alongside Jira comments |

Both tools require `NETRA_CONFLUENCE__BASE_URL` and `NETRA_CONFLUENCE__API_TOKEN` to be set. When they are not set, `search_context` returns an empty result (no error) and `generate_briefing` runs without Confluence context.

To enable, add these vars to `.env`:

| Variable | Description |
|---|---|
| `NETRA_CONFLUENCE__BASE_URL` | Confluence base URL. Cloud: `https://yourorg.atlassian.net/wiki`. Server/DC: `https://confluence.internal` (no `/wiki` suffix) |
| `NETRA_CONFLUENCE__EMAIL` | Confluence account email (Cloud only; omit for Server/DC) |
| `NETRA_CONFLUENCE__API_TOKEN` | Confluence API token (Cloud) or PAT (Server/DC). Create at id.atlassian.com/manage-profile/security/api-tokens |
| `NETRA_CONFLUENCE__DEFAULT_SPACES` | Comma-separated space keys to search, e.g. `ENG,PROJ` (optional; searches all spaces if unset) |

Example `.env` entries (Confluence Cloud):

```
NETRA_CONFLUENCE__BASE_URL=https://yourorg.atlassian.net/wiki
NETRA_CONFLUENCE__EMAIL=you@yourorg.com
NETRA_CONFLUENCE__API_TOKEN=your_token_here
NETRA_CONFLUENCE__DEFAULT_SPACES=ENG,DOCS
```

`search_context` accepts a natural-language query and an optional `spaces` list override. It extracts version references and intent type via `QueryIntentAnalyser`, builds up to three CQL variants, deduplicates results, and runs each matching page through `ContextExtractor` to return the most relevant passage. When Confluence is configured, `generate_briefing` also researches each agenda topic concurrently and passes the found page references into the issue analysis step, surfacing Confluence evidence alongside Jira comments.

## How to test the MCP server

### 1. Run the automated test suite (no network, no API keys needed)

The unit and integration tests fake the LLM (PydanticAI `TestModel`), the backend HTTP calls (`httpx.MockTransport`), and the MCP transport (in-memory FastMCP client), so they run fully offline:

```powershell
uv run python -m pytest --cov=core --cov=memory --cov=models --cov=config
```

Expected: all tests pass with coverage well above the 85% project gate.

Lint and type checks (both must stay clean):

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

### 2. Interactive testing with the MCP Inspector

The fastest way to poke the live server is FastMCP's dev inspector (opens a browser UI where you can list tools and call them with arbitrary arguments; requires Node.js since the inspector itself is an npm package):

```powershell
uv run fastmcp dev server.py:create_server
```

Then in the inspector:

1. Call `query_jira` with `query = "show escalations from today"` and `session_id = "test-1"`.
2. Expect a response with `requires_user_input: true` and a `clarification_question` referencing label/field/priority options - this works even **without** the backend or a Jira instance.
3. Call `query_jira` again with the same `query` and `session_id`, plus `clarification_answer = "we use the escalation label"`.
4. With the atlasMind backend running you get JQL + issues back; without it you get a graceful `errors: ["backend unreachable after retries: ..."]` response, which still proves the clarification loop end-to-end.
5. Repeat step 1 with a new `session_id` - no question this time: the convention was learned and persisted to `data/conventions.json`.

All five tools are live. Try `generate_briefing` with `agenda_text = "top blockers in project X, risks for carline Y"` and `session_id = "brief-1"` to see the full briefing pipeline. Try `search_context` with `query = "release blockers for R1"` (requires `NETRA_CONFLUENCE__URL` to be set).

### 3. Testing from Claude Desktop (stdio)

Add this to `claude_desktop_config.json` (Settings -> Developer -> Edit Config).

With Groq (default):

```json
{
  "mcpServers": {
    "atlasmind-netra": {
      "command": "uv",
      "args": [
        "--directory",
        "\\path\\to\\AtlasMind-Netra-mcp",
        "run",
        "python",
        "server.py"
      ],
      "env": {
        "GROQ_API_KEY": "gsk_..."
      }
    }
  }
}
```

With Google Gemini free tier (alternative):

```json
{
  "mcpServers": {
    "atlasmind-netra": {
      "command": "uv",
      "args": [
        "--directory",
        "\\path\\to\\AtlasMind-Netra-mcp",
        "run",
        "python",
        "server.py"
      ],
      "env": {
        "NETRA_LLM__MODEL": "google:gemini-2.0-flash",
        "GOOGLE_API_KEY": "AIza..."
      }
    }
  }
}
```

Restart Claude Desktop, then ask: *"Using the atlasmind-netra tools, show escalations from today."* Claude should relay the clarification question to you verbatim (the tool description forbids it from answering itself), call the tool again with your answer, and present the results.

### 4. Testing over streamable-http (production transport)

In this mode the server runs standalone as a web service and clients connect to it over HTTP, instead of spawning it as a child process (stdio). One server process can serve many users; this is the production transport from the design doc.

#### Terminal 1 - start the server in HTTP mode

```powershell
$env:NETRA_SERVER__TRANSPORT = "streamable-http"
uv run python server.py
```

(Or set `NETRA_SERVER__TRANSPORT=streamable-http` in `.env` instead. `GROQ_API_KEY` must be available in this terminal or in `.env`, because the clarifier LLM runs server-side.)

`main()` in `server.py` reads the settings and starts uvicorn bound to `127.0.0.1:8765`. The `/mcp` path is FastMCP's default endpoint for the streamable-http protocol, so the complete URL clients need is:

```
http://127.0.0.1:8765/mcp
```

To change the bind address or port, set `NETRA_SERVER__HOST` / `NETRA_SERVER__PORT` before starting (e.g. host `0.0.0.0` to accept connections from other machines). Leave this terminal running; everything below happens in a second terminal.

#### Terminal 2, Option A - MCP Inspector

```powershell
npx @modelcontextprotocol/inspector
```

In the browser UI that opens: select transport **Streamable HTTP**, enter `http://127.0.0.1:8765/mcp`, and click **Connect**. You get the same UI as the stdio dev mode - list the five tools and call `query_jira` with JSON arguments (see the test script in section 2).

#### Terminal 2, Option B - Claude Code

Register the running server once:

```powershell
claude mcp add --transport http atlasmind-netra http://127.0.0.1:8765/mcp
```

This command tells Claude Code to remember this server:
- `claude mcp add` - register a new MCP server with Claude Code
- `--transport http` - connect over HTTP (the server is a separate process, not a subprocess spawned by Claude)
- `atlasmind-netra` - the name Claude Code will use to identify this server
- `http://127.0.0.1:8765/mcp` - the address where the server is listening (loopback, port 8765, `/mcp` path)

You only need to run this once; Claude Code remembers the registration across sessions.

Then start a Claude Code session and ask, for example: *"Using the atlasmind-netra tools, show escalations from today."* Claude relays the clarification question, calls the tool again with your answer, and presents the results. Remove the registration later with `claude mcp remove atlasmind-netra`.

#### Notes for this mode

- **Sessions live in the server process.** The Phase 1 store is in-memory: a pending clarification survives across tool calls while the server runs, but a server restart clears it. Learned conventions survive restarts - they are persisted in `data/conventions.json`.
- **No auth yet.** Per-session credential binding is Milestone 4; until then anyone who can reach the port can query, and Jira auth comes from the backend's profile. Keep the default loopback bind (`127.0.0.1`) unless you are on a trusted network.

### 5. Showing results in the AtlasMind browser UI (show_in_ui)

`query_jira` accepts an opt-in `show_in_ui: true` flag. After the query succeeds, Netra-mcp pushes `<generated JQL> /raw` (the flag is appended after the JQL, separated by a space, so the bridge reads it as a command) into the live AtlasMind chat window via the frontendUI bridge server's `POST /api/mcp/inject` endpoint (contract: `docs/frontendui_bridge_contract.md`). The browser runs it through its normal send flow and renders the table and chart on screen - charts are only ever drawn by the frontend (single-renderer principle), and exports stay one click away there.

To test:

1. Start the frontendUI bridge server (`uv run python main.py` in the frontendUI repo, port 8001) and open the chat UI in a browser tab.
2. If the bridge has an `API_KEY` configured, set `NETRA_FRONTEND__API_KEY` in `.env`.
3. Call the tool with the flag, e.g. in the inspector:
   `query_jira` with `{"query": "open bugs by assignee as a bar chart", "session_id": "ui-1", "show_in_ui": true}`.
4. Expect: the chart appears in the browser, and the tool response carries `ui_injected: true`.
5. Degradation checks: close the browser tab and repeat - the query still succeeds, with `ui_injected: false` and an errors note ("No active UI session"); same when the bridge is not running at all.

Notes: injection re-executes the JQL once via the browser (the `/raw` flag skips LLM generation, so only one extra Jira search); it is opt-in per call by contract and never on by default.

### 6. End-to-end with the atlasMind backend

1. Start the backend (`uv run python app.py --server --model groq` in the backend repo) on port 8000, with its Jira profile configured.
2. Optionally point the clarifier at the backend's cached Jira metadata so questions use real field names:
   `NETRA_CLARIFICATION__JIRA_FIELDS_PATH=../AtlasMind/data/<profile>/jira_fields.json`
3. Run any of the flows above; `query_jira` responses now include the generated JQL, issues, `display_fields`, and `chart_spec` passed through from the backend (contract: `docs/atlasmind_lite_api_contract.md`).

## Human-verifiable query reports

Every dispatched `query_jira` call (successful or failed at the backend) also writes a markdown report to `data/reports/<timestamp>_<session>_<id>.md` and returns its location in `report_path`. The report contains the original query, the applied term interpretations (so a wrongly learned convention is caught in the output), the generated JQL, the issue table, the chart specification, and any warnings - everything a human needs to verify the answer. Clarification questions do not produce reports.

- Disable with `NETRA_DELIVERY__ENABLED=false`; change the folder with `NETRA_DELIVERY__OUTPUT_DIR`.
- Delivery is best-effort: a failed write never fails the query (a note appears in `errors`).
- The markdown file channel is the first `BaseDeliveryChannel` implementation (`briefings/delivery.py`); Teams/Slack/email/Confluence channels plug in as subclasses with Milestone 3.

## Docker and cloud deployment

The server ships as a self-contained Docker image. Valkey is bundled inside the container on loopback - no external Redis/Valkey service is required. Full runbook (CF, OCI A1, blue-green, nginx TLS): `docs/docker_cf_deployment.md`.

### Local dev with docker-compose

```bash
# From repo root (never from docker/)
docker-compose up
curl http://localhost:8765/health   # {"status":"ok"}
curl http://localhost:8765/mcp      # MCP endpoint
```

`./data` is mounted into the container so reports and learned conventions persist across restarts.

### Build the image locally (single arch, fast)

Requires Docker Desktop 4.x+ with buildx (shipped by default).

```bash
bash scripts/docker-build-local.sh
```

Detects your native architecture (`amd64` on Intel/AMD, `arm64` on Apple Silicon), builds with `--load` into the local daemon, and tags the image using the latest git tag (e.g. `ghcr.io/sunishbharat/atlasmind-netra-mcp:v0.1.0`), falling back to `:dev` if no tags exist. To override, set `VERSION` before running:

```bash
# Linux / macOS / Git Bash
VERSION=v0.2.0-dev bash scripts/docker-build-local.sh
VERSION=latest bash scripts/docker-build-local.sh
```

```powershell
# Windows PowerShell
$env:VERSION = "v0.2.0-dev"; bash scripts/docker-build-local.sh
$env:VERSION = "latest"; bash scripts/docker-build-local.sh
```

Test it with:

```bash
docker run -p 8765:8765 --env-file .env \
  ghcr.io/sunishbharat/atlasmind-netra-mcp:latest
curl http://localhost:8765/health
```

### Release images (CI-built, multi-arch)

CI builds run on git tags - merges to `main` do not trigger a build.

#### Tag naming rules

| Tag format | Example | Images pushed to GHCR |
|---|---|---|
| Full release | `v0.1.0` | `:0.1.0`, `:0.1`, `:latest`, `:sha-<sha>` |
| Pre-release | `v0.1.0-rc.1` | `:0.1.0-rc.1`, `:sha-<sha>` only - `:latest` is not touched |

Use pre-release tags while validating the pipeline; switch to a full release tag when confident.

To publish a release image, push a version tag from the `main` branch:

```bash
git tag v0.1.0 -m "Initial release"
git push origin v0.1.0
```

GitHub Actions builds a single multi-arch manifest covering `linux/amd64` and `linux/arm64` and pushes these tags to GHCR:

- `ghcr.io/sunishbharat/atlasmind-netra-mcp:0.1.0`
- `ghcr.io/sunishbharat/atlasmind-netra-mcp:0.1`
- `ghcr.io/sunishbharat/atlasmind-netra-mcp:latest`  ← added automatically; do NOT push a tag named `latest`
- `ghcr.io/sunishbharat/atlasmind-netra-mcp:sha-<short-sha>`

Docker automatically selects the correct layer for the host architecture - no separate `-arm64` tag is needed. To pull the latest release:

```bash
docker pull ghcr.io/sunishbharat/atlasmind-netra-mcp:latest
docker run -p 8765:8765 --env-file .env \
  ghcr.io/sunishbharat/atlasmind-netra-mcp:latest
curl http://localhost:8765/health
```

To test CI without updating `:latest`, push a pre-release tag instead. `metadata-action` detects the `-rc.` suffix and suppresses `:latest` automatically:

```bash
git tag v0.1.1-rc.1 -m "Release candidate"
git push origin v0.1.1-rc.1
# pushes :0.1.1-rc.1 and :sha-<sha> only; :latest is not updated
```

To delete a bad tag before CI finishes (delete it both locally and on the remote, then cancel the running job in the GitHub Actions UI if it has already started):

```bash
git tag -d v0.1.0
git push origin :refs/tags/v0.1.0
```

### Build and push multi-platform (amd64 + arm64, manual CF deploy)

One-time buildx setup required before the first push:

```bash
docker buildx create --name multiarch --driver docker-container --use
docker buildx inspect --bootstrap
```

If `docker buildx inspect --bootstrap` fails with "context canceled" (e.g. after a Docker Desktop restart), remove and recreate the builder:

```bash
docker buildx rm multiarch
docker buildx create --name multiarch --driver docker-container --use
docker buildx inspect --bootstrap
```

Then build and push (replace with your registry):

```bash
export REGISTRY=ghcr.io/sunishbharat
./scripts/cf-deploy.sh   # builds both platforms, pushes to GHCR, then cf push
```

`--push` is mandatory for multi-platform builds; `--load` only works for single-arch images. OCI-only users can stop after the `docker buildx build` step - `cf push` at the end requires an active CF session and will fail without one.

### Cloud Foundry manifest

`manifest.yml.template` is the CF application descriptor. `cf-deploy.sh` substitutes the `DOCKER_IMAGE_TAG` placeholder with the actual image reference at deploy time and passes the result to `cf push` - you never edit the template for routine deploys.

If you need to inspect or customise the manifest before pushing - for example to scale instances or change memory - generate it manually:

```bash
export REGISTRY=ghcr.io/sunishbharat
GIT_SHA=$(git rev-parse --short HEAD)
IMAGE="${REGISTRY}/atlasmind-netra-mcp:${GIT_SHA}"
sed "s|DOCKER_IMAGE_TAG|${IMAGE}|g" manifest.yml.template > manifest.yml
# review or edit manifest.yml, then:
cf push atlasmind-netra-mcp -f manifest.yml
```

To deploy a specific CI-built release tag instead of the local SHA:

```bash
sed "s|DOCKER_IMAGE_TAG|ghcr.io/sunishbharat/atlasmind-netra-mcp:0.1.0|g" \
  manifest.yml.template > manifest.yml
cf push atlasmind-netra-mcp -f manifest.yml
```

Default resource settings in the template (edit `manifest.yml.template` to change permanently):

| Setting | Default | Notes |
|---|---|---|
| `memory` | `512M` | Increase if the server process grows under sustained load |
| `disk_quota` | `512M` | Covers Python env + log files |
| `instances` | `1` | Scale up only with `NETRA_SERVER__SESSION_BACKEND=valkey` - in-memory sessions do not survive across instances |
| `health-check-http-endpoint` | `/health` | Do not change - CF will not route traffic until this returns 200 |

The two Jira metadata URL lines in the template are commented out. Uncomment them when the static-file server (`netra-fields-server`) is deployed and set its route:

```yaml
NETRA_CLARIFICATION__JIRA_FIELDS_URL: https://<fields-server-route>/jira_fields.json
NETRA_CLARIFICATION__ALLOWED_VALUES_URL: https://<fields-server-route>/jira_allowed_values.json
```

### Required environment variables in CF / OCI

| Variable | Description |
|----------|-------------|
| `GROQ_API_KEY` | LLM API key (default provider) |
| `NETRA_LITE__BASE_URL` | AtlasMind backend URL, e.g. `https://atlasmind.de` |
| `NETRA_LITE__API_KEY` | X-API-Key for backend calls (recommended) |
| `NETRA_SERVER__API_KEY` | X-API-Key to protect the MCP HTTP endpoint (recommended) |
| `NETRA_SERVER__PUBLIC_URL` | Public URL of this server - enables `view_url` in tool responses |

Set secrets with `cf set-env atlasmind-netra-mcp <VAR> <value>` followed by `cf restage atlasmind-netra-mcp`.

### Connect Claude Desktop to the deployed server

```json
{
  "mcpServers": {
    "atlasmind-netra": {
      "transport": "http",
      "url": "https://netra.<CF_DOMAIN>/mcp",
      "headers": {
        "X-API-Key": "<NETRA_SERVER__API_KEY value>"
      }
    }
  }
}
```

Omit `headers` if `NETRA_SERVER__API_KEY` is not set.
