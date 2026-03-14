from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from src.features.gmail_bootstrap_state import GmailBootstrapSession
from src.gmail_bootstrap_web import (
    _extract_first_url,
    _render_session_html,
    _validate_credentials_json,
    build_google_auth_url,
    build_session_urls,
)


def test_build_session_urls_trims_trailing_slash() -> None:
    urls = build_session_urls(base_url="https://bot.example.com/", session_id="sess-1")

    assert urls == {
        "session_page_url": "https://bot.example.com/gmail/bootstrap/session/sess-1",
        "status_url": "https://bot.example.com/gmail/bootstrap/api/session/sess-1",
    }


def test_session_payload_shape_is_derived_from_session() -> None:
    session = GmailBootstrapSession(
        session_id="sess-1",
        created_at="2026-03-13T10:00:00+00:00",
        updated_at="2026-03-13T10:00:00+00:00",
        phase="cloud_auth_pending",
        project_id="ila-demo-project",
        project_name="ILA Demo Project",
        redirect_uri="https://bot.example.com/gmail/oauth/callback",
        callback_base_url="https://bot.example.com",
        oauth_client_name="ILA Gmail OAuth",
    )

    from src.gmail_bootstrap_web import _session_payload

    payload = _session_payload("https://bot.example.com", session)

    assert payload["session_id"] == "sess-1"
    assert payload["phase"] == "cloud_auth_pending"
    assert payload["phase_label"] == "Google sign-in needed"
    assert payload["connected"] is False
    assert payload["urls"]["status_url"].endswith("/gmail/bootstrap/api/session/sess-1")


def test_build_google_auth_url_contains_expected_redirect_and_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.config.GMAIL_BOOTSTRAP_GOOGLE_SCOPES",
        ("openid", "email", "https://www.googleapis.com/auth/cloud-platform"),
    )
    session = GmailBootstrapSession(
        session_id="sess-1",
        created_at="2026-03-13T10:00:00+00:00",
        updated_at="2026-03-13T10:00:00+00:00",
        phase="cloud_auth_pending",
        project_id="ila-demo-project",
        project_name="ILA Demo Project",
        redirect_uri="https://bot.example.com/gmail/oauth/callback",
        callback_base_url="https://bot.example.com",
        oauth_client_name="ILA Gmail OAuth",
    )

    url = build_google_auth_url(session=session, client_id="client-123")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.netloc == "accounts.google.com"
    assert query["client_id"] == ["client-123"]
    assert query["state"] == ["sess-1"]
    assert query["redirect_uri"] == ["https://bot.example.com/gmail/bootstrap/google/callback"]


def test_extract_first_url_returns_first_http_match() -> None:
    text = 'open https://accounts.google.com/o/oauth2/v2/auth?x=1 and ignore the rest'
    assert _extract_first_url(text) == "https://accounts.google.com/o/oauth2/v2/auth?x=1"


def test_validate_credentials_json_accepts_installed_payload() -> None:
    payload = _validate_credentials_json('{"installed":{"client_id":"abc"}}')
    assert payload["installed"]["client_id"] == "abc"


def test_render_session_html_includes_manual_checklist_and_upload_form(tmp_path) -> None:
    checklist_path = tmp_path / "MANUAL_CHECKLIST.md"
    checklist_path.write_text(
        "# Gmail self-hosted setup checklist for `ila-demo-project`\n\n"
        "## Manual Google Cloud Console checkpoint\n"
        "1. Open Google Cloud Console for the prepared project.\n",
        encoding="utf-8",
    )
    session = GmailBootstrapSession(
        session_id="sess-1",
        created_at="2026-03-13T10:00:00+00:00",
        updated_at="2026-03-13T10:00:00+00:00",
        phase="oauth_manual_pending",
        project_id="ila-demo-project",
        project_name="ILA Demo Project",
        redirect_uri="https://bot.example.com/gmail/oauth/callback",
        callback_base_url="https://bot.example.com",
        oauth_client_name="ILA Gmail OAuth",
        manual_checklist_path=str(checklist_path),
    )

    html = _render_session_html("https://bot.example.com", session)

    assert "Manual Checklist" in html
    assert "Upload Credentials and Continue" in html
    assert "Manual Google Cloud Console checkpoint" in html


def test_render_session_html_for_google_auth_failure_omits_upload_form() -> None:
    session = GmailBootstrapSession(
        session_id="sess-1",
        created_at="2026-03-13T10:00:00+00:00",
        updated_at="2026-03-13T10:00:00+00:00",
        phase="failed",
        project_id="ila-demo-project",
        project_name="ILA Demo Project",
        redirect_uri="https://bot.example.com/gmail/oauth/callback",
        callback_base_url="https://bot.example.com",
        oauth_client_name="ILA Gmail OAuth",
        failure_reason="google_auth_error:access_denied",
    )

    html = _render_session_html("https://bot.example.com", session)

    assert "Google sign-in was cancelled or denied" in html
    assert "Upload Credentials and Continue" not in html
