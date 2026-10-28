"""Soulboard cumulative consolidation checkpoints."""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from nanobot.agent.memory import Consolidator, MemoryStore
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session, SessionManager
from nanobot.utils.prompt_templates import render_template

from nanobot_soulboard.schemas import ConsolidationHistoryEntry
from nanobot_soulboard.session import (
    CONSOLIDATION_HISTORY_KEY,
    consolidation_history,
    rebase_consolidation_history,
)

_ARCHIVE_SUMMARY_MAX_CHARS = 8_000


class SoulConsolidator(Consolidator):
    """Generate cumulative summaries and persist recoverable checkpoints."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        context_window_tokens: int,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        max_completion_tokens: int = 4096,
        consolidation_ratio: float = 0.5,
        unified_session: bool = False,
        timezone_name: str | None = None,
    ):
        super().__init__(
            store=store,
            provider=provider,
            model=model,
            sessions=sessions,
            context_window_tokens=context_window_tokens,
            build_messages=build_messages,
            get_tool_definitions=get_tool_definitions,
            max_completion_tokens=max_completion_tokens,
            consolidation_ratio=consolidation_ratio,
            unified_session=unified_session,
        )
        self.timezone = ZoneInfo(timezone_name) if timezone_name else None

    def _now(self) -> datetime:
        if self.timezone is not None:
            return datetime.now(self.timezone)
        return datetime.now().astimezone()

    @staticmethod
    def _chunk_end(session: Session, messages: list[dict]) -> int | None:
        if not messages:
            return None
        size = len(messages)
        for start in range(len(session.messages) - size + 1):
            candidate = session.messages[start : start + size]
            if all(left is right for left, right in zip(candidate, messages, strict=True)):
                return start + size
        return None

    async def archive(
        self,
        messages: list[dict],
        *,
        session_key: str | None = None,
    ) -> str | None:
        if not messages:
            return None

        session = self.sessions.get_or_create(session_key) if session_key else None
        end = self._chunk_end(session, messages) if session is not None else None
        checkpoints = consolidation_history(session) if session is not None else []
        previous = checkpoints[-1] if checkpoints else None
        start = previous.message_count if previous else 0
        cumulative_messages = messages
        if session is not None and end is not None and start <= end:
            cumulative_messages = session.messages[start:end]

        formatted = MemoryStore._format_messages(cumulative_messages)
        if previous:
            formatted = (
                "Previous cumulative summary:\n"
                f"{previous.summary}\n\n"
                "New conversation since that checkpoint:\n"
                f"{formatted}"
            )
        formatted = self._truncate_to_token_budget(formatted)
        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            render_template("agent/consolidator_archive.md", strip=True)
                            + "\nReturn one cumulative summary covering both any previous summary "
                            "and all new conversation."
                        ),
                    },
                    {"role": "user", "content": formatted},
                ],
                tools=None,
                tool_choice=None,
            )
            if response.finish_reason == "error":
                raise RuntimeError(f"LLM returned error: {response.content}")
            summary = response.content or "[no summary]"
            self.store.append_history(
                summary,
                max_chars=_ARCHIVE_SUMMARY_MAX_CHARS,
                session_key=session_key,
            )
        except Exception:  # noqa: BLE001 - preserve upstream raw-archive fallback
            logger.warning("Consolidation LLM call failed, raw-dumping to history")
            self.store.raw_archive(messages, session_key=session_key)
            return None

        if session is not None and end is not None and summary != "(nothing)":
            entry = ConsolidationHistoryEntry(
                message_count=end,
                summary=summary,
                created_at=self._now(),
            )
            if not checkpoints or end > checkpoints[-1].message_count:
                checkpoints.append(entry)
                session.metadata[CONSOLIDATION_HISTORY_KEY] = [
                    checkpoint.model_dump(mode="json")
                    for checkpoint in checkpoints
                ]
                session.metadata["_last_summary"] = {
                    "text": summary,
                    "last_active": entry.created_at.isoformat(),
                }
        return summary

    async def compact_idle_session(
        self,
        session_key: str,
        max_suffix: int = 8,
    ) -> str | None:
        lock = self.get_lock(session_key)
        async with lock:
            self.sessions.invalidate(session_key)
            session = self.sessions.get_or_create(session_key)
            original_count = len(session.messages)

            tail = list(session.messages[session.last_consolidated:])
            if not tail:
                session.updated_at = datetime.now()  # noqa: DTZ005 - upstream sessions are naive
                self.sessions.save(session)
                return ""

            probe = Session(
                key=session.key,
                messages=tail.copy(),
                created_at=session.created_at,
                updated_at=session.updated_at,
                metadata={},
                last_consolidated=0,
            )
            dropped, already_consolidated = probe.retain_recent_legal_suffix(max_suffix)
            kept = probe.messages
            archive_messages = dropped[already_consolidated:]
            if not archive_messages and not kept:
                session.updated_at = datetime.now()  # noqa: DTZ005 - upstream sessions are naive
                self.sessions.save(session)
                return ""

            last_active = session.updated_at
            summary: str | None = ""
            if archive_messages:
                summary = await self.archive(archive_messages, session_key=session_key)

            if summary and summary != "(nothing)":
                session.metadata["_last_summary"] = {
                    "text": summary,
                    "last_active": last_active.isoformat(),
                }

            dropped_prefix = original_count - len(kept)
            rebase_consolidation_history(session, dropped_prefix)
            session.messages = kept
            session.last_consolidated = 0
            session.updated_at = datetime.now()  # noqa: DTZ005 - upstream sessions are naive
            self.sessions.save(session)

            if archive_messages:
                logger.info(
                    "Idle-session compact for {}: archived={}, kept={}, summary={}",
                    session_key,
                    len(archive_messages),
                    len(kept),
                    bool(summary),
                )
            return summary
