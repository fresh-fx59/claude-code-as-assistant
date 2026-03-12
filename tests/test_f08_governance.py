import asyncio
from unittest.mock import patch

from src.f08_governance import F08GovernanceAdvisory


async def _flush_tasks() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def test_chat_turn_advisory_records_success() -> None:
    advisory = F08GovernanceAdvisory()

    async def run() -> None:
        with patch("src.f08_governance.metrics.observe_f08_governance_event") as observe_mock:
            advisory.submit_chat_turn(scope_key="chat:1:thread:none", prompt="hello")
            await _flush_tasks()
            observe_mock.assert_called()
            kwargs = observe_mock.call_args.kwargs
            assert kwargs["event"] == "chat_turn_advisory"
            assert kwargs["status"] == "success"
            assert kwargs["decision"] == "advisory"

    asyncio.run(run())


def test_chat_turn_advisory_warns_on_risky_marker() -> None:
    advisory = F08GovernanceAdvisory()

    async def run() -> None:
        with patch("src.f08_governance.metrics.observe_f08_governance_event") as observe_mock:
            advisory.submit_chat_turn(scope_key="chat:2:thread:none", prompt="please run git reset --hard now")
            await _flush_tasks()
            kwargs = observe_mock.call_args.kwargs
            assert kwargs["event"] == "chat_turn_advisory"
            assert kwargs["status"] == "warn"

    asyncio.run(run())


def test_selfmod_apply_advisory_warns_on_custom_target() -> None:
    advisory = F08GovernanceAdvisory()

    async def run() -> None:
        with patch("src.f08_governance.metrics.observe_f08_governance_event") as observe_mock:
            advisory.submit_selfmod_apply(
                scope_key="chat:3:thread:none",
                relative_path="tools_plugin.py",
                test_target="tests/test_all.py",
            )
            await _flush_tasks()
            kwargs = observe_mock.call_args.kwargs
            assert kwargs["event"] == "selfmod_apply_advisory"
            assert kwargs["status"] == "warn"

    asyncio.run(run())
