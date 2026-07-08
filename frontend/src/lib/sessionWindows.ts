import type { SessionDetail } from "../types";

export function appendMessages(
  current: SessionDetail | null,
  messages: Array<Record<string, unknown>>,
): SessionDetail | null {
  if (!current || !messages.length) {
    return current;
  }
  return {
    ...current,
    messages: [...current.messages, ...messages],
  };
}

export function prependSessionWindow(
  current: SessionDetail | null,
  older: SessionDetail,
): SessionDetail | null {
  if (!current) {
    return older;
  }
  if (older.history_end !== current.history_start) {
    return {
      ...older,
      messages: [...older.messages, ...current.messages],
      history_end: current.history_end,
      total_messages: current.total_messages,
    };
  }
  return {
    ...current,
    created_at: older.created_at,
    updated_at: older.updated_at,
    metadata: older.metadata,
    last_consolidated: older.last_consolidated,
    history_start: older.history_start,
    total_messages: older.total_messages,
    messages: [...older.messages, ...current.messages],
  };
}
