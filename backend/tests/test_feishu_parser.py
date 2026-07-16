import asyncio
import json
import logging
import tempfile
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.commands import KNOWN_CHANNEL_COMMANDS
from app.channels.feishu import (
    FeishuChannel,
    _disable_lark_websocket_proxy,
    _handle_lark_ws_loop_exception,
    _LarkWebSocketLogFilter,
)
from app.channels.message_bus import (
    PENDING_CLARIFICATION_METADATA_KEY,
    RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY,
    InboundMessage,
    MessageBus,
    OutboundMessage,
)
from app.channels.store import ChannelStore


def _pending(
    topic_id: str,
    *,
    thread_id: str | None = None,
    source_message_id: str | None = None,
    card_message_id: str | None = None,
    created_at: float = 9999999999,
) -> dict:
    return {
        "thread_id": thread_id or f"deer-thread-{topic_id}",
        "topic_id": topic_id,
        "source_message_id": source_message_id or topic_id,
        "card_message_id": card_message_id or f"card-{topic_id}",
        "created_at": created_at,
    }


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_lark_websocket_log_filter_redacts_connection_credentials():
    record = logging.LogRecord(
        "Lark",
        logging.INFO,
        __file__,
        1,
        "connected to wss://msg-frontier.feishu.cn/ws/v2?device_id=123&access_key=secret&ticket=ticket [conn_id=123]",
        (),
        None,
    )

    assert _LarkWebSocketLogFilter().filter(record) is True
    assert record.getMessage() == "connected to wss://msg-frontier.feishu.cn/ws/v2?<redacted> [conn_id=<redacted>]"
    assert "access_key" not in record.getMessage()
    assert "ticket" not in record.getMessage()


def test_lark_websocket_log_filter_demotes_normal_close():
    record = logging.LogRecord(
        "Lark",
        logging.ERROR,
        __file__,
        1,
        "receive message loop exit, err: sent 1000 (OK); then received 1000 (OK) bye",
        (),
        None,
    )

    assert _LarkWebSocketLogFilter().filter(record) is True
    assert record.levelno == logging.INFO
    assert record.levelname == "INFO"


def test_lark_websocket_proxy_is_disabled_only_for_feishu_hosts():
    calls: list[tuple[str, dict]] = []
    unset = object()

    def connect(uri: str, *, proxy: object = unset, **kwargs):
        captured = dict(kwargs)
        if proxy is not unset:
            captured["proxy"] = proxy
        calls.append((uri, captured))
        return object()

    websockets_module = SimpleNamespace(connect=connect)
    _disable_lark_websocket_proxy(websockets_module)

    websockets_module.connect("wss://msg-frontier.feishu.cn/ws/v2", proxy="socks5://localhost:1080")
    websockets_module.connect("wss://example.com/socket")

    assert calls == [
        ("wss://msg-frontier.feishu.cn/ws/v2", {"proxy": None}),
        ("wss://example.com/socket", {}),
    ]


def test_feishu_ws_loop_handler_suppresses_normal_close_only_after_stop():
    loop = MagicMock()
    normal_close = type("ConnectionClosedOK", (), {"code": 1000})()
    context = {"exception": normal_close, "message": "Task exception was never retrieved"}

    _handle_lark_ws_loop_exception(loop, context, stop_requested=True)
    loop.default_exception_handler.assert_not_called()

    _handle_lark_ws_loop_exception(loop, context, stop_requested=False)
    loop.default_exception_handler.assert_called_once_with(context)


def test_feishu_on_message_plain_text():
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    # Create mock event
    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"

    # Plain text content
    content_dict = {"text": "Hello world"}
    event.event.message.content = json.dumps(content_dict)

    # Call _on_message
    channel._on_message(event)

    # Since main_loop isn't running in this synchronous test, we can't easily assert on bus,
    # but we can intercept _make_inbound to check the parsed text.
    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        assert mock_make_inbound.call_args[1]["text"] == "Hello world"


def test_feishu_is_not_running_when_ws_thread_exits():
    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
    channel._running = True
    channel._thread = MagicMock()
    channel._thread.is_alive.return_value = False

    assert channel.is_running is False


def test_feishu_stop_disconnects_ws_client_and_joins_thread(monkeypatch):
    async def go():
        import lark_oapi as lark
        import lark_oapi.ws.client as ws_client_module

        started = threading.Event()
        disconnected = threading.Event()

        class FakeWSClient:
            instance = None

            def __init__(self, **_kwargs):
                self._auto_reconnect = True
                self.loop = None
                FakeWSClient.instance = self

            def start(self):
                self.loop = ws_client_module.loop
                started.set()
                self.loop.run_forever()

            async def _disconnect(self):
                disconnected.set()

        monkeypatch.setattr(lark.ws, "Client", FakeWSClient)

        channel = FeishuChannel(MessageBus(), {"app_id": "test", "app_secret": "test"})
        channel._running = True
        channel._thread = threading.Thread(
            target=channel._run_ws,
            args=("test", "test", "https://open.feishu.cn"),
            daemon=True,
        )
        thread = channel._thread
        thread.start()
        assert await asyncio.to_thread(started.wait, 1)

        try:
            await channel.stop()
            assert disconnected.is_set()
            assert not thread.is_alive()
            assert channel._thread is None
        finally:
            if thread.is_alive():
                instance = FakeWSClient.instance
                assert instance is not None and instance.loop is not None
                instance.loop.call_soon_threadsafe(instance.loop.stop)
                await asyncio.to_thread(thread.join, 1)

    _run(go())


def test_feishu_ignores_messages_after_stop():
    channel = FeishuChannel(MessageBus(), {"app_id": "test", "app_secret": "test"})
    channel._make_inbound = MagicMock()
    _run(channel.stop())

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.message.parent_id = None
    event.event.message.thread_id = None
    event.event.message.content = json.dumps({"text": "after stop"})
    event.event.sender.sender_id.open_id = "user_1"

    channel._on_message(event)

    channel._make_inbound.assert_not_called()


def test_feishu_event_handler_ignores_non_content_message_events():
    import lark_oapi as lark

    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})

    event_handler = channel._build_event_handler(lark)

    assert "p2.im.message.receive_v1" in event_handler._processorMap
    assert "p2.im.message.message_read_v1" in event_handler._processorMap
    assert "p2.im.message.reaction.created_v1" in event_handler._processorMap
    assert "p2.im.message.reaction.deleted_v1" in event_handler._processorMap
    assert "p2.im.message.recalled_v1" in event_handler._processorMap


def test_feishu_on_message_rich_text():
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    # Create mock event
    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"

    # Rich text content (topic group / post)
    content_dict = {"content": [[{"tag": "text", "text": "Paragraph 1, part 1."}, {"tag": "text", "text": "Paragraph 1, part 2."}], [{"tag": "at", "text": "@bot"}, {"tag": "text", "text": " Paragraph 2."}]]}
    event.event.message.content = json.dumps(content_dict)

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        parsed_text = mock_make_inbound.call_args[1]["text"]

        # Expected text:
        # Paragraph 1, part 1. Paragraph 1, part 2.
        #
        # @bot  Paragraph 2.
        assert "Paragraph 1, part 1. Paragraph 1, part 2." in parsed_text
        assert "@bot  Paragraph 2." in parsed_text
        assert "\n\n" in parsed_text


def test_feishu_receive_file_replaces_placeholders_in_order():
    async def go():
        bus = MessageBus()
        channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})

        msg = InboundMessage(
            channel_name="feishu",
            chat_id="chat_1",
            user_id="user_1",
            text="before [image] middle [file] after",
            thread_ts="msg_1",
            files=[{"image_key": "img_key"}, {"file_key": "file_key"}],
        )

        channel._receive_single_file = AsyncMock(side_effect=["/mnt/user-data/uploads/a.png", "/mnt/user-data/uploads/b.pdf"])

        result = await channel.receive_file(msg, "thread_1")

        assert result.text == "before /mnt/user-data/uploads/a.png middle /mnt/user-data/uploads/b.pdf after"

    _run(go())


def test_feishu_receive_file_syncs_sandbox_with_explicit_user_id(tmp_path, monkeypatch):
    async def go():
        from deerflow.config.paths import Paths

        bus = MessageBus()
        channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
        channel._GetMessageResourceRequest = MagicMock()
        builder = MagicMock()
        builder.message_id.return_value = builder
        builder.file_key.return_value = builder
        builder.type.return_value = builder
        builder.build.return_value = object()
        channel._GetMessageResourceRequest.builder.return_value = builder

        response = MagicMock()
        response.success.return_value = True
        response.file = BytesIO(b"file-bytes")
        response.file_name = "report.md"
        channel._api_client = MagicMock()
        channel._api_client.im.v1.message_resource.get.return_value = response

        provider = MagicMock()
        provider.acquire_async = AsyncMock(return_value="aio-1")
        sandbox = MagicMock()
        provider.get.return_value = sandbox

        monkeypatch.setattr("app.channels.feishu.get_paths", lambda: Paths(base_dir=tmp_path))
        monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", lambda: provider)
        monkeypatch.setattr("app.channels.feishu.get_effective_user_id", lambda: "default")

        virtual_path = await channel._receive_single_file("message-1", "file-key", "file", "thread-1", user_id="ou-user")

        assert virtual_path == "/mnt/user-data/uploads/report.md"
        assert (tmp_path / "users" / "ou-user" / "threads" / "thread-1" / "user-data" / "uploads" / "report.md").read_bytes() == b"file-bytes"
        provider.acquire_async.assert_awaited_once_with("thread-1", user_id="ou-user")
        sandbox.update_file.assert_called_once_with("/mnt/user-data/uploads/report.md", b"file-bytes")

    _run(go())


def test_feishu_receive_file_rejects_preexisting_upload_symlink(tmp_path, monkeypatch):
    async def go():
        from deerflow.config.paths import Paths

        paths = Paths(base_dir=tmp_path)
        uploads_dir = paths.sandbox_uploads_dir("thread-1", user_id="ou-user")
        uploads_dir.mkdir(parents=True)
        protected_file = tmp_path / "protected.txt"
        protected_file.write_bytes(b"protected")
        upload_path = uploads_dir / "report.md"
        upload_path.symlink_to(protected_file)

        channel = FeishuChannel(MessageBus(), {"app_id": "test", "app_secret": "test"})
        channel._GetMessageResourceRequest = MagicMock()
        builder = MagicMock()
        builder.message_id.return_value = builder
        builder.file_key.return_value = builder
        builder.type.return_value = builder
        builder.build.return_value = object()
        channel._GetMessageResourceRequest.builder.return_value = builder

        response = MagicMock()
        response.success.return_value = True
        response.file = BytesIO(b"attacker-controlled")
        response.file_name = "report.md"
        channel._api_client = MagicMock()
        channel._api_client.im.v1.message_resource.get.return_value = response

        provider = MagicMock()
        provider.acquire.return_value = "local"
        monkeypatch.setattr("app.channels.feishu.get_paths", lambda: paths)
        monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", lambda: provider)

        result = await channel._receive_single_file(
            "message-1",
            "file-key",
            "file",
            "thread-1",
            user_id="ou-user",
        )

        assert protected_file.read_bytes() == b"protected"
        assert result == "Failed to obtain the [file]"
        assert upload_path.is_symlink()

    _run(go())


@pytest.mark.parametrize("blocking_stage", ["acquire", "update_file"])
def test_feishu_receive_file_keeps_sandbox_io_off_event_loop(tmp_path, monkeypatch, blocking_stage):
    async def go():
        from deerflow.config.paths import Paths

        channel = FeishuChannel(MessageBus(), {"app_id": "test", "app_secret": "test"})
        channel._GetMessageResourceRequest = MagicMock()
        builder = MagicMock()
        builder.message_id.return_value = builder
        builder.file_key.return_value = builder
        builder.type.return_value = builder
        builder.build.return_value = object()
        channel._GetMessageResourceRequest.builder.return_value = builder

        response = MagicMock()
        response.success.return_value = True
        response.file = BytesIO(b"file-bytes")
        response.file_name = "report.md"
        channel._api_client = MagicMock()
        channel._api_client.im.v1.message_resource.get.return_value = response

        blocking_call_started = threading.Event()
        release_blocking_call = threading.Event()
        released_while_blocked = False

        def block_if_selected(stage):
            nonlocal released_while_blocked
            if stage == blocking_stage:
                blocking_call_started.set()
                released_while_blocked = release_blocking_call.wait(timeout=0.5)

        class Sandbox:
            def update_file(self, *_args):
                block_if_selected("update_file")

        class Provider:
            def acquire(self, *_args, **_kwargs):
                block_if_selected("acquire")
                return "aio-1"

            def get(self, *_args):
                return Sandbox()

        monkeypatch.setattr("app.channels.feishu.get_paths", lambda: Paths(base_dir=tmp_path))
        monkeypatch.setattr("app.channels.feishu.get_sandbox_provider", lambda: Provider())

        async def release_from_event_loop():
            assert await asyncio.to_thread(blocking_call_started.wait, 1)
            release_blocking_call.set()

        await asyncio.gather(
            channel._receive_single_file(
                "message-1",
                "file-key",
                "file",
                "thread-1",
                user_id="ou-user",
            ),
            release_from_event_loop(),
        )

        assert released_while_blocked is True

    _run(go())


def test_feishu_on_message_extracts_image_and_file_keys():
    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"

    # Rich text with one image and one file element.
    event.event.message.content = json.dumps(
        {
            "content": [
                [
                    {"tag": "text", "text": "See"},
                    {"tag": "img", "image_key": "img_123"},
                    {"tag": "file", "file_key": "file_456"},
                ]
            ]
        }
    )

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        files = mock_make_inbound.call_args[1]["files"]
        assert files == [{"image_key": "img_123"}, {"file_key": "file_456"}]
        assert "[image]" in mock_make_inbound.call_args[1]["text"]
        assert "[file]" in mock_make_inbound.call_args[1]["text"]


def test_feishu_on_message_reuses_stored_parent_topic_for_card_replies():
    bus = MessageBus()
    store = ChannelStore(path=Path(tempfile.mkdtemp()) / "store.json")
    store.set_thread_id(
        "feishu",
        "chat_1",
        "deer-thread-1",
        topic_id="om_clarification_card",
        user_id="user_1",
    )
    channel = FeishuChannel(
        bus,
        {"app_id": "test", "app_secret": "test", "channel_store": store},
    )

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_reply"
    event.event.message.root_id = "om_unknown_root"
    event.event.message.parent_id = "om_clarification_card"
    event.event.message.thread_id = None
    event.event.sender.sender_id.open_id = "user_1"
    event.event.message.content = json.dumps({"text": "prod"})

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        inbound = mock_make_inbound.return_value
        assert inbound.topic_id == "om_clarification_card"
        assert mock_make_inbound.call_args.kwargs["metadata"]["topic_id"] == "om_clarification_card"


def _make_text_event(
    text: str,
    *,
    chat_id: str = "chat_1",
    message_id: str = "msg_1",
    user_id: str = "user_1",
    root_id: str | None = None,
    parent_id: str | None = None,
    thread_id: str | None = None,
):
    event = MagicMock()
    event.event.message.chat_id = chat_id
    event.event.message.message_id = message_id
    event.event.message.root_id = root_id
    event.event.message.parent_id = parent_id
    event.event.message.thread_id = thread_id
    event.event.sender.sender_id.open_id = user_id
    event.event.message.content = json.dumps({"text": text})
    return event


def test_feishu_plain_reply_consumes_pending_clarification_topic():
    bus = MessageBus()
    store = ChannelStore(path=Path(tempfile.mkdtemp()) / "store.json")
    store.set_thread_id("feishu", "chat_1", "deer-thread-1", topic_id="om_original", user_id="user_1")
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test", "channel_store": store})
    channel._pending_clarifications[channel._pending_key("chat_1", "user_1")] = [_pending("om_original", thread_id="deer-thread-1", card_message_id="om_card")]

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(_make_text_event("2", message_id="msg_plain_2"))

        inbound = mock_make_inbound.return_value
        metadata = mock_make_inbound.call_args.kwargs["metadata"]
        assert inbound.topic_id == "om_original"
        assert metadata["topic_id"] == "om_original"
        assert metadata[RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY] is True
        assert channel._pending_key("chat_1", "user_1") not in channel._pending_clarifications


def test_feishu_command_room_plain_message_starts_new_topic_instead_of_consuming_pending():
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        {
            "app_id": "test",
            "app_secret": "test",
            "session": {"assistant_id": "command-room"},
        },
    )
    key = channel._pending_key("chat_1", "user_1")
    channel._pending_clarifications[key] = [_pending("om_original", thread_id="deer-thread-1")]

    created: list[InboundMessage] = []

    def fake_make_inbound(**kwargs):
        inbound = InboundMessage(channel_name="feishu", **kwargs)
        created.append(inbound)
        return inbound

    with pytest.MonkeyPatch.context() as m:
        m.setattr(channel, "_make_inbound", fake_make_inbound)
        channel._on_message(_make_text_event("new task", message_id="msg_new"))

    assert created[0].topic_id == "msg_new"
    assert key in channel._pending_clarifications
    assert created[0].metadata[RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY] is False


def test_feishu_pending_clarification_is_consumed_once():
    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
    channel._pending_clarifications[channel._pending_key("chat_1", "user_1")] = [_pending("om_original", thread_id="deer-thread-1", card_message_id="om_card")]

    with pytest.MonkeyPatch.context() as m:
        created = []

        def fake_make_inbound(**kwargs):
            inbound = InboundMessage(channel_name="feishu", **kwargs)
            created.append(inbound)
            return inbound

        mock_make_inbound = MagicMock(side_effect=fake_make_inbound)
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(_make_text_event("2", message_id="msg_first"))
        channel._on_message(_make_text_event("next", message_id="msg_second"))

        first_inbound = created[0]
        second_inbound = created[1]
        first_metadata = mock_make_inbound.call_args_list[0].kwargs["metadata"]
        second_metadata = mock_make_inbound.call_args_list[1].kwargs["metadata"]
        assert first_inbound.topic_id == "om_original"
        assert second_inbound.topic_id == "msg_second"
        assert first_metadata["topic_id"] == "om_original"
        assert first_metadata[RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY] is True
        assert second_metadata["topic_id"] == "msg_second"
        assert second_metadata[RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY] is False


def test_feishu_expired_pending_clarification_is_ignored(monkeypatch):
    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
    monkeypatch.setattr("app.channels.feishu.time.time", lambda: 10_000.0)
    channel._pending_clarifications[channel._pending_key("chat_1", "user_1")] = [_pending("om_original", thread_id="deer-thread-1", card_message_id="om_card", created_at=0.0)]

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(_make_text_event("2", message_id="msg_plain_2"))

        metadata = mock_make_inbound.call_args.kwargs["metadata"]
        assert metadata["topic_id"] == "msg_plain_2"
        assert metadata[RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY] is False
        assert channel._pending_key("chat_1", "user_1") not in channel._pending_clarifications


def test_feishu_command_does_not_consume_pending_clarification():
    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
    key = channel._pending_key("chat_1", "user_1")
    channel._pending_clarifications[key] = [_pending("om_original", thread_id="deer-thread-1", card_message_id="om_card")]

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(_make_text_event("/status", message_id="msg_command"))

        metadata = mock_make_inbound.call_args.kwargs["metadata"]
        assert mock_make_inbound.call_args.kwargs["msg_type"].value == "command"
        assert metadata["topic_id"] == "msg_command"
        assert metadata[RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY] is False
        assert key in channel._pending_clarifications


def test_feishu_remembers_pending_clarification_only_after_final_card_success():
    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
    outbound = OutboundMessage(
        channel_name="feishu",
        chat_id="chat_1",
        thread_id="deer-thread-1",
        text="clarify?",
        thread_ts="om_original",
        metadata={
            PENDING_CLARIFICATION_METADATA_KEY: True,
            "user_id": "user_1",
            "topic_id": "om_original",
            "message_id": "om_original",
        },
    )

    channel._remember_pending_clarification(outbound, None)
    assert channel._pending_clarifications == {}

    channel._remember_pending_clarification(outbound, "om_card")
    pending = channel._pending_clarifications[channel._pending_key("chat_1", "user_1")][0]
    assert pending["topic_id"] == "om_original"
    assert pending["thread_id"] == "deer-thread-1"
    assert pending["card_message_id"] == "om_card"


def test_feishu_multiple_pending_clarifications_are_consumed_in_order():
    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
    key = channel._pending_key("chat_1", "user_1")
    channel._pending_clarifications[key] = [
        _pending("om_first", thread_id="deer-thread-1"),
        _pending("om_second", thread_id="deer-thread-2"),
    ]

    with pytest.MonkeyPatch.context() as m:
        created = []

        def fake_make_inbound(**kwargs):
            inbound = InboundMessage(channel_name="feishu", **kwargs)
            created.append(inbound)
            return inbound

        m.setattr(channel, "_make_inbound", MagicMock(side_effect=fake_make_inbound))
        channel._on_message(_make_text_event("first answer", message_id="msg_first"))
        channel._on_message(_make_text_event("second answer", message_id="msg_second"))

        assert [msg.topic_id for msg in created] == ["om_first", "om_second"]
        assert key not in channel._pending_clarifications


def test_feishu_explicit_reply_prefers_stored_mapping_over_pending():
    bus = MessageBus()
    store = ChannelStore(path=Path(tempfile.mkdtemp()) / "store.json")
    store.set_thread_id("feishu", "chat_1", "deer-thread-card", topic_id="om_card", user_id="user_1")
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test", "channel_store": store})
    key = channel._pending_key("chat_1", "user_1")
    channel._pending_clarifications[key] = [_pending("om_pending", thread_id="deer-thread-pending")]

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(
            _make_text_event(
                "answer",
                message_id="msg_reply",
                root_id="om_unknown",
                parent_id="om_card",
            )
        )

        metadata = mock_make_inbound.call_args.kwargs["metadata"]
        assert metadata["topic_id"] == "om_card"
        assert metadata[RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY] is False
        assert key in channel._pending_clarifications


@pytest.mark.parametrize("command", sorted(KNOWN_CHANNEL_COMMANDS))
def test_feishu_recognizes_all_known_slash_commands(command):
    """Every entry in KNOWN_CHANNEL_COMMANDS must be classified as a command."""
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"
    event.event.message.content = json.dumps({"text": command})

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        assert mock_make_inbound.call_args[1]["msg_type"].value == "command", f"{command!r} should be classified as COMMAND"


@pytest.mark.parametrize(
    "text",
    [
        "/unknown",
        "/mnt/user-data/outputs/prd/technical-design.md",
        "/etc/passwd",
        "/not-a-command at all",
    ],
)
def test_feishu_treats_unknown_slash_text_as_chat(text):
    """Slash-prefixed text that is not a known command must be classified as CHAT."""
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"
    event.event.message.content = json.dumps({"text": text})

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        assert mock_make_inbound.call_args[1]["msg_type"].value == "chat", f"{text!r} should be classified as CHAT"
