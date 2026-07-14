import type { CronJobSchedule } from "../types";

export function formatDate(value: string | null): string {
  if (!value) {
    return "n/a";
  }
  return new Date(value).toLocaleString();
}

export function formatMessageTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const now = new Date();
  const year = String(date.getFullYear());
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const time = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  const isToday = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
  if (isToday) {
    return time;
  }
  if (date.getFullYear() === now.getFullYear()) {
    return `${month}/${day} ${time}`;
  }
  return `${year}/${month}/${day} ${time}`;
}

export function renderContent(value: unknown): string {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

export function summarizeToolResult(value: unknown): string {
  const content = renderContent(value).trim();
  if (!content) {
    return "empty";
  }
  const singleLine = content.replace(/\s+/g, " ");
  return singleLine.length > 96 ? `${singleLine.slice(0, 96)}...` : singleLine;
}

export function renderOverrideValue(value: string | string[] | boolean | null | undefined): string {
  if (typeof value === "boolean") {
    return value ? "enabled" : "disabled";
  }
  if (Array.isArray(value)) {
    return value.length ? value.join(", ") : "inherits from base config";
  }
  if (typeof value === "string") {
    return value.trim() ? value : "inherits from base config";
  }
  return "inherits from base config";
}

export function renderEnabledList(values: string[]): string {
  return values.length ? values.join(", ") : "none enabled";
}

export function renderToolList(values: string[], emptyLabel: string): string {
  if (!values.length) {
    return emptyLabel;
  }
  return [...values].sort((left, right) => left.localeCompare(right)).join(", ");
}

type SkillTextStats = {
  char_count: number | null;
  word_count: number | null;
  line_count: number | null;
};

function formatCount(value: number, singular: string, plural: string): string {
  return `${value.toLocaleString()} ${value === 1 ? singular : plural}`;
}

export function formatSkillTextStats(skill: SkillTextStats): string[] {
  const values: string[] = [];
  if (skill.char_count !== null && skill.char_count !== undefined) {
    values.push(formatCount(skill.char_count, "char", "chars"));
  }
  if (skill.word_count !== null && skill.word_count !== undefined) {
    values.push(formatCount(skill.word_count, "word", "words"));
  }
  if (skill.line_count !== null && skill.line_count !== undefined) {
    values.push(formatCount(skill.line_count, "line", "lines"));
  }
  return values;
}

export function renderHeaderOverrideSummary(headersByServer: Record<string, Record<string, string>>): string {
  const serverNames = Object.keys(headersByServer);
  if (!serverNames.length) {
    return "none";
  }
  return serverNames
    .map((name) => `${name} (${Object.keys(headersByServer[name] ?? {}).length})`)
    .join(", ");
}

export function getMessageReasoning(message: Record<string, unknown>): string {
  return typeof message.reasoning_content === "string" ? message.reasoning_content : "";
}

export function renderMcpTypeLabel(value: string | null): string {
  if (value === "streamableHttp") {
    return "streamable HTTP";
  }
  return value || "unknown";
}

export function formatTimestampMs(value: number | null): string {
  return value ? new Date(value).toLocaleString() : "none";
}

export function formatCronSchedule(schedule: CronJobSchedule): string {
  if (schedule.kind === "every" && schedule.every_ms) {
    const seconds = schedule.every_ms / 1000;
    if (seconds % 3600 === 0) {
      return `every ${seconds / 3600}h`;
    }
    if (seconds % 60 === 0) {
      return `every ${seconds / 60}m`;
    }
    return `every ${seconds}s`;
  }
  if (schedule.kind === "cron" && schedule.expr) {
    return schedule.tz ? `${schedule.expr} (${schedule.tz})` : schedule.expr;
  }
  if (schedule.kind === "at" && schedule.at_ms) {
    return `at ${formatTimestampMs(schedule.at_ms)}`;
  }
  return schedule.kind;
}
