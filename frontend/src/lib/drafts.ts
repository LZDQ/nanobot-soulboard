import {
  SOUL_PROMPT_FILE_NAMES,
  type DraftOverrides,
  type MCPServerConfig,
  type MCPServerDraft,
  type NanobotTool,
  type SoulOverrides,
  type SoulPromptDraft,
  type SoulPromptFile,
  type SoulPromptFileName,
  type ToolPolicyState,
} from "../types";

function splitCsv(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinLines(values: string[]): string {
  return values.join("\n");
}

function splitLines(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function getEmptyPromptDraft(): SoulPromptDraft {
  return {
    "AGENTS.md": "",
    "SOUL.md": "",
    "USER.md": "",
    "TOOLS.md": "",
    "SYSTEM.md": "",
  };
}

export function getEmptyPromptSelection(): Record<SoulPromptFileName, boolean> {
  return {
    "AGENTS.md": false,
    "SOUL.md": false,
    "USER.md": false,
    "TOOLS.md": false,
    "SYSTEM.md": false,
  };
}

export function getEmptyDraftOverrides(): DraftOverrides {
  return {
    workspace: "",
    model: "",
    provider: "",
    channels: "",
    mcp_servers: [],
    mcp_http_headers: {},
    enabled_tools: [],
    disabled_tools: [],
    autostart: false,
    groups: [],
  };
}

export function getEmptyMcpDraft(): MCPServerDraft {
  return {
    type: "stdio",
    command: "",
    args: "",
    env: "{}",
    url: "",
    headers: "{}",
    tool_timeout: "30",
    use_enabled_tools: false,
    enabled_tools: "",
  };
}

export function overridesToDraft(overrides: SoulOverrides): DraftOverrides {
  return {
    workspace: overrides.workspace ?? "",
    model: overrides.model ?? "",
    provider: overrides.provider ?? "",
    channels: overrides.channels.join(", "),
    mcp_servers: [...overrides.mcp_servers],
    mcp_http_headers: Object.fromEntries(
      Object.entries(overrides.mcp_http_headers ?? {}).map(([name, headers]) => [
        name,
        JSON.stringify(headers, null, 2),
      ]),
    ),
    enabled_tools: [...(overrides.enabled_tools ?? [])],
    disabled_tools: [...(overrides.disabled_tools ?? [])],
    autostart: overrides.autostart,
    groups: [...(overrides.groups ?? [])],
  };
}

function normalizeSoulMcpHeaderDraft(
  selectedServers: string[],
  headerDraft: Record<string, string>,
): Record<string, string> {
  return Object.fromEntries(
    selectedServers
      .filter((name) => Object.prototype.hasOwnProperty.call(headerDraft, name))
      .map((name) => [name, headerDraft[name] ?? "{}"]),
  );
}

export function updateSoulMcpSelection(
  draft: DraftOverrides,
  serverName: string,
  checked: boolean,
): DraftOverrides {
  const nextServers = checked
    ? draft.mcp_servers.includes(serverName)
      ? draft.mcp_servers
      : [...draft.mcp_servers, serverName]
    : draft.mcp_servers.filter((name) => name !== serverName);
  const nextHeaders = checked
    ? { ...draft.mcp_http_headers, [serverName]: draft.mcp_http_headers[serverName] ?? "{}" }
    : normalizeSoulMcpHeaderDraft(nextServers, draft.mcp_http_headers);
  return {
    ...draft,
    mcp_servers: nextServers,
    mcp_http_headers: nextHeaders,
  };
}

export function getToolChoices(
  tools: NanobotTool[],
  ...toolNameLists: string[][]
): NanobotTool[] {
  const known = new Set(tools.map((tool) => tool.name));
  const extraNames = Array.from(new Set(toolNameLists.flat()));
  return [
    ...tools,
    ...extraNames
      .filter((name) => name && !known.has(name))
      .map((name) => ({ name, description: "" })),
  ].sort((left, right) => left.name.localeCompare(right.name));
}

export function getToolPolicyState(
  enabledTools: string[],
  disabledTools: string[],
  toolName: string,
): ToolPolicyState {
  if (disabledTools.includes(toolName)) {
    return "disabled";
  }
  return enabledTools.includes(toolName) ? "enabled" : "inherit";
}

export function updateToolNameList(
  values: string[],
  toolName: string,
  included: boolean,
): string[] {
  if (included) {
    return values.includes(toolName) ? values : [...values, toolName];
  }
  return values.filter((name) => name !== toolName);
}

export function updateDraftToolPolicy(
  draft: DraftOverrides,
  toolName: string,
  state: ToolPolicyState,
): DraftOverrides {
  const enabledTools = draft.enabled_tools.filter((name) => name !== toolName);
  const disabledTools = draft.disabled_tools.filter((name) => name !== toolName);
  return {
    ...draft,
    enabled_tools: state === "enabled" ? [...enabledTools, toolName] : enabledTools,
    disabled_tools: state === "disabled" ? [...disabledTools, toolName] : disabledTools,
  };
}

export function draftToOverrides(draft: DraftOverrides): SoulOverrides {
  const mcpHttpHeaders = Object.fromEntries(
    Object.entries(normalizeSoulMcpHeaderDraft(draft.mcp_servers, draft.mcp_http_headers)).map(
      ([name, rawHeaders]) => [name, parseRecordInput(`mcp_http_headers.${name}`, rawHeaders)],
    ),
  );
  return {
    workspace: draft.workspace || null,
    model: draft.model || null,
    provider: draft.provider || null,
    channels: splitCsv(draft.channels),
    mcp_servers: [...draft.mcp_servers],
    mcp_http_headers: mcpHttpHeaders,
    enabled_tools: [...draft.enabled_tools],
    disabled_tools: [...draft.disabled_tools],
    autostart: draft.autostart,
    groups: [...draft.groups],
  };
}

export function mcpConfigToDraft(config: MCPServerConfig): MCPServerDraft {
  return {
    type: config.type ?? "stdio",
    command: config.command,
    args: joinLines(config.args),
    env: JSON.stringify(config.env, null, 2),
    url: config.url,
    headers: JSON.stringify(config.headers, null, 2),
    tool_timeout: String(config.toolTimeout),
    use_enabled_tools: !(config.enabledTools.length === 1 && config.enabledTools[0] === "*"),
    enabled_tools:
      config.enabledTools.length === 1 && config.enabledTools[0] === "*"
        ? ""
        : joinLines(config.enabledTools),
  };
}

function parseRecordInput(label: string, value: string): Record<string, string> {
  if (!value.trim()) {
    return {};
  }
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`);
  }
  const entries = Object.entries(parsed);
  for (const [key, item] of entries) {
    if (typeof item !== "string") {
      throw new Error(`${label}.${key} must be a string`);
    }
  }
  return Object.fromEntries(entries);
}

function draftToMcpConfig(draft: MCPServerDraft): MCPServerConfig {
  const toolTimeout = Number(draft.tool_timeout);
  if (!Number.isFinite(toolTimeout) || toolTimeout <= 0) {
    throw new Error("Tool timeout must be a positive number");
  }
  const type = draft.type.trim();
  if (type !== "stdio" && type !== "sse" && type !== "streamableHttp") {
    throw new Error("Type must be stdio, sse, or streamableHttp");
  }
  const command = draft.command.trim();
  const url = draft.url.trim();
  if (type === "stdio" && !command) {
    throw new Error("Command is required for stdio MCP servers");
  }
  if ((type === "sse" || type === "streamableHttp") && !url) {
    throw new Error("URL is required for HTTP MCP servers");
  }
  return {
    type: type as MCPServerConfig["type"],
    command: type === "stdio" ? command : "",
    args: type === "stdio" ? splitLines(draft.args) : [],
    env: parseRecordInput("env", draft.env),
    url: type === "stdio" ? "" : url,
    headers: type === "stdio" ? {} : parseRecordInput("headers", draft.headers),
    toolTimeout: toolTimeout,
    enabledTools: draft.use_enabled_tools ? splitLines(draft.enabled_tools) : ["*"],
  };
}

export function draftToMcpPayload(draft: MCPServerDraft): Record<string, unknown> {
  const config = draftToMcpConfig(draft);
  const payload: Record<string, unknown> = {
    type: config.type,
    command: config.command,
    args: config.args,
    env: config.env,
    url: config.url,
    headers: config.headers,
    toolTimeout: config.toolTimeout,
  };
  if (draft.use_enabled_tools) {
    payload.enabledTools = config.enabledTools;
  }
  return payload;
}

export function promptFilesToDraft(files: SoulPromptFile[]): SoulPromptDraft {
  const draft = getEmptyPromptDraft();
  for (const file of files) {
    if (SOUL_PROMPT_FILE_NAMES.some((name) => name === file.name)) {
      draft[file.name as SoulPromptFileName] = file.content;
    }
  }
  return draft;
}

export function promptFilesToSelection(): Record<SoulPromptFileName, boolean> {
  return getEmptyPromptSelection();
}
