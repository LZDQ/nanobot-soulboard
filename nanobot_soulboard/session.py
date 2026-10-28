"""Soulboard-specific session persistence."""

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.session.manager import Session, SessionManager
from pydantic import ValidationError

from nanobot_soulboard.schemas import ConsolidationHistoryEntry

CONSOLIDATION_HISTORY_KEY = "_consolidation_history"
LEGACY_CONSOLIDATION_INDICES_KEY = "_consolidation_indices"

_FORK_RUNTIME_METADATA_KEYS = {
    "channel",
    "chat_id",
    "fork",
    "goal_state",
    "thread_goal",
    "pending_user_turn",
    "runtime_checkpoint",
    "_sustained_goal_continuation_rounds",
    "title",
    "title_user_edited",
}


def consolidation_history(session: Session) -> list[ConsolidationHistoryEntry]:
    """Return valid cumulative checkpoints in their persisted order."""
    raw = session.metadata.get(CONSOLIDATION_HISTORY_KEY)
    if not isinstance(raw, list):
        return []

    entries: list[ConsolidationHistoryEntry] = []
    previous = -1
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            entry = ConsolidationHistoryEntry.model_validate(item)
        except ValidationError:
            continue
        if (
            entry.message_count <= previous
            or entry.message_count > len(session.messages)
            or not entry.summary.strip()
        ):
            continue
        entries.append(entry)
        previous = entry.message_count
    return entries


def consolidation_boundaries(session: Session) -> list[int]:
    """Return pagination boundaries from checkpoints and the upstream cursor."""
    boundaries = {
        entry.message_count
        for entry in consolidation_history(session)
        if entry.message_count > 0
    }
    current = session.last_consolidated
    if 0 < current <= len(session.messages):
        boundaries.add(current)
    return sorted(boundaries)


def rebase_consolidation_history(session: Session, dropped_prefix: int) -> None:
    """Rebase cumulative checkpoints after deleting a contiguous prefix."""
    if dropped_prefix <= 0:
        return
    rebased = []
    for entry in consolidation_history(session):
        if entry.message_count < dropped_prefix:
            continue
        rebased.append(
            entry.model_copy(
                update={"message_count": entry.message_count - dropped_prefix}
            )
        )
    if rebased:
        session.metadata[CONSOLIDATION_HISTORY_KEY] = [
            entry.model_dump(mode="json") for entry in rebased
        ]
    else:
        session.metadata.pop(CONSOLIDATION_HISTORY_KEY, None)


def clear_consolidation_history(session: Session) -> None:
    session.metadata.pop(CONSOLIDATION_HISTORY_KEY, None)


def _session_fork_info(metadata: Any) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("fork")
    if not isinstance(raw, dict):
        return None
    parent = raw.get("parent_session_key")
    count = raw.get("forked_message_count")
    if (
        not isinstance(parent, str)
        or not parent
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
    ):
        return None
    return {"parent_session_key": parent, "forked_message_count": count}


class SoulSessionManager(SessionManager):
    """Persist Soulboard checkpoint and fork metadata around upstream sessions."""

    def save(self, session: Session, *, fsync: bool = False) -> None:
        if not isinstance(session.metadata, dict):
            session.metadata = {}
        session.metadata.pop(LEGACY_CONSOLIDATION_INDICES_KEY, None)
        if not session.messages:
            clear_consolidation_history(session)
        else:
            history = consolidation_history(session)
            if history:
                session.metadata[CONSOLIDATION_HISTORY_KEY] = [
                    entry.model_dump(mode="json") for entry in history
                ]
            else:
                clear_consolidation_history(session)
        super().save(session, fsync=fsync)

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = super().list_sessions()
        for item in sessions:
            path = Path(item["path"])
            fork = None
            try:
                with path.open(encoding="utf-8") as file:
                    first_line = file.readline().strip()
                if first_line:
                    data = json.loads(first_line)
                    if isinstance(data, dict):
                        fork = _session_fork_info(data.get("metadata"))
            except (OSError, json.JSONDecodeError):
                fork = None
            item["fork"] = fork
        return sessions

    def fork_session_at_message_count(
        self,
        source: Session,
        target_key: str,
        message_count: int,
    ) -> Session:
        """Fork an already validated message prefix via upstream's fork helper."""
        before_user_index = sum(
            1
            for message in source.messages[:message_count]
            if message.get("role") == "user"
        )
        target = self.fork_session_before_user_index(
            source.key,
            target_key,
            before_user_index,
        )
        if target is None or len(target.messages) != message_count:
            raise ValueError("Could not copy the requested message prefix")

        metadata = deepcopy(target.metadata)
        for key in _FORK_RUNTIME_METADATA_KEYS:
            metadata.pop(key, None)
        for key in list(metadata):
            if key.startswith("_internal_continuation"):
                metadata.pop(key, None)

        checkpoints = [
            deepcopy(entry)
            for entry in consolidation_history(source)
            if entry.message_count <= message_count
        ]
        if checkpoints:
            checkpoint = checkpoints[-1]
            metadata[CONSOLIDATION_HISTORY_KEY] = [
                entry.model_dump(mode="json") for entry in checkpoints
            ]
            metadata["_last_summary"] = {
                "text": checkpoint.summary,
                "last_active": checkpoint.created_at.isoformat(),
            }
            target.last_consolidated = checkpoint.message_count
        else:
            metadata.pop(CONSOLIDATION_HISTORY_KEY, None)
            metadata.pop("_last_summary", None)
            target.last_consolidated = 0

        metadata.update(_metadata_for_target_key(target_key))
        metadata["fork"] = {
            "parent_session_key": source.key,
            "forked_message_count": message_count,
        }
        target.metadata = metadata
        target.updated_at = datetime.now()  # noqa: DTZ005 - upstream sessions are naive
        self.save(target, fsync=True)
        return target


def _metadata_for_target_key(key: str) -> dict[str, str]:
    metadata = {"title": key}
    if ":" not in key:
        return metadata
    channel, chat_id = key.split(":", 1)
    if channel:
        metadata["channel"] = channel
    if chat_id:
        metadata["chat_id"] = chat_id
    return metadata
