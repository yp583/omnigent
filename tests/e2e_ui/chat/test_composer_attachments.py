"""E2E: attaching and removing files in the chat composer.

The composer (``pages/ChatPage.tsx``) lets the user attach files via the
paperclip button (which clicks a hidden ``<input type="file">``), paste, or
drag-drop. Each attached file renders as a chip below the textarea with a
per-file remove button; on send each file is POSTed to
``/v1/sessions/{id}/resources/files`` and the message references the returned
``file_id``, and ``removeFile`` drops a chip.

The new-chat landing screen (``shell/NewChatDialog.tsx``) has its own composer
with the same affordances, and it validates attachments the same way — that
one matters most, because a file rejected only after the session is created
strands the user's first message in a session they never wanted.

This flow has no coverage below the browser: no web vitest test exercises
the ChatPage composer's ``addFiles`` / ``removeFile`` path, and the attach
mechanism (a real hidden file input populated by the OS file picker) is exactly
what a unit test can't drive. Playwright's ``set_input_files`` populates the
hidden input directly — the same change event the picker fires — so the
attach → chip → remove cycle is fully deterministic and needs no agent turn or
network: the chips are local component state.

The assertion pins to the chip's per-file remove control
(``aria-label="Remove {filename}"``, ChatPage.tsx) appearing after attach and
disappearing after the remove click.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page, Route, expect

_COMPOSER = "Ask the agent anything…"
# The hidden input intentionally has no MIME allowlist; every file type can be
# stored and native runners receive a local path. A .txt fixture keeps the
# basic attach/remove test trivial.
_ATTACH_NAME = "attach_sample.txt"
_ATTACH_BODY = "composer attachment e2e sample\n"

# An arbitrary binary type used to prove the old client allowlist is gone.
_PPTX_NAME = "deck.pptx"

# JSON is its own MIME (``application/json``), which is NOT covered by the
# ``text/*`` wildcard, so it has to be listed in the ``accept`` attr explicitly
# for the OS picker (and the drag-drop ``matchesAccept`` validator) to admit it.
_JSON_NAME = "attach_sample.json"
_JSON_BODY = '{"composer": "attachment", "e2e": true}\n'

# A zip is the case users actually hit (dragging an iCloud Photos export).
_ZIP_NAME = "photos.zip"

# The server's real 415 body for an unsupported upload, from
# ``routes_resources.upload_session_file``. Used to drive the failed-send path.
_SERVER_415_DETAIL = (
    "Unsupported attachment type 'application/zip'. Only images, PDF, "
    "and text/code files can be attached."
)


def test_attach_then_remove_file(
    page: Page, seeded_session: tuple[str, str], tmp_path: Path
) -> None:
    """Attach a file via the hidden input → chip + remove button appear → remove clears it."""
    base_url, session_id = seeded_session
    sample = tmp_path / _ATTACH_NAME
    sample.write_text(_ATTACH_BODY)

    page.goto(f"{base_url}/c/{session_id}")
    expect(page.get_by_placeholder(_COMPOSER)).to_be_visible(timeout=30_000)

    # The attach affordance is a paperclip button; its click target is the
    # hidden file input. Drive the input directly (the picker can't be scripted).
    file_input = page.locator('input[type="file"]')
    file_input.set_input_files(str(sample))

    # The chip renders below the textarea with a per-file remove button whose
    # accessible name carries the filename.
    remove_button = page.get_by_role("button", name=f"Remove {_ATTACH_NAME}")
    expect(remove_button).to_be_visible(timeout=10_000)
    expect(page.get_by_text(_ATTACH_NAME, exact=True)).to_be_visible()

    # Removing the chip drops it from composer state.
    remove_button.click()
    expect(remove_button).to_be_hidden(timeout=10_000)
    expect(page.get_by_text(_ATTACH_NAME, exact=True)).to_be_hidden()


def test_attach_json_file(page: Page, seeded_session: tuple[str, str], tmp_path: Path) -> None:
    """A ``.json`` file is admitted by the picker and attaches as a chip.

    The hidden input has no ``accept`` allowlist, and driving a real JSON file
    through it yields the normal attachment chip.
    """
    base_url, session_id = seeded_session
    sample = tmp_path / _JSON_NAME
    sample.write_text(_JSON_BODY)

    page.goto(f"{base_url}/c/{session_id}")
    expect(page.get_by_placeholder(_COMPOSER)).to_be_visible(timeout=30_000)

    file_input = page.locator('input[type="file"]')
    assert file_input.get_attribute("accept") is None

    file_input.set_input_files(str(sample))

    remove_button = page.get_by_role("button", name=f"Remove {_JSON_NAME}")
    expect(remove_button).to_be_visible(timeout=10_000)
    expect(page.get_by_text(_JSON_NAME, exact=True)).to_be_visible()


def test_accept_arbitrary_binary_type(
    page: Page, seeded_session: tuple[str, str], tmp_path: Path
) -> None:
    """A pptx reaches composer state instead of being rejected by MIME."""
    base_url, session_id = seeded_session
    sample = tmp_path / _PPTX_NAME
    sample.write_bytes(b"PK\x03\x04 not a real pptx, just an unsupported binary")

    page.goto(f"{base_url}/c/{session_id}")
    expect(page.get_by_placeholder(_COMPOSER)).to_be_visible(timeout=30_000)

    file_input = page.locator('input[type="file"]')
    file_input.set_input_files(str(sample))

    expect(page.get_by_role("button", name=f"Remove {_PPTX_NAME}")).to_be_visible(
        timeout=10_000
    )
    expect(page.get_by_text("can't be attached", exact=False)).to_have_count(0)


def test_landing_accepts_arbitrary_binary_and_keeps_message(
    page: Page, seeded_session: tuple[str, str], tmp_path: Path
) -> None:
    """The new-chat landing composer keeps a zip and the typed message."""
    base_url, _session_id = seeded_session
    sample = tmp_path / _ZIP_NAME
    sample.write_bytes(b"PK\x03\x04 not a real zip, just an unsupported binary")

    page.goto(base_url)
    composer = page.get_by_test_id("new-chat-landing-input")
    expect(composer).to_be_visible(timeout=30_000)

    composer.fill("summarize these photos")
    page.get_by_test_id("new-chat-landing-file-input").set_input_files(str(sample))

    expect(page.get_by_role("button", name=f"Remove {_ZIP_NAME}")).to_be_visible(timeout=10_000)
    error = page.get_by_test_id("new-chat-landing-attachment-error")
    expect(error).to_have_count(0)

    # The message the user typed is untouched, and no session was created —
    # still on the landing screen, not redirected into /c/<id>.
    expect(composer).to_have_value("summarize these photos")
    assert "/c/" not in page.url, f"a session was created despite the rejection: {page.url}"



def test_failed_upload_restores_the_message(
    page: Page, seeded_session: tuple[str, str], tmp_path: Path
) -> None:
    """A send whose upload fails hands the message back to the composer.

    Before, a failed upload left the user with an error and an empty composer:
    ``submit`` clears the text optimistically, the optimistic bubble rolls
    back, and nothing else held the message. Now ``send`` stashes it in
    ``failedSendDraft`` and the composer restores it.

    The failure is injected at the network boundary (the upload route responds
    415 with the server's real body) rather than by attaching an unsupported
    file — client-side validation would reject that before any request, so it
    would never exercise this path. The 415 body also pins the second half of
    the fix: the banner must carry the server's reason, not a bare
    ``upload failed: 415`` built from an empty HTTP/2 ``statusText``.
    """
    base_url, session_id = seeded_session
    sample = tmp_path / _ATTACH_NAME
    sample.write_text(_ATTACH_BODY)

    def _reject_upload(route: Route) -> None:
        route.fulfill(
            status=415,
            content_type="application/json",
            body=json.dumps({"detail": _SERVER_415_DETAIL}),
        )

    page.route("**/resources/files", _reject_upload)

    page.goto(f"{base_url}/c/{session_id}")
    composer = page.get_by_placeholder(_COMPOSER)
    expect(composer).to_be_visible(timeout=30_000)

    page.locator('input[type="file"]').set_input_files(str(sample))
    expect(page.get_by_role("button", name=f"Remove {_ATTACH_NAME}")).to_be_visible(timeout=10_000)

    composer.fill("look at this file")
    composer.press("Enter")

    # The compact banner keeps the reason one expansion away instead of
    # dropping it or replacing it with a bare status line.
    alert = page.get_by_role("alert")
    expect(alert).to_be_visible(timeout=30_000)
    headline = alert.get_by_role("button", name="Something went wrong", exact=False)
    expect(headline).to_have_attribute("aria-expanded", "false")
    headline.click()
    expect(page.get_by_text("Unsupported attachment type", exact=False)).to_be_visible()
    # And the message is back in the composer, ready to retry.
    expect(composer).to_have_value("look at this file", timeout=10_000)
