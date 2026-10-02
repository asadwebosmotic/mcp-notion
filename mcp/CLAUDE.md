# Notion + Slack + Google MCP Server (Python)
MCP server exposing Notion API, Slack API, and Google APIs (Gmail + Calendar) as AI-accessible tools.
## Stack
- Python 3.11+, uv, mcp SDK (FastMCP)
- notion-client (official Notion SDK)
- slack-sdk (official Slack SDK)
- google-api-python-client (Gmail + Calendar SDK)
- pydantic for validation
## Commands
uv run notion-mcp-server          # start server (stdio)
uv run notion-mcp-server --http   # start server (streamable-http)
uv run pytest                     # run tests
uv add <pkg>                      # add dependency
## Google OAuth Setup (first time)
uv run python -m notion_mcp.tools.google_auth
## Project Structure
src/notion_mcp/
  mcp_server.py       # FastMCP init, lifecycle
  __init__.py     # entry
  notion/
    mcp_client.py     # NotionClient wrapper
  tools/
    pages.py      # search, create, retrieve, update, archive, move
    blocks.py     # retrieve_block_children, append_block_children
    databases.py  # retrieve_database, query/create data sources
    comments.py   # list, create comments
    users.py      # list, retrieve users
    slack.py      # list channels, read history, search, post messages, reply to threads
    google_auth.py  # Google OAuth2 token helper (run standalone for setup)
    google.py     # Gmail (list, read, send) + Calendar (list, create, get events)
  models.py       # pydantic schemas
## Conventions
- One tool per function, decorated with @mcp.tool()
- Use async for any I/O-bound tools
- NOTION_TOKEN env var for Notion auth
- SLACK_BOT_TOKEN env var for Slack auth (xoxb-...)
- GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET env vars for Google OAuth
- Slack tools prefixed with `slack_`, Gmail with `gmail_`, Calendar with `calendar_`
- All API responses: typed with pydantic
- Tests: pytest, co-located in tests/
## Key Decisions
- stdio transport default (--http for streamable)
- Notion API version 2025-09-03 (Data Source Edition)
- FastMCP high-level API (not low-level Server)
- Google auth uses OAuth 2.0 desktop flow with local token file
