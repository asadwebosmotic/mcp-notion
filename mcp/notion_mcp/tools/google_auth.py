"""Google OAuth 2.0 helper — manages token file for Gmail + Calendar APIs.

First-time setup (run once from the project root):
    uv run python -m notion_mcp.tools.google_auth

This opens a browser for you to authorise the app.
The resulting token file is saved alongside this file.

Credentials source priority:
  1. GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET env vars
  2. credentials.json file (Google Cloud Desktop App OAuth download)
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

from dotenv import load_dotenv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

_TOOLS_DIR = pathlib.Path(__file__).resolve().parent
_TOKEN_PATH = _TOOLS_DIR / "google_token.json"
_CREDENTIALS_FILE = _TOOLS_DIR / "credentials.json"

# Scopes needed for Gmail and Calendar
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar",
]

_SCOPE_LABELS = {
    "gmail.readonly": "Read Gmail",
    "gmail.send": "Send Gmail",
    "gmail.labels": "List Gmail labels",
    "gmail.modify": "Modify Gmail",
    "calendar.readonly": "Read Calendar",
    "calendar.events": "Manage Calendar events",
    "calendar": "Full Calendar access",
}


def _client_config() -> dict[str, Any]:
    """Return the Google OAuth client config from env vars or credentials.json."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if client_id and client_secret:
        return {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }

    if _CREDENTIALS_FILE.exists():
        with open(_CREDENTIALS_FILE) as f:
            return json.load(f)

    raise ValueError(
        "No Google OAuth credentials found.\n\n"
        "Option 1 — Set env vars in .env:\n"
        "  GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com\n"
        "  GOOGLE_CLIENT_SECRET=GOCSPX-...\n\n"
        "Option 2 — Place credentials.json in:\n"
        f"  {_CREDENTIALS_FILE}\n"
        "  (Download from https://console.cloud.google.com/apis/credentials)"
    )


def _missing_scopes(creds: Credentials) -> list[str]:
    """Return which required scopes are missing from the token."""
    if not creds.scopes:
        return SCOPES
    granted = set(creds.scopes)
    return [s for s in SCOPES if s not in granted]


def get_credentials() -> Credentials:
    """Load existing credentials or refresh them; raise if no token file."""
    load_dotenv()
    creds = None

    # Try the project's token path first, then standard token.json
    for path in (_TOKEN_PATH, _TOOLS_DIR / "token.json"):
        if path.exists():
            creds = Credentials.from_authorized_user_file(str(path), SCOPES)
            break

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds or not creds.valid:
        raise ValueError(
            f"No valid Google token found.\n\n"
            "Run the setup script to authorize:\n"
            "    uv run python -m notion_mcp.tools.google_auth"
        )

    # Warn about missing scopes
    missing = _missing_scopes(creds)
    if missing:
        labels = "\n".join(f"  - {_SCOPE_LABELS.get(s.split('/')[-1], s)} ({s})" for s in missing)
        print(
            f"⚠️  Token missing {len(missing)} scope(s):\n{labels}\n"
            "Re-run the setup script to grant full access:\n"
            "    uv run python -m notion_mcp.tools.google_auth\n"
        )

    return creds


def _run_setup() -> None:
    """Interactive OAuth flow — saves google_token.json for later use."""
    load_dotenv()
    print("Starting Google OAuth 2.0 setup...")
    print(f"Requesting {len(SCOPES)} scopes:\n")
    for s in SCOPES:
        label = _SCOPE_LABELS.get(s.split("/")[-1], s)
        print(f"  • {label}")
    print()

    config = _client_config()
    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    with open(_TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"\n✅ Token saved to {_TOKEN_PATH}")
    print("You can now use Gmail and Calendar tools.")


if __name__ == "__main__":
    _run_setup()
