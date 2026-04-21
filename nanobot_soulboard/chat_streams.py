"""Websocket chat stream management for nanobot-soulboard."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot_soulboard.schemas import (
    ChatRequest,
    StreamChunkResponse,
    StreamFinalizedMessageResponse,
    StreamResetResponse,
)

StreamKey = tuple[str, str, str, str]


@dataclass
class StreamState:
    """Current streaming snapshot for one websocket chat session."""

    reasoning_content: str = ""
    content: str = ""
    websockets: set[WebSocket] = field(default_factory=set)
    queue: asyncio.Queue[ChatRequest] = field(default_factory=asyncio.Queue)
    backlog: list[dict[str, Any]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ChatStreamManager:
    """Manage durable websocket chat streams across disconnects."""

    def __init__(self) -> None:
        self._streams: dict[StreamKey, StreamState] = {}

    def get_or_create(self, key: StreamKey) -> StreamState:
        return self._streams.setdefault(key, StreamState())

    async def connect(self, key: StreamKey, websocket: WebSocket) -> StreamState:
        stream = self.get_or_create(key)
        async with stream.lock:
            stream.websockets.add(websocket)
            reset = StreamResetResponse(
                content=stream.content or None,
                reasoning_content=stream.reasoning_content or None,
            ).model_dump()
            backlog = list(stream.backlog)
        try:
            await websocket.send_json(reset)
        except RuntimeError:
            async with stream.lock:
                stream.websockets.discard(websocket)
            raise
        if backlog:
            delivered_all = True
            for payload in backlog:
                try:
                    await websocket.send_json(payload)
                except RuntimeError:
                    delivered_all = False
                    break
            if delivered_all:
                async with stream.lock:
                    if stream.backlog == backlog:
                        stream.backlog.clear()
        return stream

    async def disconnect(self, key: StreamKey, websocket: WebSocket) -> None:
        stream = self._streams.get(key)
        if stream is None:
            return
        async with stream.lock:
            stream.websockets.discard(websocket)
        await self._cleanup_if_idle(key, stream)

    async def enqueue(self, key: StreamKey, agent_loop: AgentLoop, body: ChatRequest) -> None:
        stream = self.get_or_create(key)
        await stream.queue.put(body)
        async with stream.lock:
            if stream.task is None or stream.task.done():
                stream.task = asyncio.create_task(self._run_stream(key, stream, agent_loop))

    async def _broadcast(self, stream: StreamState, payload: dict[str, Any]) -> bool:
        async with stream.lock:
            sockets = list(stream.websockets)
        if not sockets:
            return False
        alive = False
        dead: list[WebSocket] = []
        for websocket in sockets:
            try:
                await websocket.send_json(payload)
                alive = True
            except RuntimeError:
                dead.append(websocket)
        if dead:
            async with stream.lock:
                for websocket in dead:
                    stream.websockets.discard(websocket)
        return alive

    async def _run_stream(self, key: StreamKey, stream: StreamState, agent_loop: AgentLoop) -> None:
        try:
            while True:
                try:
                    body = stream.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                await self._process_turn(stream, agent_loop, body)
                await self._cleanup_if_idle(key, stream)
        finally:
            async with stream.lock:
                stream.task = None
            await self._cleanup_if_idle(key, stream)

    async def _process_turn(self, stream: StreamState, agent_loop: AgentLoop, body: ChatRequest) -> None:
        session = agent_loop.sessions.get_or_create(body.session_key)
        before_count = len(session.messages)
        async with stream.lock:
            stream.reasoning_content = ""
            stream.content = ""
            stream.backlog.clear()
        await self._broadcast(stream, StreamResetResponse().model_dump())

        async def _send_progress(content: str, *, tool_hint: bool = False) -> None:
            if tool_hint:
                payload = StreamChunkResponse(content=content, reasoning_content=None).model_dump()
            else:
                async with stream.lock:
                    stream.reasoning_content += content
                payload = StreamChunkResponse(content=None, reasoning_content=content).model_dump()
            await self._broadcast(stream, payload)

        async def _send_stream(delta: str) -> None:
            async with stream.lock:
                stream.content += delta
            payload = StreamChunkResponse(content=delta, reasoning_content=None).model_dump()
            await self._broadcast(stream, payload)

        async def _send_stream_end(*, resuming: bool = False) -> None:
            del resuming

        try:
            await agent_loop.process_direct(
                content=body.content,
                session_key=body.session_key,
                channel=body.channel,
                chat_id=body.chat_id,
                on_progress=_send_progress,
                on_stream=_send_stream,
                on_stream_end=_send_stream_end,
            )
            payloads = [
                StreamFinalizedMessageResponse(
                    role=message["role"],
                    content=message.get("content"),
                    tool_calls=message.get("tool_calls"),
                    tool_call_id=message.get("tool_call_id"),
                ).model_dump()
                for message in session.messages[before_count:]
            ]
        except Exception as exc:
            logger.exception("Background websocket stream failed for session {}", body.session_key)
            payloads = [
                StreamFinalizedMessageResponse(
                    role="assistant",
                    content=f"Sorry, I encountered an error: {exc}",
                    tool_calls=None,
                    tool_call_id=None,
                ).model_dump()
            ]

        for payload in payloads:
            delivered = await self._broadcast(stream, payload)
            if not delivered:
                async with stream.lock:
                    stream.backlog.append(payload)

        async with stream.lock:
            stream.reasoning_content = ""
            stream.content = ""

    async def _cleanup_if_idle(self, key: StreamKey, stream: StreamState) -> None:
        async with stream.lock:
            idle = (
                not stream.websockets
                and stream.queue.empty()
                and stream.task is None
                and not stream.reasoning_content
                and not stream.content
                and not stream.backlog
            )
        if idle:
            self._streams.pop(key, None)
