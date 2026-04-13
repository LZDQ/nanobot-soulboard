"""Soulboard-specific agent loop."""

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Awaitable, Callable

from loguru import logger
from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import OutboundMessage
from nanobot.command.router import CommandContext
from nanobot.session.manager import Session, SessionManager

from nanobot_soulboard.context import SoulboardContextBuilder
from nanobot_soulboard.cron import SoulCronTool


class SoulAgentLoop(AgentLoop):
    """
    AgentLoop subclass.
    Swaps in the soulboard context builder and persist messages each turn.
    """

    def __init__(self, *args, soul_id: str, **kwargs):
        self.soul_id = soul_id
        super().__init__(*args, **kwargs)
        self.context = SoulboardContextBuilder(self.workspace, soul_id=soul_id)

    def _register_default_tools(self) -> None:
        super()._register_default_tools()
        if self.cron_service:
            self.tools.register(SoulCronTool(self.cron_service, default_timezone=self.context.timezone))

    def _set_tool_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        session_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super()._set_tool_context(channel, chat_id, message_id)
        if cron_tool := self.tools.get("cron"):
            if isinstance(cron_tool, SoulCronTool):
                cron_tool.set_context(channel, chat_id, session_key, metadata)

    def _persist_turn_slice(self, session: Session, messages: list[dict[str, Any]], start: int) -> int:
        """Persist one suffix of newly-created turn messages and return the new cursor."""
        for message in messages[start:]:
            entry = dict(message)
            role = entry.get("role")
            content = entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue
            if role == "tool" and isinstance(content, str) and len(content) > self.max_tool_result_chars:
                entry["content"] = content[:self.max_tool_result_chars] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    for item in content:
                        if (
                            item.get("type") == "text"
                            and isinstance(item.get("text"), str)
                            and item["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
                        ):
                            continue
                        if (
                            item.get("type") == "image_url"
                            and item.get("image_url", {}).get("url", "").startswith("data:image/")
                        ):
                            path = (item.get("_meta") or {}).get("path", "")
                            placeholder = f"[image: {path}]" if path else "[image]"
                            filtered.append({"type": "text", "text": placeholder})
                        else:
                            filtered.append(item)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()
        self.sessions.save(session)
        return len(messages)

    async def _run_agent_loop_incremental(
        self,
        session: Session,
        initial_messages: list[dict[str, Any]],
        persisted_until: int,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        session_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str | None, list[str], list[dict[str, Any]]]:
        """Run the agent loop and flush completed tool iterations incrementally."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        _raw_stream = on_stream
        _stream_buf = ""

        async def _filtered_stream(delta: str) -> None:
            nonlocal _stream_buf
            from nanobot.utils.helpers import strip_think

            prev_clean = strip_think(_stream_buf)
            _stream_buf += delta
            new_clean = strip_think(_stream_buf)
            incremental = new_clean[len(prev_clean):]
            if incremental and _raw_stream:
                await _raw_stream(incremental)

        while iteration < self.max_iterations:
            iteration += 1

            tool_defs = self.tools.get_definitions()
            if on_stream:
                response = await self.provider.chat_stream_with_retry(
                    messages=messages,
                    tools=tool_defs,
                    model=self.model,
                    on_content_delta=_filtered_stream,
                )
            else:
                response = await self.provider.chat_with_retry(
                    messages=messages,
                    tools=tool_defs,
                    model=self.model,
                )

            usage = response.usage or {}
            self._last_usage = {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            }

            if response.has_tool_calls:
                if on_stream and on_stream_end:
                    await on_stream_end(resuming=True)
                    _stream_buf = ""

                if on_progress:
                    if not on_stream:
                        thought = self._strip_think(response.content)
                        if thought:
                            await on_progress(thought)
                    tool_hint = self._tool_hint(response.tool_calls)
                    tool_hint = self._strip_think(tool_hint)
                    await on_progress(tool_hint, tool_hint=True)

                tool_call_dicts = [tool_call.to_openai_tool_call() for tool_call in response.tool_calls]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])

                self._set_tool_context(channel, chat_id, message_id, session_key, metadata)

                results = await asyncio.gather(
                    *(self.tools.execute(tool_call.name, tool_call.arguments) for tool_call in response.tool_calls),
                    return_exceptions=True,
                )

                for tool_call, result in zip(response.tool_calls, results):
                    if isinstance(result, BaseException):
                        result = f"Error: {type(result).__name__}: {result}"
                    messages = self.context.add_tool_result(messages, tool_call.id, tool_call.name, result)

                persisted_until = self._persist_turn_slice(session, messages, persisted_until)
                continue

            if on_stream and on_stream_end:
                await on_stream_end(resuming=False)
                _stream_buf = ""

            clean = self._strip_think(response.content)
            if response.finish_reason == "error":
                logger.error("LLM returned error: {}", (clean or "")[:200])
                final_content = clean or "Sorry, I encountered an error calling the AI model."
                break

            messages = self.context.add_assistant_message(
                messages,
                clean,
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            )
            persisted_until = self._persist_turn_slice(session, messages, persisted_until)
            final_content = clean
            break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def _process_message(
        self,
        msg,
        session_key: str | None = None,
        on_progress=None,
        on_stream=None,
        on_stream_end=None,
    ):
        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            system_metadata = dict(msg.metadata or {})
            system_key = str(system_metadata.get("_system_session_key") or f"{channel}:{chat_id}")
            session = self.sessions.get_or_create(system_key)
            await self.consolidator.maybe_consolidate_by_tokens(session)
            self._set_tool_context(channel, chat_id, system_metadata.get("message_id"), system_key, system_metadata)
            history = session.get_history(max_messages=0)
            current_role = "assistant" if msg.sender_id == "subagent" else "user"
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                channel=channel,
                chat_id=chat_id,
                current_role=current_role,
            )
            persisted_until = self._persist_turn_slice(session, messages, len(messages) - 1)
            final_content, _, _ = await self._run_agent_loop_incremental(
                session,
                messages,
                persisted_until,
                on_progress=on_progress,
                channel=channel,
                chat_id=chat_id,
                message_id=system_metadata.get("message_id"),
                session_key=system_key,
                metadata=system_metadata,
            )
            self._schedule_background(self.consolidator.maybe_consolidate_by_tokens(session))
            outbound_metadata = {key: value for key, value in system_metadata.items() if not key.startswith("_")}
            return OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=final_content or "Background task completed.",
                metadata=outbound_metadata,
            )

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        raw = msg.content.strip()
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=self)
        if result := await self.commands.dispatch(ctx):
            return result

        await self.consolidator.maybe_consolidate_by_tokens(session)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"), key, msg.metadata or {})
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=0)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(
                OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta)
            )

        persisted_until = self._persist_turn_slice(session, initial_messages, len(initial_messages) - 1)
        final_content, _, _ = await self._run_agent_loop_incremental(
            session,
            initial_messages,
            persisted_until,
            on_progress=on_progress or _bus_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=msg.channel,
            chat_id=msg.chat_id,
            message_id=msg.metadata.get("message_id"),
            session_key=key,
            metadata=msg.metadata or {},
        )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        self._schedule_background(self.consolidator.maybe_consolidate_by_tokens(session))

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        meta = dict(msg.metadata or {})
        if on_stream is not None:
            meta["_streamed"] = True
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=meta,
        )
