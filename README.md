# AtlasMind-Netra-mcp
AtlasMind-Netra-mcp is the AI agent and MCP server layer of the AtlasMind platform. It sits between any MCP-compatible client (Claude Desktop, Cursor, or a custom UI) and the `atlasMind-Lite` JQL execution backend, running a multi-turn clarification loop before dispatching any query to Jira.

Jira queries written by an LLM routinely fail or return wrong results because natural language is ambiguous — "escalation" could be a label, a custom field, or a priority value; "today" could mean created, updated, or due today; "my team" has no JQL equivalent. This agent fixes that by detecting ambiguous terms, asking one targeted clarifying question using real field names from the live Jira instance, learning team conventions so the same question is never asked twice, and only calling `atlasMind-Lite` once the intent is fully resolved.

Three MCP tools are exposed publicly: `query_jira`, `get_report`, and `get_jira_context`. Everything else — the clarification loop, session store, intent classifier, and report synthesiser — is internal.
