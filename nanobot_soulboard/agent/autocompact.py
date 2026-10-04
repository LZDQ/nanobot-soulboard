"""Soulboard-specific automatic session compaction behavior."""

from collections.abc import Collection, Callable, Coroutine
from typing import Any

from nanobot.agent.autocompact import AutoCompact


class SoulAutoCompact(AutoCompact):
    """Avoid scanning session files when idle compaction is disabled."""

    def check_expired(
        self,
        schedule_background: Callable[[Coroutine[Any, Any, Any]], None],
        active_session_keys: Collection[str] = (),
    ) -> None:
        if self._ttl <= 0:
            return
        super().check_expired(schedule_background, active_session_keys)
