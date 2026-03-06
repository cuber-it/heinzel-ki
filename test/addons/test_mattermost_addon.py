"""Tests für MattermostAddOn — Message-Filter, Dispatch, Thread-Reply, Reconnect."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from addons.mattermost import MattermostAddOn, MattermostMessage, MattermostReply
from addons.mattermost.client import MattermostClient


# =============================================================================
# Fixtures
# =============================================================================


def _make_addon(mention_only=True, reply_in_thread=True) -> MattermostAddOn:
    addon = MattermostAddOn(
        url="http://mm-test:8065",
        token="test-token",
        channel="heinzel-general",
        mention_only=mention_only,
        reply_in_thread=reply_in_thread,
    )
    # Client mocken
    addon._client = AsyncMock(spec=MattermostClient)
    addon._client.get_my_id = AsyncMock(return_value="bot-user-id")
    addon._client.get_my_username = AsyncMock(return_value="heinzel-1")
    addon._client.resolve_channel_id = AsyncMock(return_value="channel-abc")
    addon._client.post_message = AsyncMock(return_value={"id": "post-123"})
    addon._client.connect_websocket = AsyncMock(return_value=AsyncMock())
    # Lifecycle-Felder direkt setzen (ohne on_attach)
    addon._channel_id = "channel-abc"
    addon._my_id = "bot-user-id"
    addon._my_username = "heinzel-1"
    return addon


def _make_runner(response: str = "Antwort vom Heinzel") -> MagicMock:
    runner = MagicMock()
    runner.chat = AsyncMock(return_value=response)
    return runner


def _make_heinzel(addon: MattermostAddOn | None = None) -> MagicMock:
    heinzel = MagicMock()
    heinzel.runner = _make_runner()
    return heinzel


def _make_post(
    text: str = "Hallo @heinzel-1",
    user_id: str = "user-123",
    channel_id: str = "channel-abc",
    post_id: str = "post-001",
    root_id: str = "",
) -> dict:
    return {
        "id": post_id,
        "user_id": user_id,
        "channel_id": channel_id,
        "message": text,
        "root_id": root_id,
        "props": {},
    }


# =============================================================================
# MattermostMessage
# =============================================================================


def test_message_is_thread_reply():
    msg = MattermostMessage(
        message_id="1", channel_id="c", user_id="u", username="x",
        text="hi", root_id="root-1"
    )
    assert msg.is_thread_reply is True


def test_message_not_thread_reply():
    msg = MattermostMessage(
        message_id="1", channel_id="c", user_id="u", username="x", text="hi"
    )
    assert msg.is_thread_reply is False


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def test_is_mentioned_true():
    addon = _make_addon()
    assert addon._is_mentioned("Hey @heinzel-1 kannst du helfen?") is True


def test_is_mentioned_false():
    addon = _make_addon()
    assert addon._is_mentioned("Hallo alle zusammen") is False


def test_extract_mentions():
    addon = _make_addon()
    mentions = addon._extract_mentions("@heinzel-1 und @riker bitte helfen")
    assert "heinzel-1" in mentions
    assert "riker" in mentions


def test_strip_mention():
    addon = _make_addon()
    result = addon._strip_mention("@heinzel-1 suche nach Python")
    assert "@heinzel-1" not in result
    assert "suche nach Python" in result


# =============================================================================
# on_attach / on_detach
# =============================================================================


@pytest.mark.asyncio
async def test_on_attach_resolves_ids():
    addon = _make_addon()
    # Felder zurücksetzen um echten on_attach zu testen
    addon._channel_id = ""
    addon._my_id = ""
    addon._my_username = ""
    # WS-Loop sofort canceln damit Test nicht hängt
    addon._ws_task = asyncio.create_task(asyncio.sleep(0))

    heinzel = _make_heinzel()
    with patch.object(addon, '_ws_loop', new=AsyncMock()):
        await addon.on_attach(heinzel)

    assert addon._channel_id == "channel-abc"
    assert addon._my_id == "bot-user-id"
    assert addon._my_username == "heinzel-1"


@pytest.mark.asyncio
async def test_on_detach_cancels_task():
    addon = _make_addon()
    addon._runner = _make_runner()

    # Fake WS-Task
    async def _never_ending():
        await asyncio.sleep(9999)

    addon._ws_task = asyncio.create_task(_never_ending())
    await addon.on_detach(_make_heinzel())

    assert addon._ws_task is None
    assert addon._runner is None


def test_is_connected_false_without_task():
    addon = _make_addon()
    assert addon.is_connected() is False


# =============================================================================
# _handle_post — Filter
# =============================================================================


@pytest.mark.asyncio
async def test_ignores_own_messages():
    """Eigene Messages werden ignoriert."""
    addon = _make_addon()
    addon._runner = _make_runner()
    post = _make_post(user_id="bot-user-id")  # eigene ID
    await addon._handle_post(post)
    addon._runner.chat.assert_not_called()


@pytest.mark.asyncio
async def test_ignores_wrong_channel():
    """Messages aus anderen Channels ignorieren."""
    addon = _make_addon()
    addon._runner = _make_runner()
    post = _make_post(channel_id="anderer-channel")
    await addon._handle_post(post)
    addon._runner.chat.assert_not_called()


@pytest.mark.asyncio
async def test_ignores_empty_message():
    addon = _make_addon()
    addon._runner = _make_runner()
    post = _make_post(text="   ")
    await addon._handle_post(post)
    addon._runner.chat.assert_not_called()


@pytest.mark.asyncio
async def test_mention_only_ignores_without_mention():
    """mention_only=True: Message ohne @mention ignorieren."""
    addon = _make_addon(mention_only=True)
    addon._runner = _make_runner()
    post = _make_post(text="Hallo zusammen, wie geht's?")
    await addon._handle_post(post)
    addon._runner.chat.assert_not_called()


@pytest.mark.asyncio
async def test_mention_only_processes_with_mention():
    """mention_only=True: Message mit @mention verarbeiten."""
    addon = _make_addon(mention_only=True)
    addon._runner = _make_runner()
    post = _make_post(text="@heinzel-1 suche nach Python")
    await addon._handle_post(post)
    addon._runner.chat.assert_called_once()


@pytest.mark.asyncio
async def test_no_mention_filter_processes_all():
    """mention_only=False: alle Messages im Channel verarbeiten."""
    addon = _make_addon(mention_only=False)
    addon._runner = _make_runner()
    post = _make_post(text="Hallo zusammen ohne mention")
    await addon._handle_post(post)
    addon._runner.chat.assert_called_once()


# =============================================================================
# _dispatch — Text-Bereinigung + runner.chat()
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_strips_mention_before_llm():
    """@mention wird vor dem LLM-Call entfernt."""
    addon = _make_addon()
    runner = _make_runner()
    addon._runner = runner

    msg = MattermostMessage(
        message_id="1", channel_id="channel-abc", user_id="u",
        username="user", text="@heinzel-1 suche nach asyncio"
    )
    await addon._dispatch(msg)

    call_text = runner.chat.call_args[0][0]
    assert "@heinzel-1" not in call_text
    assert "asyncio" in call_text


@pytest.mark.asyncio
async def test_dispatch_posts_reply():
    """Antwort vom runner wird in Mattermost gepostet."""
    addon = _make_addon()
    addon._runner = _make_runner(response="Hier ist die Antwort.")

    msg = MattermostMessage(
        message_id="post-001", channel_id="channel-abc",
        user_id="u", username="user", text="@heinzel-1 hallo",
        root_id="post-001",
    )
    await addon._dispatch(msg)
    addon._client.post_message.assert_called_once()
    call_args = addon._client.post_message.call_args
    assert "Hier ist die Antwort." in call_args[0][1]


@pytest.mark.asyncio
async def test_dispatch_reply_in_thread():
    """reply_in_thread=True → root_id wird übergeben."""
    addon = _make_addon(reply_in_thread=True)
    addon._runner = _make_runner()

    msg = MattermostMessage(
        message_id="post-001", channel_id="channel-abc",
        user_id="u", username="user", text="@heinzel-1 hallo",
        root_id="root-thread-id",
    )
    await addon._dispatch(msg)
    call_args = addon._client.post_message.call_args
    assert call_args[1].get("root_id") == "root-thread-id" or \
           (len(call_args[0]) > 2 and call_args[0][2] == "root-thread-id")


@pytest.mark.asyncio
async def test_dispatch_no_thread():
    """reply_in_thread=False → kein root_id."""
    addon = _make_addon(reply_in_thread=False)
    addon._runner = _make_runner()

    msg = MattermostMessage(
        message_id="post-001", channel_id="channel-abc",
        user_id="u", username="user", text="hallo",
        root_id="root-id",
    )
    await addon._dispatch(msg)
    call_args = addon._client.post_message.call_args
    # root_id sollte leer sein
    root_id = call_args[1].get("root_id", call_args[0][2] if len(call_args[0]) > 2 else "")
    assert root_id == ""


@pytest.mark.asyncio
async def test_dispatch_no_runner_logs_warning():
    """Kein Runner → kein Crash, nur Warning."""
    addon = _make_addon()
    addon._runner = None

    msg = MattermostMessage(
        message_id="1", channel_id="c", user_id="u", username="u", text="hi"
    )
    # Darf nicht crashen
    await addon._dispatch(msg)
    addon._client.post_message.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_runner_error_posts_error_message():
    """Fehler in runner.chat() → Fehlermeldung wird gepostet."""
    addon = _make_addon()
    runner = MagicMock()
    runner.chat = AsyncMock(side_effect=RuntimeError("LLM kaputt"))
    addon._runner = runner

    msg = MattermostMessage(
        message_id="1", channel_id="channel-abc", user_id="u",
        username="u", text="@heinzel-1 hallo", root_id="1"
    )
    await addon._dispatch(msg)
    # Fehlermeldung wurde gepostet
    addon._client.post_message.assert_called_once()
    error_text = addon._client.post_message.call_args[0][1]
    assert "Fehler" in error_text


# =============================================================================
# post / post_to
# =============================================================================


@pytest.mark.asyncio
async def test_post_uses_configured_channel():
    addon = _make_addon()
    await addon.post("Hallo Channel!")
    addon._client.post_message.assert_called_once_with("channel-abc", "Hallo Channel!", root_id="")


@pytest.mark.asyncio
async def test_post_to_arbitrary_channel():
    addon = _make_addon()
    await addon.post_to("other-channel", "Hallo anderer Channel!")
    addon._client.post_message.assert_called_once_with("other-channel", "Hallo anderer Channel!", root_id="")
