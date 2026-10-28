import type { SessionFork } from "../types";

/** Mirrors the backend's `_valid_session_key` so bad keys fail before the round trip. */
export function validateSessionKey(key: string): string {
  if (!key) {
    return "Session key is required";
  }
  if (key !== key.trim()) {
    return "Session key cannot start or end with whitespace";
  }
  if (key.length > 512) {
    return "Session key is longer than 512 characters";
  }
  if (/[\x00-\x1f\x7f]/.test(key)) {
    return "Session key cannot contain control characters";
  }
  const illegal = key.match(/[/\\?#]/);
  if (illegal) {
    return `Session key cannot contain "${illegal[0]}"`;
  }
  return "";
}

/**
 * Name a branch after its source rather than stacking suffixes: a fork of
 * `cli:direct-fork-2` is `cli:direct-fork-3`, not `cli:direct-fork-2-fork`.
 */
export function suggestForkKey(sourceKey: string, takenKeys: Iterable<string>): string {
  const base = sourceKey.replace(/-fork(-\d+)?$/, "") || sourceKey;
  const taken = new Set(takenKeys);
  for (let attempt = 1; attempt < 1000; attempt += 1) {
    const candidate = attempt === 1 ? `${base}-fork` : `${base}-fork-${attempt}`;
    if (!taken.has(candidate)) {
      return candidate;
    }
  }
  return `${base}-fork-${Date.now()}`;
}

/** Read fork provenance out of a session's raw metadata, validating as the backend does. */
export function readSessionFork(metadata: Record<string, unknown> | undefined): SessionFork | null {
  const raw = metadata?.fork;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const { parent_session_key: parent, forked_message_count: count } = raw as Record<string, unknown>;
  if (typeof parent !== "string" || !parent) {
    return null;
  }
  if (typeof count !== "number" || !Number.isInteger(count) || count <= 0) {
    return null;
  }
  return { parent_session_key: parent, forked_message_count: count };
}

export function describeFork(fork: SessionFork): string {
  const { forked_message_count: count } = fork;
  return `forked from ${fork.parent_session_key} · ${count} ${count === 1 ? "message" : "messages"}`;
}

/** First line of a message, clipped — enough to recognize the turn being branched from. */
export function forkPointPreview(text: string): string {
  const line = text.replace(/\s+/g, " ").trim();
  if (!line) {
    return "(empty message)";
  }
  return line.length > 120 ? `${line.slice(0, 119)}…` : line;
}
