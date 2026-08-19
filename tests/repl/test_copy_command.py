"""Tests for the native TUI's ``/copy`` clipboard command."""

from __future__ import annotations

from omnigent_ui_sdk import RichBlockFormatter
from rich.text import Text

from omnigent.repl._repl import COMMANDS, handle_slash_command


class _Session:
    def __init__(self, text: str = "") -> None:
        self._last_assistant_text = text


class _Host:
    def __init__(self, *, copy_result: bool = True) -> None:
        self.copy_result = copy_result
        self.copied: list[str] = []
        self.outputs: list[Text] = []

    def copy_to_clipboard(self, text: str) -> bool:
        self.copied.append(text)
        return self.copy_result

    def output(self, item: Text) -> None:
        self.outputs.append(item)


def test_copy_command_is_discoverable() -> None:
    assert "/copy" in COMMANDS
    assert "latest assistant response" in COMMANDS["/copy"][0].lower()


async def test_copy_command_copies_latest_assistant_text() -> None:
    host = _Host()
    await handle_slash_command(
        "/copy",
        _Session("  response text  "),
        None,  # type: ignore[arg-type]
        host,  # type: ignore[arg-type]
        RichBlockFormatter(),
    )

    assert host.copied == ["response text"]
    assert "Copied latest response" in host.outputs[-1].plain


async def test_copy_command_preserves_clipboard_when_no_response() -> None:
    host = _Host()
    await handle_slash_command(
        "/copy",
        _Session(),
        None,  # type: ignore[arg-type]
        host,  # type: ignore[arg-type]
        RichBlockFormatter(),
    )

    assert host.copied == []
    assert "Nothing to copy" in host.outputs[-1].plain


async def test_copy_command_reports_terminal_rejection() -> None:
    host = _Host(copy_result=False)
    await handle_slash_command(
        "/copy",
        _Session("response text"),
        None,  # type: ignore[arg-type]
        host,  # type: ignore[arg-type]
        RichBlockFormatter(),
    )

    assert host.copied == ["response text"]
    assert "copy failed" in host.outputs[-1].plain
