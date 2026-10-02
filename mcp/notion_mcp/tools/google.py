"""Google tools — Gmail and Calendar.

Requires OAuth 2.0 setup (run once):
    uv run python -m notion_mcp.tools.google_auth

Then set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

from google.auth.transport.requests import Request
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from notion_mcp import mcp
from notion_mcp.tools.google_auth import get_credentials

# ---------------------------------------------------------------------------
# Reusable API clients (lazy-init, cached)
# ---------------------------------------------------------------------------

_GMAIL_SERVICE: Resource | None = None
_CALENDAR_SERVICE: Resource | None = None


def _get_gmail_service() -> Resource:
    global _GMAIL_SERVICE
    if _GMAIL_SERVICE is None:
        creds = get_credentials()
        if creds.expired:
            creds.refresh(Request())
        _GMAIL_SERVICE = build("gmail", "v1", credentials=creds)
    return _GMAIL_SERVICE


def _get_calendar_service() -> Resource:
    global _CALENDAR_SERVICE
    if _CALENDAR_SERVICE is None:
        creds = get_credentials()
        if creds.expired:
            creds.refresh(Request())
        _CALENDAR_SERVICE = build("calendar", "v3", credentials=creds)
    return _CALENDAR_SERVICE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _google_error(exc: HttpError) -> dict[str, Any]:
    resp = exc.resp
    try:
        details = exc.reason or str(exc)
    except Exception:
        details = str(exc)
    return {
        "success": False,
        "error": details,
        "status_code": resp.status if resp else None,
    }


def _format_message(msg: dict[str, Any]) -> dict[str, Any]:
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    body = ""
    payload = msg.get("payload", {})
    if "body" in payload and payload["body"].get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    else:
        parts = payload.get("parts", [])
        for p in parts:
            if p.get("mimeType") == "text/plain" and p.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(p["body"]["data"]).decode("utf-8", errors="replace")
                break
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId", ""),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "snippet": msg.get("snippet", ""),
        "body": body,
        "label_ids": msg.get("labelIds", []),
    }


# ---------------------------------------------------------------------------
# Gmail tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def gmail_list_messages(
    query: str = "",
    max_results: int = 20,
) -> dict[str, Any]:
    """Search Gmail inbox for messages matching a query.

    Args:
        query: Gmail search query (e.g. ``from:alice@example.com has:attachment``).
               Leave empty to list recent messages.
        max_results: Max messages to return (default 20, max 100).

    Returns:
        A dict with ``messages`` list containing id, subject, from, date, snippet.
    """
    try:
        service = _get_gmail_service()
        response = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=min(max_results, 100),
        ).execute()

        raw_messages = response.get("messages", [])
        results = []
        for m in raw_messages[:max_results]:
            msg = service.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute()
            results.append(_format_message(msg))

        return {"success": True, "count": len(results), "messages": results}
    except HttpError as exc:
        return _google_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
async def gmail_get_message(
    message_id: str,
) -> dict[str, Any]:
    """Retrieve the full content of a Gmail message by ID.

    Args:
        message_id: The Gmail message ID (from ``gmail_list_messages``).

    Returns:
        A dict with id, thread_id, from, to, subject, date, body, label_ids.
    """
    try:
        service = _get_gmail_service()
        msg = service.users().messages().get(
            userId="me", id=message_id, format="full",
        ).execute()
        result = _format_message(msg)
        result["success"] = True
        return result
    except HttpError as exc:
        return _google_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
async def gmail_send_message(
    to: str,
    subject: str,
    body: str,
) -> dict[str, Any]:
    """Send an email via Gmail.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain-text email body.

    Returns:
        A dict with ``id``, ``thread_id``, and ``label_ids`` on success.
    """
    try:
        service = _get_gmail_service()
        msg = EmailMessage()
        msg.set_content(body)
        msg["To"] = to
        msg["Subject"] = subject
        msg["From"] = "me"

        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        body_payload = {"raw": encoded}

        sent = service.users().messages().send(userId="me", body=body_payload).execute()
        return {
            "success": True,
            "id": sent["id"],
            "thread_id": sent.get("threadId", ""),
            "label_ids": sent.get("labelIds", []),
            "message": "Email sent successfully.",
        }
    except HttpError as exc:
        return _google_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
async def gmail_list_labels() -> dict[str, Any]:
    """List all Gmail labels for the authenticated account.

    Returns:
        A dict with ``labels`` list containing id, name, type, message count.
    """
    try:
        service = _get_gmail_service()
        response = service.users().labels().list(userId="me").execute()
        labels = []
        for lbl in response.get("labels", []):
            labels.append({
                "id": lbl["id"],
                "name": lbl["name"],
                "type": lbl.get("type", ""),
                "messages_total": lbl.get("messagesTotal", 0),
                "messages_unread": lbl.get("messagesUnread", 0),
            })
        return {"success": True, "count": len(labels), "labels": labels}
    except HttpError as exc:
        return _google_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
async def gmail_list_drafts(max_results: int = 20) -> dict[str, Any]:
    """List Gmail drafts.

    Args:
        max_results: Max drafts to return (default 20, max 100).

    Returns:
        A dict with ``drafts`` list containing id, subject, snippet.
    """
    try:
        service = _get_gmail_service()
        response = service.users().drafts().list(
            userId="me",
            maxResults=min(max_results, 100),
        ).execute()

        drafts = []
        for d in response.get("drafts", []):
            msg = service.users().drafts().get(
                userId="me", id=d["id"], format="metadata",
                metadataHeaders=["Subject"],
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("message", {}).get("payload", {}).get("headers", [])}
            drafts.append({
                "id": d["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "snippet": msg.get("message", {}).get("snippet", ""),
            })

        return {"success": True, "count": len(drafts), "drafts": drafts}
    except HttpError as exc:
        return _google_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Calendar tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def calendar_list_events(
    max_results: int = 20,
    time_min: str | None = None,
    time_max: str | None = None,
) -> dict[str, Any]:
    """List upcoming events from the user's primary Google Calendar.

    Args:
        max_results: Max events to return (default 20, max 100).
        time_min: RFC3339 timestamp for the start of the range
                  (default: now). Example: ``2026-06-08T00:00:00Z``.
        time_max: RFC3339 timestamp for the end of the range.

    Returns:
        A dict with ``events`` list containing id, summary, start, end,
        location, description, and attendees.
    """
    try:
        service = _get_calendar_service()
        now = datetime.now(timezone.utc)

        params: dict[str, Any] = {
            "calendarId": "primary",
            "maxResults": min(max_results, 100),
            "orderBy": "startTime",
            "singleEvents": True,
        }
        if time_min:
            params["timeMin"] = time_min
        else:
            params["timeMin"] = now.isoformat()
        if time_max:
            params["timeMax"] = time_max

        events_result = service.events().list(**params).execute()
        events = []
        for ev in events_result.get("items", []):
            events.append({
                "id": ev["id"],
                "summary": ev.get("summary", "(no title)"),
                "description": ev.get("description", ""),
                "location": ev.get("location", ""),
                "start": ev["start"].get("dateTime", ev["start"].get("date", "")),
                "end": ev["end"].get("dateTime", ev["end"].get("date", "")),
                "attendees": [
                    {"email": a.get("email"), "response_status": a.get("responseStatus")}
                    for a in ev.get("attendees", [])
                ],
                "status": ev.get("status", ""),
            })

        return {"success": True, "count": len(events), "events": events}
    except HttpError as exc:
        return _google_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
async def calendar_create_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    attendees: list[str] | None = None,
    timezone: str = "UTC",
) -> dict[str, Any]:
    """Create a new event in the user's primary Google Calendar.

    Args:
        summary: Event title.
        start_time: Start time in RFC3339 format (e.g. ``2026-06-10T14:00:00Z``).
        end_time: End time in RFC3339 format (e.g. ``2026-06-10T15:00:00Z``).
        description: Optional event description.
        location: Optional event location.
        attendees: Optional list of attendee email addresses.
        timezone: IANA timezone string (default: UTC).

    Returns:
        A dict with the created event's id, summary, start, end, and htmlLink.
    """
    try:
        service = _get_calendar_service()
        event_body: dict[str, Any] = {
            "summary": summary,
            "description": description or "",
            "location": location or "",
            "start": {"dateTime": start_time, "timeZone": timezone},
            "end": {"dateTime": end_time, "timeZone": timezone},
        }
        if attendees:
            event_body["attendees"] = [{"email": a} for a in attendees]

        created = service.events().insert(
            calendarId="primary",
            body=event_body,
            sendUpdates="all" if attendees else "none",
        ).execute()

        return {
            "success": True,
            "id": created["id"],
            "summary": created.get("summary", ""),
            "start": created["start"].get("dateTime", created["start"].get("date", "")),
            "end": created["end"].get("dateTime", created["end"].get("date", "")),
            "html_link": created.get("htmlLink", ""),
            "message": "Event created successfully.",
        }
    except HttpError as exc:
        return _google_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
async def calendar_get_event(event_id: str) -> dict[str, Any]:
    """Get a specific Google Calendar event by ID.

    Args:
        event_id: The event ID (from ``calendar_list_events``).

    Returns:
        A dict with event details: summary, description, start, end,
        location, attendees, status.
    """
    try:
        service = _get_calendar_service()
        ev = service.events().get(calendarId="primary", eventId=event_id).execute()
        return {
            "success": True,
            "id": ev["id"],
            "summary": ev.get("summary", ""),
            "description": ev.get("description", ""),
            "location": ev.get("location", ""),
            "start": ev["start"].get("dateTime", ev["start"].get("date", "")),
            "end": ev["end"].get("dateTime", ev["end"].get("date", "")),
            "attendees": [
                {"email": a.get("email"), "response_status": a.get("responseStatus")}
                for a in ev.get("attendees", [])
            ],
            "status": ev.get("status", ""),
            "html_link": ev.get("htmlLink", ""),
            "created": ev.get("created", ""),
            "updated": ev.get("updated", ""),
        }
    except HttpError as exc:
        return _google_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
async def calendar_list_calendars() -> dict[str, Any]:
    """List all calendars the authenticated user can access.

    Returns:
        A dict with ``calendars`` list containing id, summary, description,
        and primary status.
    """
    try:
        service = _get_calendar_service()
        response = service.calendarList().list().execute()
        calendars = []
        for cal in response.get("items", []):
            calendars.append({
                "id": cal["id"],
                "summary": cal.get("summary", ""),
                "description": cal.get("description", ""),
                "primary": cal.get("primary", False),
                "access_role": cal.get("accessRole", ""),
            })
        return {"success": True, "count": len(calendars), "calendars": calendars}
    except HttpError as exc:
        return _google_error(exc)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
