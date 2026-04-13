from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse
from nanobot_soulboard.agent import SoulAgentLoop


@pytest.mark.asyncio
async def test_soul_agent_loop_process_direct_supports_stream_callbacks(tmp_path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    async def _chat_stream_with_retry(*, messages, tools, model, on_content_delta):
        assert messages
        assert tools is not None
        assert model == "test-model"
        await on_content_delta("done")
        return LLMResponse(content="done", finish_reason="stop")

    provider.chat_stream_with_retry = AsyncMock(side_effect=_chat_stream_with_retry)
    provider.chat_with_retry = AsyncMock()

    loop = SoulAgentLoop(
        soul_id="alpha",
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )

    streamed: list[str] = []
    stream_end_calls: list[bool] = []

    async def on_stream(delta: str) -> None:
        streamed.append(delta)

    async def on_stream_end(*, resuming: bool = False) -> None:
        stream_end_calls.append(resuming)

    result = await loop.process_direct(
        "hello",
        session_key="cli:test",
        on_stream=on_stream,
        on_stream_end=on_stream_end,
    )

    assert result is not None
    assert result.content == "done"
    assert result.metadata["_streamed"] is True
    assert streamed == ["done"]
    assert stream_end_calls == [False]
    provider.chat_with_retry.assert_not_awaited()
