from __future__ import annotations

from typing import Any, Callable


def finalize_turn_metrics_and_sessions(
    *,
    final_response: Any,
    provider: Any,
    session: Any,
    chat_id: int,
    thread_id: int | None,
    session_manager: Any,
    is_codex_family_cli_fn: Callable[[str | None], bool],
    metrics: Any,
    scope_key: str,
    final_provider_name: str,
    final_model_name: str,
    state: Any,
    response_has_user_content: bool,
    observed_tools: list[str],
    raw_prompt: str,
    output_size_out: int,
    step_plan_active: bool,
    steering_events_applied: int,
    provider_attempts: int,
) -> None:
    if (
        final_response
        and not is_codex_family_cli_fn(provider.cli)
        and final_response.session_id
        and final_response.session_id != session.claude_session_id
    ):
        session_manager.update_session_id(chat_id, final_response.session_id, thread_id)
    if (
        final_response
        and is_codex_family_cli_fn(provider.cli)
        and final_response.session_id
        and final_response.session_id != session.codex_session_id
    ):
        session_manager.update_codex_session_id(chat_id, final_response.session_id, thread_id)

    if final_response:
        status = "error" if final_response.is_error else "success"
        if state.cancel_requested:
            status = "cancelled"
        metrics.MESSAGES_TOTAL.labels(status=status).inc()
    metrics.observe_cost_intelligence_turn(
        scope_key=scope_key,
        provider=final_provider_name,
        model=final_model_name,
        mode="foreground",
        cost_usd=float(final_response.cost_usd) if final_response else 0.0,
        num_turns=int(final_response.num_turns) if final_response else 0,
        duration_ms=float(final_response.duration_ms) if final_response else 0.0,
        is_error=bool(final_response.is_error) if final_response else True,
        is_cancelled=state.cancel_requested,
        is_empty_response=(
            not response_has_user_content
            if (final_response and not final_response.is_error and not state.cancel_requested)
            else not bool((final_response.text or "").strip()) if final_response else True
        ),
        tool_timeout=bool(final_response.idle_timeout) if final_response else False,
        tool_names=observed_tools,
        message_size_in=len(raw_prompt),
        message_size_out=output_size_out,
        step_plan_active=step_plan_active,
        steering_event_count=steering_events_applied,
        attempts=max(1, provider_attempts),
    )
