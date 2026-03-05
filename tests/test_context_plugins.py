"""Tests for core/plugin context composition."""

from pathlib import Path

from src.core.context_plugins import ContextPluginRegistry
from src.plugins.tools_plugin import ToolRegistry as PluginToolRegistry
from src.tools import ToolRegistry as ShimToolRegistry


class _StaticPlugin:
    def __init__(self, name: str, block: str) -> None:
        self.name = name
        self._block = block

    def build_context(self, user_message: str) -> str:
        return self._block

    def format_for_display(self) -> str:
        return self.name


def test_context_plugin_registry_combines_non_empty_blocks() -> None:
    registry = ContextPluginRegistry(
        [
            _StaticPlugin("a", "<a>one</a>"),
            _StaticPlugin("b", " "),
            _StaticPlugin("c", "<c>two</c>"),
        ]
    )
    context = registry.build_context("hello")
    assert context == "<a>one</a>\n\n<c>two</c>"


def test_tools_plugin_loads_yaml_and_matches_triggers(tmp_path: Path) -> None:
    (tmp_path / "web.yaml").write_text(
        "\n".join(
            [
                "name: web_search",
                "description: Search web",
                "triggers: [search, latest]",
                "instructions: |",
                "  Use web search.",
            ]
        )
    )
    registry = PluginToolRegistry(tmp_path)
    context = registry.build_context("search latest exchange rates")

    assert "<tools>" in context
    assert "- web_search: Search web" in context
    assert '<tool name="web_search">' in context
    assert "Use web search." in context


def test_tools_module_is_compatibility_shim(tmp_path: Path) -> None:
    (tmp_path / "sample.yaml").write_text("name: sample\ndescription: Sample\ntriggers: [x]")
    shim_registry = ShimToolRegistry(tmp_path)
    plugin_registry = PluginToolRegistry(tmp_path)

    assert type(shim_registry) is type(plugin_registry)


def test_tools_plugin_explicit_use_tool_directive(tmp_path: Path) -> None:
    (tmp_path / "web.yaml").write_text(
        "\n".join(
            [
                "name: web_search",
                "description: Search web",
                "triggers: [search]",
                "instructions: |",
                "  Use web search.",
            ]
        )
    )
    registry = PluginToolRegistry(tmp_path)
    context = registry.build_context("Please decide.\nUSE_TOOL: web_search")

    assert '<tool name="web_search">' in context
    assert "If a tool is needed, respond with exactly: USE_TOOL: <tool_name>." in context


def test_extract_requested_tools_deduplicates_and_normalizes() -> None:
    requested = PluginToolRegistry.extract_requested_tools(
        "USE_TOOL: Web_Search\nnoise\nUSE_TOOL: web_search\nUSE_TOOL: github_pr"
    )
    assert requested == ["web_search", "github_pr"]
