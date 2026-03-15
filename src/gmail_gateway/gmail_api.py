from __future__ import annotations

import base64
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from aiohttp import ClientSession, ClientTimeout


@dataclass(frozen=True)
class GmailApiError(Exception):
    status: int
    reason: str
    message: str
    retryable: bool


class GmailApiClient:
    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        self._timeout = ClientTimeout(total=timeout_seconds)

    @staticmethod
    def _build_raw_message(*, to: list[str], subject: str, body_text: str) -> str:
        message = EmailMessage()
        message["To"] = ", ".join(to)
        message["Subject"] = subject
        message.set_content(body_text)
        raw_bytes = message.as_bytes()
        return base64.urlsafe_b64encode(raw_bytes).decode("utf-8").rstrip("=")

    async def send_message(
        self,
        *,
        access_token: str,
        to: list[str],
        subject: str,
        body_text: str,
    ) -> str:
        payload = {"raw": self._build_raw_message(to=to, subject=subject, body_text=body_text)}
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        async with ClientSession(timeout=self._timeout) as session:
            async with session.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers=headers,
                json=payload,
            ) as resp:
                data: dict[str, Any] = await resp.json(content_type=None)
                if resp.status >= 400:
                    error = data.get("error", {}) if isinstance(data, dict) else {}
                    reason = "gmail_error"
                    details = error.get("errors") if isinstance(error, dict) else None
                    if isinstance(details, list) and details:
                        first = details[0]
                        if isinstance(first, dict) and first.get("reason"):
                            reason = str(first.get("reason"))
                    retryable = resp.status in {429, 500, 502, 503, 504}
                    raise GmailApiError(
                        status=resp.status,
                        reason=reason,
                        message=str(error.get("message") or "Gmail API request failed"),
                        retryable=retryable,
                    )
                message_id = str(data.get("id", "")).strip()
                if not message_id:
                    raise GmailApiError(
                        status=502,
                        reason="invalid_response",
                        message="Gmail API response missing message id",
                        retryable=True,
                    )
                return message_id
