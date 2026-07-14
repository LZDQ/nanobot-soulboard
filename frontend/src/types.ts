export type SoulOverrides = {
  model?: string | null;
  provider?: string | null;
  channels: string[];
  mcp_servers: string[];
  mcp_http_headers: Record<string, Record<string, string>>;
  enabled_tools: string[];
  disabled_tools: string[];
  autostart: boolean;
  groups: string[];
};

export type MCPServerConfig = {
  type: "stdio" | "sse" | "streamableHttp" | null;
  command: string;
  args: string[];
  env: Record<string, string>;
  url: string;
  headers: Record<string, string>;
  toolTimeout: number;
  enabledTools: string[];
};

export type MCPServer = {
  name: string;
  config: MCPServerConfig;
};

export type SoulSkill = {
  name: string;
  path: string;
  content: string;
  description: string | null;
  char_count: number | null;
  word_count: number | null;
  line_count: number | null;
  link_target: string | null;
};

export type SkillPoolEntry = {
  skill_path: string;
  relative_path: string;
  name: string;
  description: string | null;
  char_count: number | null;
  word_count: number | null;
  line_count: number | null;
};

export type SkillPool = {
  path: string;
  exists: boolean;
  skills: SkillPoolEntry[];
};

export type SkillRegistryResponse = {
  pools: SkillPool[];
};

export type Soul = {
  soul_id: string;
  workspace: string;
  skills: SoulSkill[];
  running: boolean;
  overrides: SoulOverrides;
};

export type NanobotTool = {
  name: string;
  description: string;
};

export type DisabledToolsResponse = {
  disabled_tools: string[];
};

export type ToolPolicyState = "inherit" | "enabled" | "disabled";

export type SessionSummary = {
  key: string;
  created_at: string | null;
  updated_at: string | null;
  path: string;
};

export type SessionListResponse = {
  items: SessionSummary[];
  total: number;
  limit: number;
  offset: number;
  order: "asc" | "desc";
};

export type SessionDetail = {
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
  last_consolidated: number;
  history_start: number;
  history_end: number;
  total_messages: number;
  messages: Array<Record<string, unknown>>;
};

export type SoulPromptFile = {
  name: string;
  exists: boolean;
  content: string;
};

export type SoulPromptFilesResponse = {
  files: SoulPromptFile[];
};

export type CronJobSchedule = {
  kind: string;
  at_ms: number | null;
  every_ms: number | null;
  expr: string | null;
  tz: string | null;
};

export type CronJobState = {
  next_run_at_ms: number | null;
  last_run_at_ms: number | null;
  last_status: string | null;
  last_error: string | null;
};

export type CronJob = {
  id: string;
  name: string;
  enabled: boolean;
  delete_after_run: boolean;
  message: string;
  deliver: boolean;
  channel: string | null;
  chat_id: string | null;
  session_key: string | null;
  recurring_session_key_format: string | null;
  schedule: CronJobSchedule;
  state: CronJobState;
};

export type CronJobRegistryEntry = {
  name: string;
  label: string | null;
  cron_expr: string | null;
  every_seconds: number | null;
  tz: string | null;
  message: string;
  deliver: boolean;
  channel: string | null;
  chat_id: string | null;
  session_key: string | null;
  recurring_session_key_format: string | null;
};

export type CronJobRegistryResponse = {
  items: CronJobRegistryEntry[];
};

export type CronJobRegistryEntryDraft = {
  name: string;
  label: string;
  cron_expr: string;
  every_seconds: string;
  tz: string;
  message: string;
  deliver: boolean;
  channel: string;
  chat_id: string;
  session_key: string;
  recurring_session_key_format: string;
};

export type CronJobEditDraft = {
  name: string;
  enabled: boolean;
  message: string;
  deliver: boolean;
  channel: string;
  chat_id: string;
  session_key: string;
  delete_after_run: boolean;
  schedule_kind: "every" | "cron";
  every_seconds: string;
  cron_expr: string;
  tz: string;
};

export type CronJobCreateDraft = {
  name: string;
  message: string;
  deliver: boolean;
  channel: string;
  chat_id: string;
  session_key: string;
  recurring_session_key_format: string;
  delete_after_run: boolean;
  schedule_kind: "every" | "cron";
  every_seconds: string;
  cron_expr: string;
  tz: string;
};

export const EMPTY_CRON_CREATE_DRAFT: CronJobCreateDraft = {
  name: "",
  message: "",
  deliver: false,
  channel: "",
  chat_id: "",
  session_key: "",
  recurring_session_key_format: "",
  delete_after_run: false,
  schedule_kind: "cron",
  every_seconds: "",
  cron_expr: "",
  tz: "",
};

export type StreamResetMessage = {
  type: "reset";
  content: string | null;
  reasoning_content: string | null;
};

export type StreamChunkMessage = {
  type: "chunk";
  content: string | null;
  reasoning_content: string | null;
};

export type StreamFinalizedMessage = {
  type: "finalized";
  role: string;
  content: unknown;
  timestamp: string | null;
  tool_calls: Array<Record<string, unknown>> | null;
  tool_call_id: string | null;
};

export type StreamMessage = StreamResetMessage | StreamChunkMessage | StreamFinalizedMessage;

export type DraftOverrides = {
  model: string;
  provider: string;
  channels: string;
  mcp_servers: string[];
  mcp_http_headers: Record<string, string>;
  enabled_tools: string[];
  disabled_tools: string[];
  autostart: boolean;
  groups: string[];
};

export type CreateSoulSkillDraft = {
  enabled: boolean;
  mode: "symlink" | "copy";
  target_name: string;
};

export type MCPServerDraft = {
  type: string;
  command: string;
  args: string;
  env: string;
  url: string;
  headers: string;
  tool_timeout: string;
  use_enabled_tools: boolean;
  enabled_tools: string;
};

export const SOUL_PROMPT_FILE_NAMES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "SYSTEM.md"] as const;

export type SoulPromptFileName = (typeof SOUL_PROMPT_FILE_NAMES)[number];
export type SoulPromptDraft = Record<SoulPromptFileName, string>;

export const DEFAULT_CHAT_CHANNEL = "cli";
export const DEFAULT_CHAT_ID = "direct";
