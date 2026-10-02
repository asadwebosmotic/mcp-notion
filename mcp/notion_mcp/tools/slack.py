"""Slack tools — read channels, search messages, list users, and post messages.

Requires the SLACK_BOT_TOKEN environment variable (xoxb-...) with these scopes:
  Read:  channels:history, channels:read, groups:history, users:read, search:read
  Join:  channels:join (needed to auto-join public channels before reading)
  Write: chat:write, chat:write.public
"""

from __future__ import annotations

import os
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from notion_mcp import mcp


# ---------------------------------------------------------------------------
# Slack client helper
# ---------------------------------------------------------------------------

def _get_slack_client() -> WebClient:
    """Return a Slack WebClient initialised with the bot token."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise ValueError(
            "SLACK_BOT_TOKEN environment variable is required.\n"
            "1. Create a Slack App at https://api.slack.com/apps\n"
            "2. Add the required OAuth scopes\n"
            "3. Install the app to your workspace\n"
            "4. Copy the Bot User OAuth Token (xoxb-...)"
        )
    return WebClient(token=token)


def _slack_error(exc: SlackApiError) -> dict[str, Any]:
    """Return a helpful, user-facing Slack error payload."""
    error = exc.response.get("error", str(exc))
    needed = exc.response.get("needed")
    provided = exc.response.get("provided")
    payload: dict[str, Any] = {"success": False, "error": error}
    if needed:
        payload["needed_scope"] = needed
    if provided:
        payload["provided_scopes"] = provided
    return payload


def _resolve_channel(client: WebClient, channel: str) -> dict[str, Any]:
    """Resolve a channel ID or name to Slack channel metadata when possible."""
    cleaned = channel.strip()
    if cleaned.startswith("#"):
        cleaned = cleaned[1:]

    if cleaned.startswith(("C", "G")):
        try:
            info = client.conversations_info(channel=cleaned).get("channel", {})
            if info:
                return {
                    "id": info.get("id", cleaned),
                    "name": info.get("name", cleaned),
                    "is_private": info.get("is_private", cleaned.startswith("G")),
                    "is_member": info.get("is_member"),
                }
        except SlackApiError:
            return {"id": cleaned, "name": cleaned, "is_private": cleaned.startswith("G")}

    cursor: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "types": "public_channel,private_channel",
            "limit": 1000,
            "exclude_archived": True,
        }
        if cursor:
            kwargs["cursor"] = cursor
        response = client.conversations_list(**kwargs)
        for item in response.get("channels", []):
            if item.get("name") == cleaned or item.get("id") == cleaned:
                return {
                    "id": item["id"],
                    "name": item.get("name", item["id"]),
                    "is_private": item.get("is_private", False),
                    "is_member": item.get("is_member"),
                }

        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    return {"id": channel, "name": channel, "is_private": channel.startswith("G")}


def _read_history(client: WebClient, channel_id: str, limit: int) -> dict[str, Any]:
    """Read channel history and normalize Slack's response shape."""
    response = client.conversations_history(
        channel=channel_id,
        limit=min(limit, 100),
    )
    messages = []
    for msg in response.get("messages", []):
        messages.append({
            "user": msg.get("user", "unknown"),
            "text": msg.get("text", ""),
            "ts": msg.get("ts", ""),
            "type": msg.get("subtype", "message"),
        })
    return {"success": True, "count": len(messages), "messages": messages}


# ---------------------------------------------------------------------------
# Reading tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def slack_list_channels(
    limit: int = 100,
    include_private: bool = False,
) -> dict[str, Any]:
    """List Slack channels the bot can see.

    Uses the ``channels:read`` scope. Set *include_private* to ``True`` to
    also include private channels the bot has been invited to (requires
    ``groups:read``).

    Args:
        limit: Maximum number of channels to return (default 100, max 1000).
        include_private: If True, include private channels (groups) as well.

    Returns:
        A dict with ``channels`` list containing id, name, topic, purpose,
        and member count for each channel.
    """
    try:
        client = _get_slack_client()
        types = "public_channel,private_channel" if include_private else "public_channel"
        response = client.conversations_list(
            types=types,
            limit=min(limit, 1000),
            exclude_archived=True,
        )
        channels = []
        for ch in response.get("channels", []):
            channels.append({
                "id": ch["id"],
                "name": ch.get("name", ""),
                "is_private": ch.get("is_private", False),
                "is_member": ch.get("is_member"),
                "topic": ch.get("topic", {}).get("value", ""),
                "purpose": ch.get("purpose", {}).get("value", ""),
                "num_members": ch.get("num_members", 0),
            })
        return {"success": True, "count": len(channels), "channels": channels}
    except SlackApiError as exc:
        return _slack_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
async def slack_read_channel_history(
    channel: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Read recent messages from a Slack channel.

    Uses the ``channels:history`` scope (or ``groups:history`` for private
    channels). If the bot is not a member of a public channel, this tool
    attempts to join first, which requires ``channels:join``. Private
    channels still require manually inviting the bot.

    Args:
        channel: Channel ID (e.g. ``C12345``) or name (e.g. ``#general``).
        limit: Number of messages to retrieve (default 20, max 100).

    Returns:
        A dict with ``messages`` list, each containing ``user``, ``text``,
        and ``ts`` (timestamp).
    """
    try:
        client = _get_slack_client()
        channel_info = _resolve_channel(client, channel)
        channel_id = channel_info["id"]

        try:
            return _read_history(client, channel_id, limit)
        except SlackApiError as exc:
            if exc.response.get("error") != "not_in_channel":
                raise

            if channel_info.get("is_private"):
                return {
                    "success": False,
                    "error": "not_in_channel",
                    "message": (
                        "The bot is not a member of this private channel. "
                        "Invite the bot to the channel, then retry."
                    ),
                }

            try:
                client.conversations_join(channel=channel_id)
            except SlackApiError as join_exc:
                join_error = _slack_error(join_exc)
                if join_error.get("error") == "missing_scope":
                    join_error["message"] = (
                        "The bot can see this public channel but cannot join it. "
                        "Add the `channels:join` bot scope in Slack, reinstall "
                        "the app to the workspace, then retry."
                    )
                return join_error

            history = _read_history(client, channel_id, limit)
            history["joined_channel"] = True
            history["channel"] = {
                "id": channel_id,
                "name": channel_info.get("name", channel_id),
            }
            return history
    except SlackApiError as exc:
        return _slack_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
async def slack_list_users(limit: int = 100) -> dict[str, Any]:
    """List users in the Slack workspace.

    Uses the ``users:read`` scope.

    Args:
        limit: Maximum number of users to return (default 100).

    Returns:
        A dict with ``users`` list containing id, name, real_name, email,
        and online status for each user.
    """
    try:
        client = _get_slack_client()
        response = client.users_list(limit=min(limit, 1000))
        users = []
        for u in response.get("members", []):
            if u.get("deleted", False):
                continue
            users.append({
                "id": u["id"],
                "name": u.get("name", ""),
                "real_name": u.get("real_name", ""),
                "display_name": u.get("profile", {}).get("display_name", ""),
                "email": u.get("profile", {}).get("email", ""),
                "is_bot": u.get("is_bot", False),
            })
        return {"success": True, "count": len(users), "users": users}
    except SlackApiError as exc:
        return _slack_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
async def slack_search_messages(
    query: str,
    count: int = 10,
) -> dict[str, Any]:
    """Search for messages across the Slack workspace.

    Uses the ``search:read`` scope.  Note: this scope requires a *user*
    token (``xoxp-``) — if you are using a bot token (``xoxb-``) this
    will return a ``not_allowed_token_type`` error.  In that case, use
    ``slack_read_channel_history`` on specific channels instead.

    Args:
        query: The search query string (supports Slack search operators).
        count: Number of results to return (default 10, max 100).

    Returns:
        A dict with ``messages`` list of matching messages.
    """
    try:
        client = _get_slack_client()
        response = client.search_messages(
            query=query,
            count=min(count, 100),
            sort="timestamp",
            sort_dir="desc",
        )
        matches = response.get("messages", {}).get("matches", [])
        results = []
        for m in matches:
            results.append({
                "text": m.get("text", ""),
                "user": m.get("username", m.get("user", "unknown")),
                "channel_id": m.get("channel", {}).get("id", ""),
                "channel_name": m.get("channel", {}).get("name", ""),
                "ts": m.get("ts", ""),
                "permalink": m.get("permalink", ""),
            })
        return {"success": True, "count": len(results), "messages": results}
    except SlackApiError as exc:
        return _slack_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Writing tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def slack_post_message(
    channel: str,
    text: str,
) -> dict[str, Any]:
    """Post a message to a Slack channel.

    Uses the ``chat:write`` scope.  For public channels the bot hasn't
    joined, the ``chat:write.public`` scope lets it post without joining.

    Args:
        channel: Channel ID (e.g. ``C12345``) or channel name (e.g. ``#general``).
        text: The message text to post (supports Slack mrkdwn formatting).

    Returns:
        A dict with ``ts`` (message timestamp / ID) and ``channel`` on
        success, or an ``error`` key on failure.
    """
    try:
        client = _get_slack_client()
        response = client.chat_postMessage(channel=channel, text=text)
        return {
            "success": True,
            "channel": response["channel"],
            "ts": response["ts"],
            "message": "Message posted successfully.",
        }
    except SlackApiError as exc:
        return _slack_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
async def slack_reply_to_thread(
    channel: str,
    thread_ts: str,
    text: str,
) -> dict[str, Any]:
    """Reply to an existing Slack message thread.

    Uses the ``chat:write`` scope.

    Args:
        channel: Channel ID where the thread lives.
        thread_ts: The ``ts`` (timestamp) of the parent message to reply to.
        text: The reply text (supports Slack mrkdwn formatting).

    Returns:
        A dict with ``ts`` of the reply and ``channel`` on success.
    """
    try:
        client = _get_slack_client()
        response = client.chat_postMessage(
            channel=channel,
            text=text,
            thread_ts=thread_ts,
        )
        return {
            "success": True,
            "channel": response["channel"],
            "ts": response["ts"],
            "message": "Reply posted successfully.",
        }
    except SlackApiError as exc:
        return _slack_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
