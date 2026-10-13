"""Soulboard-specific session persistence."""

from nanobot.session.manager import Session, SessionManager

CONSOLIDATION_INDICES_KEY = "_consolidation_indices"


def consolidation_indices(session: Session) -> list[int]:
    """Return valid consolidation boundaries, including the current cursor."""
    current = session.last_consolidated
    if current <= 0:
        return []

    raw = (
        session.metadata.get(CONSOLIDATION_INDICES_KEY)
        if isinstance(session.metadata, dict)
        else None
    )
    if not isinstance(raw, list):
        return [current]

    indices = sorted(
        {
            value
            for value in raw
            if isinstance(value, int)
            and not isinstance(value, bool)
            and 0 < value <= current
            and value <= len(session.messages)
        }
    )
    if not indices or indices[-1] != current:
        indices.append(current)
    return indices


class SoulSessionManager(SessionManager):
    """Persist Soulboard pagination boundaries alongside upstream sessions."""

    def save(self, session: Session, *, fsync: bool = False) -> None:
        if not isinstance(session.metadata, dict):
            session.metadata = {}

        raw = session.metadata.get(CONSOLIDATION_INDICES_KEY)
        previous = (
            [
                value
                for value in raw
                if isinstance(value, int) and not isinstance(value, bool)
            ]
            if isinstance(raw, list)
            else []
        )

        current = session.last_consolidated
        if current <= 0:
            session.metadata.pop(CONSOLIDATION_INDICES_KEY, None)
        elif previous and max(previous) > current:
            # Compaction, truncation, clearing, and shorter forks rebase message
            # indices. Their former boundaries are no longer valid.
            session.metadata[CONSOLIDATION_INDICES_KEY] = [current]
        else:
            session.metadata[CONSOLIDATION_INDICES_KEY] = consolidation_indices(session)

        super().save(session, fsync=fsync)
