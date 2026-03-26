import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Toaster, toast } from "sonner";

type SoulOverrides = {
  workspace?: string | null;
  model?: string | null;
  provider?: string | null;
  channels: string[];
  mcp_servers: string[];
  autostart: boolean;
};

type MCPServerConfig = {
  type: "stdio" | "sse" | "streamableHttp" | null;
  command: string;
  args: string[];
  env: Record<string, string>;
  url: string;
  headers: Record<string, string>;
  toolTimeout: number;
  enabledTools: string[];
};

type MCPServer = {
  name: string;
  config: MCPServerConfig;
};

type Soul = {
  soul_id: string;
  workspace: string;
  running: boolean;
  overrides: SoulOverrides;
};

type SessionSummary = {
  key: string;
  created_at: string | null;
  updated_at: string | null;
  path: string;
};

type SessionDetail = {
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
  last_consolidated: number;
  messages: Array<Record<string, unknown>>;
};

type SoulPromptFile = {
  name: string;
  exists: boolean;
  content: string;
};

type SoulPromptFilesResponse = {
  files: SoulPromptFile[];
};

type CronJobSchedule = {
  kind: string;
  at_ms: number | null;
  every_ms: number | null;
  expr: string | null;
  tz: string | null;
};

type CronJobState = {
  next_run_at_ms: number | null;
  last_run_at_ms: number | null;
  last_status: string | null;
  last_error: string | null;
};

type CronJob = {
  id: string;
  name: string;
  enabled: boolean;
  delete_after_run: boolean;
  message: string;
  deliver: boolean;
  channel: string | null;
  chat_id: string | null;
  session_key: string | null;
  schedule: CronJobSchedule;
  state: CronJobState;
};

type StreamResetMessage = {
  type: "reset";
  content: string | null;
  reasoning_content: string | null;
};

type StreamChunkMessage = {
  type: "chunk";
  content: string | null;
  reasoning_content: string | null;
};

type StreamFinalizedMessage = {
  type: "finalized";
  role: string;
  content: unknown;
  tool_calls: Array<Record<string, unknown>> | null;
  tool_call_id: string | null;
};

type StreamMessage = StreamResetMessage | StreamChunkMessage | StreamFinalizedMessage;

type DraftOverrides = {
  workspace: string;
  model: string;
  provider: string;
  channels: string;
  mcp_servers: string[];
  autostart: boolean;
};

type MCPServerDraft = {
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

const SOUL_PROMPT_FILE_NAMES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "SYSTEM.md"] as const;

type SoulPromptFileName = (typeof SOUL_PROMPT_FILE_NAMES)[number];
type SoulPromptDraft = Record<SoulPromptFileName, string>;

function getEmptyPromptDraft(): SoulPromptDraft {
  return {
    "AGENTS.md": "",
    "SOUL.md": "",
    "USER.md": "",
    "TOOLS.md": "",
    "SYSTEM.md": "",
  };
}

function getEmptyPromptSelection(): Record<SoulPromptFileName, boolean> {
  return {
    "AGENTS.md": false,
    "SOUL.md": false,
    "USER.md": false,
    "TOOLS.md": false,
    "SYSTEM.md": false,
  };
}

function getEmptyMcpDraft(): MCPServerDraft {
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

const DEFAULT_CHAT_CHANNEL = "cli";
const DEFAULT_CHAT_ID = "direct";

function getApiBase(): string {
  return (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";
}

function getWsBase(): string {
  const apiBase = getApiBase();
  const url = new URL(apiBase || window.location.origin, window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "";
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

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

function overridesToDraft(overrides: SoulOverrides): DraftOverrides {
  return {
    workspace: overrides.workspace ?? "",
    model: overrides.model ?? "",
    provider: overrides.provider ?? "",
    channels: overrides.channels.join(", "),
    mcp_servers: [...overrides.mcp_servers],
    autostart: overrides.autostart,
  };
}

function draftToOverrides(draft: DraftOverrides): SoulOverrides {
  return {
    workspace: draft.workspace || null,
    model: draft.model || null,
    provider: draft.provider || null,
    channels: splitCsv(draft.channels),
    mcp_servers: [...draft.mcp_servers],
    autostart: draft.autostart,
  };
}

function mcpConfigToDraft(config: MCPServerConfig): MCPServerDraft {
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

function draftToMcpPayload(draft: MCPServerDraft): Record<string, unknown> {
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

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = (await response.json()) as { detail?: string };
      if (data.detail) {
        message = data.detail;
      }
    } catch {
      // Keep the HTTP fallback when the body is empty or not JSON.
    }
    throw new Error(message);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function formatDate(value: string | null): string {
  if (!value) {
    return "n/a";
  }
  return new Date(value).toLocaleString();
}

function renderContent(value: unknown): string {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function summarizeToolResult(value: unknown): string {
  const content = renderContent(value).trim();
  if (!content) {
    return "empty";
  }
  const singleLine = content.replace(/\s+/g, " ");
  return singleLine.length > 96 ? `${singleLine.slice(0, 96)}...` : singleLine;
}

function MarkdownMessage({ content }: { content: string }) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
          pre: ({ children }) => {
            const child = Array.isArray(children) ? children[0] : children;
            const props = typeof child === "object" && child && "props" in child ? child.props : null;
            const code = props && "children" in props ? String(props.children).replace(/\n$/, "") : "";
            return (
              <div className="markdown-code-block">
                <button
                  type="button"
                  className="ghost markdown-code-copy"
                  onClick={() => {
                    void copyToClipboard(code).then(() => {
                      toast.success("Copied code");
                    }).catch((cause) => {
                      notifyError(cause);
                    });
                  }}
                  disabled={!code}
                >
                  Copy
                </button>
                <pre>{children}</pre>
              </div>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

async function copyToClipboard(text: string) {
  await navigator.clipboard.writeText(text);
}

function renderOverrideValue(value: string | string[] | boolean | null | undefined): string {
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

function renderEnabledList(values: string[]): string {
  return values.length ? values.join(", ") : "none enabled";
}

function promptFilesToDraft(files: SoulPromptFile[]): SoulPromptDraft {
  const draft = getEmptyPromptDraft();
  for (const file of files) {
    if (file.name in draft) {
      draft[file.name as SoulPromptFileName] = file.content;
    }
  }
  return draft;
}

function promptFilesToSelection(): Record<SoulPromptFileName, boolean> {
  return getEmptyPromptSelection();
}

function getMessageReasoning(message: Record<string, unknown>): string {
  return typeof message.reasoning_content === "string" ? message.reasoning_content : "";
}

function getErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function notifyError(cause: unknown): void {
  toast.error(getErrorMessage(cause), { id: "global-error" });
}

function appendMessages(
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

function renderMcpTypeLabel(value: string | null): string {
  if (value === "streamableHttp") {
    return "streamable HTTP";
  }
  return value || "unknown";
}

function formatTimestampMs(value: number | null): string {
  return value ? new Date(value).toLocaleString() : "none";
}

function formatCronSchedule(schedule: CronJobSchedule): string {
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

export default function App() {
  const [souls, setSouls] = useState<Soul[]>([]);
  const [selectedSoulId, setSelectedSoulId] = useState<string>("");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);
  const [promptFiles, setPromptFiles] = useState<SoulPromptFile[]>([]);
  const [cronJobs, setCronJobs] = useState<CronJob[] | null>(null);
  const [selectedMcpServerName, setSelectedMcpServerName] = useState<string>("");
  const [createMcpServerName, setCreateMcpServerName] = useState("");
  const [createSessionKey, setCreateSessionKey] = useState("");
  const [sessionDetail, setSessionDetail] = useState<SessionDetail | null>(null);
  const [sessionKey, setSessionKey] = useState<string | null>(null);
  const [socketEpoch, setSocketEpoch] = useState(0);
  const [chatInput, setChatInput] = useState("");
  const [chatReasoning, setChatReasoning] = useState("");
  const [chatContent, setChatContent] = useState("");
  const [finalizedMessages, setFinalizedMessages] = useState<StreamFinalizedMessage[]>([]);
  const [pending, setPending] = useState<string>("");
  const [soulError, setSoulError] = useState<string>("");
  const [createSoulId, setCreateSoulId] = useState("");
  const [draft, setDraft] = useState<DraftOverrides>({
    workspace: "",
    model: "",
    provider: "",
    channels: "",
    mcp_servers: [],
    autostart: false,
  });
  const [mcpDraft, setMcpDraft] = useState<MCPServerDraft>(getEmptyMcpDraft());
  const [createMcpDraft, setCreateMcpDraft] = useState<MCPServerDraft>(getEmptyMcpDraft());
  const [promptDraft, setPromptDraft] = useState<SoulPromptDraft>(getEmptyPromptDraft());
  const [promptSelection, setPromptSelection] = useState<Record<SoulPromptFileName, boolean>>(getEmptyPromptSelection());
  const [isEditingSoul, setIsEditingSoul] = useState(false);
  const [isEditingPromptFiles, setIsEditingPromptFiles] = useState(false);
  const [isCreatingSoul, setIsCreatingSoul] = useState(false);
  const [showOnlySelectedSessionCronJobs, setShowOnlySelectedSessionCronJobs] = useState(true);
  const [mcpMode, setMcpMode] = useState<"view" | "edit" | "create">("view");
  const [socketState, setSocketState] = useState<"closed" | "connecting" | "open">("closed");
  const socketRef = useRef<WebSocket | null>(null);
  const initializedRef = useRef(false);

  const selectedSoul = useMemo(
    () => souls.find((soul) => soul.soul_id === selectedSoulId) ?? null,
    [souls, selectedSoulId],
  );
  const visibleCronJobs = useMemo(() => {
    if (!cronJobs) {
      return cronJobs;
    }
    if (!showOnlySelectedSessionCronJobs || !sessionKey) {
      return cronJobs;
    }
    return cronJobs.filter((job) => job.session_key === sessionKey);
  }, [cronJobs, sessionKey, showOnlySelectedSessionCronJobs]);

  async function refreshSouls(preferredSoulId?: string): Promise<void> {
    let nextSouls: Soul[];
    try {
      nextSouls = await api<Soul[]>("/api/souls");
    } catch {
      setSouls([]);
      setSelectedSoulId("");
      setSessions([]);
      setPromptFiles([]);
      setCronJobs(null);
      setPromptDraft(getEmptyPromptDraft());
      setPromptSelection(getEmptyPromptSelection());
      setSessionDetail(null);
      setSessionKey(null);
      setChatContent("");
      setChatReasoning("");
      setFinalizedMessages([]);
      socketRef.current?.close();
      socketRef.current = null;
      setSocketState("closed");
      throw new Error("Cannot fetch soulboard");
    }
    setSouls(nextSouls);
    if (!nextSouls.length) {
      setSelectedSoulId("");
      setSessions([]);
      setPromptFiles([]);
      setCronJobs(null);
      setPromptDraft(getEmptyPromptDraft());
      setPromptSelection(getEmptyPromptSelection());
      setSessionDetail(null);
      setSessionKey(null);
      setIsEditingSoul(false);
      setIsEditingPromptFiles(false);
      setIsCreatingSoul(false);
      return;
    }
    const nextSelected =
      preferredSoulId && nextSouls.some((soul) => soul.soul_id === preferredSoulId)
        ? preferredSoulId
        : selectedSoulId && nextSouls.some((soul) => soul.soul_id === selectedSoulId)
          ? selectedSoulId
          : nextSouls[0].soul_id;
    setSelectedSoulId(nextSelected);
    const soul = nextSouls.find((item) => item.soul_id === nextSelected);
    if (soul) {
      setDraft(overridesToDraft(soul.overrides));
    }
  }

  async function refreshSessions(soulId: string): Promise<void> {
    const nextSessions = await api<SessionSummary[]>(`/api/souls/${encodeURIComponent(soulId)}/sessions`);
    setSessions(nextSessions);
  }

  async function refreshPromptFiles(soulId: string, resetDraft = false): Promise<void> {
    const response = await api<SoulPromptFilesResponse>(`/api/souls/${encodeURIComponent(soulId)}/prompt-files`);
    setPromptFiles(response.files);
    if (!isEditingPromptFiles || resetDraft) {
      setPromptDraft(promptFilesToDraft(response.files));
      setPromptSelection(promptFilesToSelection());
    }
  }

  async function refreshCronJobs(soulId: string): Promise<void> {
    const response = await api<CronJob[]>(`/api/souls/${encodeURIComponent(soulId)}/cron-jobs`);
    setCronJobs(response);
  }

  async function refreshMcpServers(preferredName?: string): Promise<void> {
    const nextServers = await api<MCPServer[]>("/api/mcp-servers");
    setMcpServers(nextServers);
    if (!nextServers.length) {
      setSelectedMcpServerName("");
      setMcpDraft(getEmptyMcpDraft());
      setMcpMode("create");
      return;
    }
    const nextSelected =
      preferredName && nextServers.some((server) => server.name === preferredName)
        ? preferredName
        : selectedMcpServerName && nextServers.some((server) => server.name === selectedMcpServerName)
          ? selectedMcpServerName
          : nextServers[0].name;
    setSelectedMcpServerName(nextSelected);
    const server = nextServers.find((item) => item.name === nextSelected);
    if (server) {
      setMcpDraft(mcpConfigToDraft(server.config));
    }
    if (mcpMode !== "create") {
      setMcpMode("view");
    }
  }

  useEffect(() => {
    if (initializedRef.current) {
      return;
    }
    initializedRef.current = true;
    void (async () => {
      try {
        await refreshSouls();
        await refreshMcpServers();
      } catch (cause) {
        notifyError(cause);
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedSoul) {
      return;
    }
    setDraft(overridesToDraft(selectedSoul.overrides));
    setIsEditingSoul(false);
    setIsCreatingSoul(false);
    setPromptFiles([]);
    setCronJobs(null);
    setPromptDraft(getEmptyPromptDraft());
    setPromptSelection(getEmptyPromptSelection());
    setIsEditingPromptFiles(false);
    setSoulError("");
    setSessionDetail(null);
    setSessionKey(null);
    setChatContent("");
    setChatReasoning("");
    setFinalizedMessages([]);
    socketRef.current?.close();
    socketRef.current = null;
    setSocketState("closed");
    void refreshSessions(selectedSoul.soul_id).catch((cause) => {
      notifyError(cause);
    });
    void refreshPromptFiles(selectedSoul.soul_id).catch((cause) => {
      notifyError(cause);
    });
    void refreshCronJobs(selectedSoul.soul_id).catch((cause) => {
      notifyError(cause);
    });
  }, [selectedSoul?.soul_id]);

  useEffect(() => {
    const selected = mcpServers.find((server) => server.name === selectedMcpServerName);
    if (!selected) {
      return;
    }
    setMcpDraft(mcpConfigToDraft(selected.config));
    if (mcpMode !== "create") {
      setMcpMode("view");
    }
  }, [mcpServers, selectedMcpServerName]);

  useEffect(() => {
    const soulId = selectedSoul?.soul_id;
    if (!soulId || !selectedSoul.running || !sessionKey) {
      socketRef.current?.close();
      socketRef.current = null;
      setSocketState("closed");
      return;
    }
    setSocketState("connecting");
    const url = new URL(`${getWsBase()}/ws/souls/${encodeURIComponent(soulId)}/chat`);
    url.searchParams.set("session_key", sessionKey);
    url.searchParams.set("channel", DEFAULT_CHAT_CHANNEL);
    url.searchParams.set("chat_id", DEFAULT_CHAT_ID);
    const socket = new WebSocket(url);
    socketRef.current = socket;
    socket.onopen = () => setSocketState("open");
    socket.onclose = () => {
      if (socketRef.current === socket) {
        socketRef.current = null;
        setSocketState("closed");
      }
    };
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as StreamMessage;
      if (message.type === "reset") {
        setFinalizedMessages((current) => {
          setSessionDetail((detail) => appendMessages(detail, current));
          return [];
        });
        setChatContent(message.content ?? "");
        setChatReasoning(message.reasoning_content ?? "");
        return;
      }
      if (message.type === "chunk") {
        if (message.content) {
          setChatContent((current) => current + message.content!);
        }
        if (message.reasoning_content) {
          setChatReasoning((current) => current + message.reasoning_content!);
        }
        return;
      }
      setFinalizedMessages((current) => [...current, message]);
      void refreshSessions(soulId).catch(() => {});
    };
    return () => {
      socket.close();
    };
  }, [selectedSoul?.soul_id, selectedSoul?.running, sessionKey, socketEpoch]);

  async function runAction(action: string, work: () => Promise<void>) {
    setPending(action);
    try {
      await work();
    } catch (cause) {
      throw cause;
    } finally {
      setPending("");
    }
  }

  async function createSoul() {
    if (!createSoulId.trim()) {
      notifyError("soul_id is required");
      return;
    }
    try {
      await runAction("create", async () => {
        const created = await api<Soul>("/api/souls", {
          method: "POST",
          body: JSON.stringify({
            soul_id: createSoulId.trim(),
            overrides: draftToOverrides(draft),
          }),
        });
        setCreateSoulId("");
        setIsCreatingSoul(false);
        await refreshSouls(created.soul_id);
        await refreshSessions(created.soul_id);
      });
    } catch (cause) {
      notifyError(cause);
    }
  }

  async function updateSoul() {
    if (!selectedSoul) {
      return;
    }
    setSoulError("");
    try {
      await runAction("update", async () => {
        if (selectedSoul.running) {
          await api<Soul>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/stop`, {
            method: "POST",
          });
        }
        await api<Soul>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}`, {
          method: "PATCH",
          body: JSON.stringify({ overrides: draftToOverrides(draft) }),
        });
        if (selectedSoul.running) {
          await api<Soul>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/start`, {
            method: "POST",
          });
        }
        await refreshSouls(selectedSoul.soul_id);
        setIsEditingSoul(false);
      });
    } catch (cause) {
      setSoulError(getErrorMessage(cause));
    }
  }

  async function updatePromptFiles() {
    if (!selectedSoul) {
      return;
    }
    setSoulError("");
    try {
      await runAction("prompt-files", async () => {
        if (selectedSoul.running) {
          await api<Soul>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/stop`, {
            method: "POST",
          });
        }
        const response = await api<SoulPromptFilesResponse>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/prompt-files`, {
          method: "PATCH",
          body: JSON.stringify({
            files: SOUL_PROMPT_FILE_NAMES.filter((name) => promptSelection[name]).map((name) => ({
              name,
              content: promptDraft[name],
            })),
          }),
        });
        if (selectedSoul.running) {
          await api<Soul>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/start`, {
            method: "POST",
          });
        }
        setPromptFiles(response.files);
        setPromptDraft(promptFilesToDraft(response.files));
        setPromptSelection(promptFilesToSelection());
        await refreshSouls(selectedSoul.soul_id);
        setIsEditingPromptFiles(false);
      });
    } catch (cause) {
      setSoulError(getErrorMessage(cause));
    }
  }

  async function toggleSoulRunning() {
    if (!selectedSoul) {
      return;
    }
    try {
      await runAction(selectedSoul.running ? "stop" : "start", async () => {
        const action = selectedSoul.running ? "stop" : "start";
        await api<Soul>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/${action}`, {
          method: "POST",
        });
        await refreshSouls(selectedSoul.soul_id);
      });
    } catch (cause) {
      notifyError(cause);
    }
  }

  async function restartSoul() {
    if (!selectedSoul || !selectedSoul.running) {
      return;
    }
    try {
      await runAction("restart", async () => {
        await api<Soul>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/stop`, {
          method: "POST",
        });
        await api<Soul>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/start`, {
          method: "POST",
        });
        await refreshSouls(selectedSoul.soul_id);
        await refreshCronJobs(selectedSoul.soul_id);
        await refreshPromptFiles(selectedSoul.soul_id, true);
      });
    } catch (cause) {
      notifyError(cause);
    }
  }

  async function deleteSoul() {
    if (!selectedSoul) {
      return;
    }
    if (!window.confirm(`Delete soul ${selectedSoul.soul_id}?`)) {
      return;
    }
    const soulId = selectedSoul.soul_id;
    try {
      await runAction("delete", async () => {
        await api<void>(`/api/souls/${encodeURIComponent(soulId)}`, { method: "DELETE" });
        await refreshSouls();
      });
    } catch (cause) {
      notifyError(cause);
    }
  }

  async function loadSession(key: string) {
    if (!selectedSoul) {
      return;
    }
    try {
      await runAction("session", async () => {
        socketRef.current?.close();
        socketRef.current = null;
        setSocketState("closed");
        const detail = await api<SessionDetail>(
          `/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/sessions/${encodeURIComponent(key)}`,
        );
        setSessionDetail(detail);
        setSessionKey(key);
        setChatContent("");
        setChatReasoning("");
        setFinalizedMessages([]);
        setSocketEpoch((current) => current + 1);
      });
    } catch (cause) {
      notifyError(cause);
    }
  }

  async function createSession() {
    if (!selectedSoul) {
      return;
    }
    const key = createSessionKey.trim();
    if (!key) {
      notifyError("Session key is required");
      return;
    }
    await runAction("create-session", async () => {
      socketRef.current?.close();
      socketRef.current = null;
      setSocketState("closed");
      const detail = await api<SessionDetail>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/sessions`, {
        method: "POST",
        body: JSON.stringify({ key }),
      });
      setSessionDetail(detail);
      setSessionKey(key);
      setCreateSessionKey("");
      setChatContent("");
      setChatReasoning("");
      setFinalizedMessages([]);
      await refreshSessions(selectedSoul.soul_id);
      setSocketEpoch((current) => current + 1);
    }).catch((cause) => {
      notifyError(cause);
    });
  }

  async function updateMcpServer() {
    if (!selectedMcpServerName) {
      return;
    }
    try {
      await runAction("mcp", async () => {
        await api<MCPServer>(`/api/mcp-servers/${encodeURIComponent(selectedMcpServerName)}`, {
          method: "PATCH",
          body: JSON.stringify({ config: draftToMcpPayload(mcpDraft) }),
        });
        await refreshMcpServers(selectedMcpServerName);
        setMcpMode("view");
      });
    } catch (cause) {
      notifyError(cause);
    }
  }

  async function deleteMcpServer() {
    if (!selectedMcpServerName) {
      return;
    }
    const deletedName = selectedMcpServerName;
    if (!window.confirm(`Delete MCP server ${deletedName}?`)) {
      return;
    }
    try {
      await runAction("mcp-delete", async () => {
        await api<void>(`/api/mcp-servers/${encodeURIComponent(deletedName)}`, {
          method: "DELETE",
        });
        setDraft((current) => ({
          ...current,
          mcp_servers: current.mcp_servers.filter((name) => name !== deletedName),
        }));
        await refreshMcpServers();
        setMcpMode("view");
      });
    } catch (cause) {
      notifyError(cause);
    }
  }

  async function createMcpServer() {
    const name = createMcpServerName.trim();
    if (!name) {
      notifyError("MCP server name is required");
      return;
    }
    try {
      await runAction("mcp-create", async () => {
        await api<MCPServer>("/api/mcp-servers", {
          method: "POST",
          body: JSON.stringify({
            name,
            config: draftToMcpPayload(createMcpDraft),
          }),
        });
        setCreateMcpServerName("");
        setCreateMcpDraft(getEmptyMcpDraft());
        await refreshMcpServers(name);
        setMcpMode("view");
      });
    } catch (cause) {
      notifyError(cause);
    }
  }

  async function submitChat(event: FormEvent) {
    event.preventDefault();
    if (!chatInput.trim() || socketState !== "open") {
      return;
    }
    const socket = socketRef.current;
    if (!socket) {
      return;
    }
    socket.send(JSON.stringify({ content: chatInput.trim() }));
    setChatInput("");
  }

  const runningCount = souls.filter((soul) => soul.running).length;
  const chatHistory = [...(sessionDetail?.messages ?? []), ...finalizedMessages] as Array<Record<string, unknown>>;
  const hasStreamingTurn = !!chatReasoning || !!chatContent;
  const selectedMcpServer = mcpServers.find((server) => server.name === selectedMcpServerName) ?? null;
  const activeMcpDraft = mcpMode === "create" ? createMcpDraft : mcpDraft;

  return (
    <div className="app-shell">
      <Toaster richColors position="top-center" expand visibleToasts={3} />
      <header className="hero">
        <div>
          <p className="eyebrow">nanobot soulboard</p>
          <h1>Operator console for soul switching, session review, and streamed chat.</h1>
        </div>
        <div className="hero-side">
          <div className="app-links">
            <a className="button-link ghost" href="/terminal/" target="_blank" rel="noreferrer">
              Terminal
            </a>
            <a className="button-link ghost" href="/filebrowser/" target="_blank" rel="noreferrer">
              Filebrowser
            </a>
            <a className="button-link ghost" href="/mihomo/" target="_blank" rel="noreferrer">
              Mihomo
            </a>
            <a className="button-link ghost" href="/napcat/" target="_blank" rel="noreferrer">
              NapCat
            </a>
          </div>
          <div className="hero-stats">
            <div className="stat-card">
              <span>souls</span>
              <strong>{souls.length}</strong>
            </div>
            <div className="stat-card">
              <span>running</span>
              <strong>{runningCount}</strong>
            </div>
          </div>
        </div>
      </header>

      <main className="grid">
        <section className="panel souls-panel">
          <div className="panel-head">
            <h2>Souls</h2>
            <button
              className="ghost"
              onClick={() => {
                void refreshSouls(selectedSoulId).catch((cause) => {
                  notifyError(cause);
                });
              }}
              disabled={!!pending}
            >
              Refresh
            </button>
          </div>
          <div className="soul-list">
            {souls.map((soul) => (
              <button
                key={soul.soul_id}
                className={`soul-card ${selectedSoulId === soul.soul_id ? "active" : ""}`}
                onClick={() => setSelectedSoulId(soul.soul_id)}
              >
                <div className="soul-card-head">
                  <strong>{soul.soul_id}</strong>
                  <span className={`pill ${soul.running ? "live" : "idle"}`}>{soul.running ? "running" : "stopped"}</span>
                </div>
                <code>{soul.workspace}</code>
              </button>
            ))}
            {!souls.length ? <p className="muted">No souls configured yet.</p> : null}
          </div>

          <div className="create-box">
            <h3>Create soul</h3>
            <button
              onClick={() => {
                setIsCreatingSoul(true);
                setSelectedSoulId("");
                setSessions([]);
                setSessionDetail(null);
                setSessionKey(null);
                setChatContent("");
                setChatReasoning("");
                setFinalizedMessages([]);
                setSoulError("");
                setDraft({
                  workspace: "",
                  model: "",
                  provider: "",
                  channels: "",
                  mcp_servers: [],
                  autostart: false,
                });
              }}
              disabled={!!pending}
            >
              New soul
            </button>
          </div>
        </section>

        <section className="panel sessions-panel">
          <div className="panel-head">
            <h2>{isCreatingSoul ? "Create new soul" : "Sessions"}</h2>
            {isCreatingSoul ? (
              <button
                className="ghost"
                onClick={() => {
                  setIsCreatingSoul(false);
                  if (selectedSoul) {
                    setDraft(overridesToDraft(selectedSoul.overrides));
                  }
                }}
                disabled={!!pending}
              >
                Cancel
              </button>
            ) : selectedSoul ? (
              <button
                className="ghost"
                onClick={() => {
                  void refreshSessions(selectedSoul.soul_id).catch((cause) => {
                    notifyError(cause);
                  });
                }}
                disabled={!!pending}
              >
                Reload
              </button>
            ) : null}
          </div>
          {isCreatingSoul ? (
            <div className="details-stack">
              <label>
                <span>Soul ID</span>
                <input value={createSoulId} onChange={(event) => setCreateSoulId(event.target.value)} placeholder="reviewer" />
              </label>
              <div className="field-grid">
                <label>
                  <span>Workspace override</span>
                  <input
                    value={draft.workspace}
                    onChange={(event) => setDraft((current) => ({ ...current, workspace: event.target.value }))}
                    placeholder="inherits soulboard default workspace"
                  />
                </label>
                <label>
                  <span>Model</span>
                  <input
                    value={draft.model}
                    onChange={(event) => setDraft((current) => ({ ...current, model: event.target.value }))}
                    placeholder="inherits from base config"
                  />
                </label>
                <label>
                  <span>Provider</span>
                  <input
                    value={draft.provider}
                    onChange={(event) => setDraft((current) => ({ ...current, provider: event.target.value }))}
                    placeholder="inherits from base config"
                  />
                </label>
                <label>
                  <span>Channels</span>
                  <input
                    value={draft.channels}
                    onChange={(event) => setDraft((current) => ({ ...current, channels: event.target.value }))}
                    placeholder="cli, telegram"
                  />
                </label>
                <label>
                  <span>MCP servers</span>
                  <div className="selection-grid">
                    {mcpServers.map((server) => (
                      <label key={server.name} className="check-tile">
                        <input
                          type="checkbox"
                          checked={draft.mcp_servers.includes(server.name)}
                          onChange={(event) => {
                            setDraft((current) => ({
                              ...current,
                              mcp_servers: event.target.checked
                                ? [...current.mcp_servers, server.name]
                                : current.mcp_servers.filter((name) => name !== server.name),
                            }));
                          }}
                        />
                        <span>{server.name}</span>
                      </label>
                    ))}
                    {!mcpServers.length ? <p className="muted">No MCP server definitions available.</p> : null}
                  </div>
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={draft.autostart}
                    onChange={(event) => setDraft((current) => ({ ...current, autostart: event.target.checked }))}
                  />
                  <span>Autostart on server boot</span>
                </label>
              </div>
              <button onClick={() => void createSoul()} disabled={!!pending}>
                Create soul
              </button>
            </div>
          ) : (
            <>
              <div className="session-list">
                {sessions.map((session) => (
                  <button key={session.key} className="session-card" onClick={() => void loadSession(session.key)}>
                    <strong>{session.key}</strong>
                    <span>updated {formatDate(session.updated_at)}</span>
                    <code>{session.path}</code>
                  </button>
                ))}
                {!sessions.length ? <p className="muted">No sessions found for this soul.</p> : null}
              </div>

              <div className="create-box">
                <h3>Create session</h3>
                <label>
                  <span>Session key</span>
                  <input
                    value={createSessionKey}
                    onChange={(event) => setCreateSessionKey(event.target.value)}
                    placeholder="cli:direct"
                  />
                </label>
                <button onClick={() => void createSession()} disabled={!!pending || !selectedSoul}>
                  Open session
                </button>
              </div>
            </>
          )}
        </section>

        <section className="panel details-panel">
          {soulError ? <section className="banner error">{soulError}</section> : null}
          <div className="panel-head">
            <h2>Selected soul</h2>
            {selectedSoul ? <code>{selectedSoul.soul_id}</code> : null}
          </div>
          {selectedSoul ? (
            <>
              <div className="action-row">
                <button onClick={() => void toggleSoulRunning()} disabled={!!pending}>
                  {selectedSoul.running ? "Stop" : "Start"}
                </button>
                {selectedSoul.running ? (
                  <button className="ghost" onClick={() => void restartSoul()} disabled={!!pending}>
                    Restart
                  </button>
                ) : null}
                <button className="danger" onClick={() => void deleteSoul()} disabled={!!pending}>
                  Delete
                </button>
              </div>

              <div className="details-stack">
                <section className="subpanel">
                  <div className="panel-head">
                    <h3>Overrides</h3>
                    {isEditingSoul ? (
                      <div className="action-row">
                        <button
                          className="ghost"
                          onClick={() => {
                            setDraft(overridesToDraft(selectedSoul.overrides));
                            setIsEditingSoul(false);
                          }}
                          disabled={!!pending}
                        >
                          Cancel
                        </button>
                        <button onClick={() => void updateSoul()} disabled={!!pending}>
                          {selectedSoul.running ? "Save & Restart" : "Save"}
                        </button>
                      </div>
                    ) : (
                      <button
                        className="ghost"
                        onClick={() => {
                          setDraft(overridesToDraft(selectedSoul.overrides));
                          setIsEditingSoul(true);
                        }}
                        disabled={!!pending}
                      >
                        Edit
                      </button>
                    )}
                  </div>

                  {isEditingSoul ? (
                    <div className="field-grid">
                      <label>
                        <span>Workspace override</span>
                        <input
                          value={draft.workspace}
                          onChange={(event) => setDraft((current) => ({ ...current, workspace: event.target.value }))}
                          placeholder={selectedSoul.workspace}
                        />
                      </label>
                      <label>
                        <span>Model</span>
                        <input
                          value={draft.model}
                          onChange={(event) => setDraft((current) => ({ ...current, model: event.target.value }))}
                          placeholder="inherits from base config"
                        />
                      </label>
                      <label>
                        <span>Provider</span>
                        <input
                          value={draft.provider}
                          onChange={(event) => setDraft((current) => ({ ...current, provider: event.target.value }))}
                          placeholder="inherits from base config"
                        />
                      </label>
                      <label>
                        <span>Channels</span>
                        <input
                          value={draft.channels}
                          onChange={(event) => setDraft((current) => ({ ...current, channels: event.target.value }))}
                          placeholder="cli, telegram"
                        />
                      </label>
                      <label>
                        <span>MCP servers</span>
                        <div className="selection-grid">
                          {mcpServers.map((server) => (
                            <label key={server.name} className="check-tile">
                              <input
                                type="checkbox"
                                checked={draft.mcp_servers.includes(server.name)}
                                onChange={(event) => {
                                  setDraft((current) => ({
                                    ...current,
                                    mcp_servers: event.target.checked
                                      ? [...current.mcp_servers, server.name]
                                      : current.mcp_servers.filter((name) => name !== server.name),
                                  }));
                                }}
                              />
                              <span>{server.name}</span>
                            </label>
                          ))}
                          {!mcpServers.length ? <p className="muted">No MCP server definitions available.</p> : null}
                        </div>
                      </label>
                      <label className="checkbox">
                        <input
                          type="checkbox"
                          checked={draft.autostart}
                          onChange={(event) => setDraft((current) => ({ ...current, autostart: event.target.checked }))}
                        />
                        <span>Autostart on server boot</span>
                      </label>
                    </div>
                  ) : (
                    <div className="override-grid">
                      <article className="override-card">
                        <span>Workspace override</span>
                        <strong>{renderOverrideValue(selectedSoul.overrides.workspace)}</strong>
                      </article>
                      <article className="override-card">
                        <span>Resolved workspace</span>
                        <strong>{selectedSoul.workspace}</strong>
                      </article>
                      <article className="override-card">
                        <span>Model override</span>
                        <strong>{renderOverrideValue(selectedSoul.overrides.model)}</strong>
                      </article>
                      <article className="override-card">
                        <span>Provider override</span>
                        <strong>{renderOverrideValue(selectedSoul.overrides.provider)}</strong>
                      </article>
                      <article className="override-card">
                        <span>Channel overrides</span>
                        <strong>{renderEnabledList(selectedSoul.overrides.channels)}</strong>
                      </article>
                      <article className="override-card">
                        <span>Enabled MCP servers</span>
                        <strong>{renderEnabledList(selectedSoul.overrides.mcp_servers)}</strong>
                      </article>
                      <article className="override-card">
                        <span>Autostart</span>
                        <strong>{renderOverrideValue(selectedSoul.overrides.autostart)}</strong>
                      </article>
                      <article className="override-card">
                        <span>Runtime status</span>
                        <strong>{selectedSoul.running ? "running" : "stopped"}</strong>
                      </article>
                    </div>
                  )}
                </section>

                <section className="subpanel">
                  <div className="panel-head">
                    <h3>Cron jobs</h3>
                    <div className="action-row">
                      <label className="check-tile cron-filter-toggle">
                        <input
                          type="checkbox"
                          checked={showOnlySelectedSessionCronJobs}
                          onChange={(event) => {
                            setShowOnlySelectedSessionCronJobs(event.target.checked);
                          }}
                        />
                        <span>Only selected session</span>
                      </label>
                      <button
                        className="ghost"
                        onClick={() => {
                          if (!selectedSoul) {
                            return;
                          }
                          void refreshCronJobs(selectedSoul.soul_id).catch((cause) => {
                            notifyError(cause);
                          });
                        }}
                        disabled={!!pending || !selectedSoul}
                      >
                        Refresh
                      </button>
                    </div>
                  </div>

                  {visibleCronJobs === null ? (
                    <p className="muted">Loading cron jobs…</p>
                  ) : visibleCronJobs.length ? (
                    <div className="session-list">
                      {visibleCronJobs.map((job) => (
                        <article key={job.id} className="session-card">
                          <strong>{job.name}</strong>
                          <span>{formatCronSchedule(job.schedule)}</span>
                          <code>{job.session_key || "no session"}</code>
                          <span>
                            {job.enabled ? "enabled" : "disabled"}
                            {job.state.last_status ? ` · last ${job.state.last_status}` : ""}
                          </span>
                          <span>next {formatTimestampMs(job.state.next_run_at_ms)}</span>
                          <p>{job.message}</p>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <p className="muted">
                      {showOnlySelectedSessionCronJobs && sessionKey
                        ? "No cron jobs found for the selected session."
                        : "No cron jobs found for this soul."}
                    </p>
                  )}
                </section>

                <section className="subpanel">
                  <div className="panel-head">
                    <h3>Prompt files</h3>
                    <div className="action-row">
                      <button
                        className="ghost"
                        onClick={() => {
                          if (!selectedSoul) {
                            return;
                          }
                          void refreshPromptFiles(selectedSoul.soul_id, true).catch((cause) => {
                            notifyError(cause);
                          });
                        }}
                        disabled={!!pending || !selectedSoul}
                      >
                        Refresh
                      </button>
                      {isEditingPromptFiles ? (
                        <>
                          <button
                            className="ghost"
                            onClick={() => {
                              setPromptDraft(promptFilesToDraft(promptFiles));
                              setPromptSelection(promptFilesToSelection());
                              setIsEditingPromptFiles(false);
                            }}
                            disabled={!!pending}
                          >
                            Cancel
                          </button>
                          <button
                            onClick={() => void updatePromptFiles()}
                            disabled={!!pending || !promptFiles.length || !SOUL_PROMPT_FILE_NAMES.some((name) => promptSelection[name])}
                          >
                            {selectedSoul.running ? "Save & Restart" : "Save"}
                          </button>
                        </>
                      ) : (
                        <button
                          className="ghost"
                          onClick={() => {
                            setPromptDraft(promptFilesToDraft(promptFiles));
                            setPromptSelection(promptFilesToSelection());
                            setIsEditingPromptFiles(true);
                          }}
                          disabled={!!pending || !promptFiles.length}
                        >
                          Edit
                        </button>
                      )}
                    </div>
                  </div>

                  <div className="md-file-list">
                    {promptFiles.length ? (
                      SOUL_PROMPT_FILE_NAMES.map((name) => {
                        const file = promptFiles.find((item) => item.name === name);
                        const exists = file?.exists ?? false;
                        const content = isEditingPromptFiles ? promptDraft[name] : (file?.content ?? "");
                        const selected = promptSelection[name];
                        const toggleSelection = () => {
                          setPromptSelection((current) => ({
                            ...current,
                            [name]: !current[name],
                          }));
                        };
                        return (
                          <details key={name} className="md-file" open={isEditingPromptFiles ? selected : undefined}>
                            <summary
                              onClick={
                                isEditingPromptFiles
                                  ? (event) => {
                                      event.preventDefault();
                                      toggleSelection();
                                    }
                                  : undefined
                              }
                            >
                              <span className={`md-file-title ${isEditingPromptFiles ? "editable" : ""}`}>
                                {isEditingPromptFiles ? (
                                  <input
                                    type="checkbox"
                                    checked={selected}
                                    onChange={() => {
                                      toggleSelection();
                                    }}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                    }}
                                  />
                                ) : null}
                                <span>{name}</span>
                              </span>
                              <span className={`pill ${exists ? "live" : "idle"}`}>{exists ? "present" : "missing"}</span>
                            </summary>
                            {isEditingPromptFiles && selected ? (
                              <label>
                                <span>{name}</span>
                                <textarea
                                  value={content}
                                  onChange={(event) =>
                                    setPromptDraft((current) => ({
                                      ...current,
                                      [name]: event.target.value,
                                    }))
                                  }
                                  placeholder={`Enter ${name} content`}
                                />
                              </label>
                            ) : isEditingPromptFiles ? (
                              <p className="muted">Enable this file to edit and include it in the save payload.</p>
                            ) : exists ? (
                              <pre>{content}</pre>
                            ) : (
                              <p className="muted">This file does not exist yet.</p>
                            )}
                          </details>
                        );
                      })
                    ) : (
                      <p className="muted">Loading prompt files…</p>
                    )}
                  </div>
                </section>

                <section className="subpanel">
                  <div className="panel-head">
                    <h3>MCP servers</h3>
                    <button
                      className="ghost"
                      onClick={() => {
                        void refreshMcpServers(selectedMcpServerName).catch((cause) => {
                          notifyError(cause);
                        });
                      }}
                      disabled={!!pending}
                    >
                      Reload
                    </button>
                  </div>
                  <div className="mcp-layout">
                    <div className="mcp-list">
                      {mcpServers.map((server) => (
                        <button
                          key={server.name}
                          className={`session-card ${selectedMcpServerName === server.name ? "active" : ""}`}
                          onClick={() => {
                            setSelectedMcpServerName(server.name);
                            setMcpMode("view");
                          }}
                        >
                          <strong>{server.name}</strong>
                          <span>{server.config.type || "auto"} transport</span>
                          <code>{server.config.command || server.config.url || "no endpoint configured"}</code>
                        </button>
                      ))}
                      {!mcpServers.length ? <p className="muted">No MCP server definitions found in the base nanobot config.</p> : null}

                      <div className="create-box">
                        <h3>Create MCP server</h3>
                        <button
                          className="ghost"
                          onClick={() => {
                            setMcpMode("create");
                            setCreateMcpServerName("");
                            setCreateMcpDraft(getEmptyMcpDraft());
                          }}
                          disabled={!!pending}
                        >
                          New MCP server
                        </button>
                      </div>
                    </div>

                    {mcpMode === "create" ? (
                      <div className="mcp-editor">
                        <div className="panel-head">
                          <h3>Create definition</h3>
                          <code>{createMcpServerName || "new server"}</code>
                        </div>
                        <div className="field-grid">
                          <label>
                            <span>Name</span>
                            <input
                              value={createMcpServerName}
                              onChange={(event) => setCreateMcpServerName(event.target.value)}
                              placeholder="github"
                            />
                          </label>
                          <label>
                            <span>Type</span>
                            <select
                              value={createMcpDraft.type}
                              onChange={(event) => setCreateMcpDraft((current) => ({ ...current, type: event.target.value }))}
                            >
                              <option value="stdio">stdio</option>
                              <option value="sse">sse</option>
                              <option value="streamableHttp">streamableHttp</option>
                            </select>
                          </label>
                          <label>
                            <span>Tool timeout</span>
                            <input
                              value={createMcpDraft.tool_timeout}
                              onChange={(event) =>
                                setCreateMcpDraft((current) => ({ ...current, tool_timeout: event.target.value }))
                              }
                              placeholder="30"
                            />
                          </label>
                          {createMcpDraft.type === "stdio" ? (
                            <>
                              <label>
                                <span>Command</span>
                                <input
                                  value={createMcpDraft.command}
                                  onChange={(event) =>
                                    setCreateMcpDraft((current) => ({ ...current, command: event.target.value }))
                                  }
                                  placeholder="npx"
                                />
                              </label>
                              <label>
                                <span>Args, one per line</span>
                                <textarea
                                  value={createMcpDraft.args}
                                  onChange={(event) =>
                                    setCreateMcpDraft((current) => ({ ...current, args: event.target.value }))
                                  }
                                />
                              </label>
                              <label>
                                <span>Env JSON</span>
                                <textarea
                                  value={createMcpDraft.env}
                                  onChange={(event) =>
                                    setCreateMcpDraft((current) => ({ ...current, env: event.target.value }))
                                  }
                                />
                              </label>
                            </>
                          ) : (
                            <>
                              <label>
                                <span>URL</span>
                                <input
                                  value={createMcpDraft.url}
                                  onChange={(event) => setCreateMcpDraft((current) => ({ ...current, url: event.target.value }))}
                                  placeholder="https://example.com/mcp"
                                />
                              </label>
                              <label>
                                <span>Headers JSON</span>
                                <textarea
                                  value={createMcpDraft.headers}
                                  onChange={(event) =>
                                    setCreateMcpDraft((current) => ({ ...current, headers: event.target.value }))
                                  }
                                />
                              </label>
                            </>
                          )}
                          <label className="checkbox">
                            <input
                              type="checkbox"
                              checked={createMcpDraft.use_enabled_tools}
                              onChange={(event) =>
                                setCreateMcpDraft((current) => ({
                                  ...current,
                                  use_enabled_tools: event.target.checked,
                                  enabled_tools: event.target.checked ? current.enabled_tools : "",
                                }))
                              }
                            />
                            <span>Whitelist tools</span>
                          </label>
                          {createMcpDraft.use_enabled_tools ? (
                            <label>
                              <span>Whitelisted tools, one per line</span>
                              <textarea
                                value={createMcpDraft.enabled_tools}
                                onChange={(event) =>
                                  setCreateMcpDraft((current) => ({ ...current, enabled_tools: event.target.value }))
                                }
                              />
                            </label>
                          ) : null}
                        </div>
                        <div className="action-row">
                          <button
                            className="ghost"
                            onClick={() => {
                              setCreateMcpServerName("");
                              setCreateMcpDraft(getEmptyMcpDraft());
                              setMcpMode(selectedMcpServer ? "view" : "create");
                            }}
                            disabled={!!pending}
                          >
                            Cancel
                          </button>
                          <button onClick={() => void createMcpServer()} disabled={!!pending}>
                            Create MCP server
                          </button>
                        </div>
                      </div>
                    ) : selectedMcpServer ? (
                      <div className="mcp-editor">
                        <div className="panel-head">
                          <h3>{mcpMode === "edit" ? "Edit definition" : "Definition"}</h3>
                          <code>{selectedMcpServer.name}</code>
                        </div>
                        {mcpMode === "edit" ? (
                          <div className="field-grid">
                            <label>
                              <span>Type</span>
                              <select
                                value={activeMcpDraft.type}
                                onChange={(event) => setMcpDraft((current) => ({ ...current, type: event.target.value }))}
                              >
                                <option value="stdio">stdio</option>
                                <option value="sse">sse</option>
                                <option value="streamableHttp">streamableHttp</option>
                              </select>
                            </label>
                            <label>
                              <span>Tool timeout</span>
                              <input
                                value={activeMcpDraft.tool_timeout}
                                onChange={(event) => setMcpDraft((current) => ({ ...current, tool_timeout: event.target.value }))}
                                placeholder="30"
                              />
                            </label>
                            {activeMcpDraft.type === "stdio" ? (
                              <>
                                <label>
                                  <span>Command</span>
                                  <input
                                    value={activeMcpDraft.command}
                                    onChange={(event) => setMcpDraft((current) => ({ ...current, command: event.target.value }))}
                                    placeholder="npx"
                                  />
                                </label>
                                <label>
                                  <span>Args, one per line</span>
                                  <textarea
                                    value={activeMcpDraft.args}
                                    onChange={(event) => setMcpDraft((current) => ({ ...current, args: event.target.value }))}
                                  />
                                </label>
                                <label>
                                  <span>Env JSON</span>
                                  <textarea
                                    value={activeMcpDraft.env}
                                    onChange={(event) => setMcpDraft((current) => ({ ...current, env: event.target.value }))}
                                  />
                                </label>
                              </>
                            ) : (
                              <>
                                <label>
                                  <span>URL</span>
                                  <input
                                    value={activeMcpDraft.url}
                                    onChange={(event) => setMcpDraft((current) => ({ ...current, url: event.target.value }))}
                                    placeholder="https://example.com/mcp"
                                  />
                                </label>
                                <label>
                                  <span>Headers JSON</span>
                                  <textarea
                                    value={activeMcpDraft.headers}
                                    onChange={(event) => setMcpDraft((current) => ({ ...current, headers: event.target.value }))}
                                  />
                                </label>
                              </>
                            )}
                            <label className="checkbox">
                              <input
                                type="checkbox"
                                checked={activeMcpDraft.use_enabled_tools}
                                onChange={(event) =>
                                  setMcpDraft((current) => ({
                                    ...current,
                                    use_enabled_tools: event.target.checked,
                                    enabled_tools: event.target.checked ? current.enabled_tools : "",
                                  }))
                                }
                              />
                              <span>Whitelist tools</span>
                            </label>
                            {activeMcpDraft.use_enabled_tools ? (
                              <label>
                                <span>Whitelisted tools, one per line</span>
                                <textarea
                                  value={activeMcpDraft.enabled_tools}
                                  onChange={(event) =>
                                    setMcpDraft((current) => ({ ...current, enabled_tools: event.target.value }))
                                  }
                                />
                              </label>
                            ) : null}
                          </div>
                        ) : (
                          <div className="override-grid">
                            <article className="override-card">
                              <span>Type</span>
                              <strong>{renderMcpTypeLabel(selectedMcpServer.config.type)}</strong>
                            </article>
                            <article className="override-card">
                              <span>Command</span>
                              <strong>{selectedMcpServer.config.command || "n/a"}</strong>
                            </article>
                            <article className="override-card">
                              <span>URL</span>
                              <strong>{selectedMcpServer.config.url || "n/a"}</strong>
                            </article>
                            <article className="override-card">
                              <span>Tool timeout</span>
                              <strong>{String(selectedMcpServer.config.toolTimeout)}</strong>
                            </article>
                            <article className="override-card">
                              <span>Args</span>
                              <strong>{renderEnabledList(selectedMcpServer.config.args)}</strong>
                            </article>
                            <article className="override-card">
                              <span>Whitelist</span>
                              <strong>
                                {selectedMcpServer.config.enabledTools.length === 1 &&
                                selectedMcpServer.config.enabledTools[0] === "*"
                                  ? "all tools"
                                  : renderEnabledList(selectedMcpServer.config.enabledTools)}
                              </strong>
                            </article>
                            <article className="override-card">
                              <span>Env</span>
                              <strong>{Object.keys(selectedMcpServer.config.env).length ? JSON.stringify(selectedMcpServer.config.env) : "none"}</strong>
                            </article>
                            <article className="override-card">
                              <span>Headers</span>
                              <strong>{Object.keys(selectedMcpServer.config.headers).length ? JSON.stringify(selectedMcpServer.config.headers) : "none"}</strong>
                            </article>
                          </div>
                        )}
                        <div className="action-row">
                          {mcpMode === "edit" ? (
                            <>
                              <button
                                className="ghost"
                                onClick={() => {
                                  setMcpDraft(mcpConfigToDraft(selectedMcpServer.config));
                                  setMcpMode("view");
                                }}
                                disabled={!!pending}
                              >
                                Cancel
                              </button>
                              <button onClick={() => void updateMcpServer()} disabled={!!pending}>
                                Save MCP server
                              </button>
                              <button className="danger" onClick={() => void deleteMcpServer()} disabled={!!pending}>
                                Delete
                              </button>
                            </>
                          ) : (
                            <>
                              <button
                                className="ghost"
                                onClick={() => {
                                  setMcpDraft(mcpConfigToDraft(selectedMcpServer.config));
                                  setMcpMode("edit");
                                }}
                                disabled={!!pending}
                              >
                                Edit
                              </button>
                              <button className="danger" onClick={() => void deleteMcpServer()} disabled={!!pending}>
                                Delete
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                    ) : null}
                  </div>
                </section>
                </div>
            </>
          ) : (
            <p className="muted">Select a soul to inspect or create one from the draft form.</p>
          )}
        </section>

        {sessionKey ? (
        <section className="panel chat-panel">
          <div className="panel-head">
            <h2>Live chat</h2>
            <div className="chat-meta">
              <span className={`pill ${socketState === "open" ? "live" : "idle"}`}>
                {socketState === "open" ? "open" : socketState}
              </span>
              <code>{sessionKey}</code>
            </div>
          </div>

          <article className="finalized-box">
            <div className="panel-head">
              <h3>Message history</h3>
              {sessionDetail ? <span className="muted">last consolidated {sessionDetail.last_consolidated}</span> : null}
            </div>
            <div className="message-list">
              {hasStreamingTurn ? (
                <div className="message-card streaming">
                  <div className="message-head">
                    <strong>assistant</strong>
                    <div className="message-head-actions">
                      <span className="muted">streaming</span>
                      <button
                        type="button"
                        className="ghost message-copy-button"
                        onClick={() => {
                          void copyToClipboard(chatContent).then(() => {
                            toast.success("Copied response");
                          }).catch((cause) => {
                            notifyError(cause);
                          });
                        }}
                        disabled={!chatContent}
                      >
                        Copy
                      </button>
                    </div>
                  </div>
                  {chatReasoning ? (
                    <details>
                      <summary>Reasoning</summary>
                      <pre>{chatReasoning}</pre>
                    </details>
                  ) : null}
                  {chatContent ? <MarkdownMessage content={chatContent} /> : <pre>(waiting for content)</pre>}
                </div>
              ) : null}
              {chatHistory.map((message, index) => {
                const role = typeof message.role === "string" ? message.role : "unknown";
                const toolCallId = typeof message.tool_call_id === "string" ? message.tool_call_id : null;
                const toolCalls = Array.isArray(message.tool_calls) ? message.tool_calls : null;
                const content = "content" in message ? message.content : null;
                const reasoning = getMessageReasoning(message);
                const renderedContent = renderContent(content) || "(empty)";
                return (
                <div key={`${role}-${index}`} className="message-card">
                  <div className="message-head">
                    <strong>{role}</strong>
                    <div className="message-head-actions">
                      {toolCallId ? <code>{toolCallId}</code> : null}
                      {role === "assistant" ? (
                        <button
                          type="button"
                          className="ghost message-copy-button"
                          onClick={() => {
                            void copyToClipboard(renderedContent).then(() => {
                              toast.success("Copied response");
                            }).catch((cause) => {
                              notifyError(cause);
                            });
                          }}
                        >
                          Copy
                        </button>
                      ) : null}
                    </div>
                  </div>
                  {reasoning ? (
                    <details>
                      <summary>Reasoning</summary>
                      <pre>{reasoning}</pre>
                    </details>
                  ) : null}
                  {toolCalls ? (
                    <details className="tool-result-details">
                      <summary>Tool calls ({toolCalls.length})</summary>
                      <pre>{JSON.stringify(toolCalls, null, 2)}</pre>
                    </details>
                  ) : null}
                  {role === "tool" ? (
                    <details className="tool-result-details">
                      <summary>Result: {summarizeToolResult(content)}</summary>
                      <pre>{renderedContent}</pre>
                    </details>
                  ) : role === "assistant" ? (
                    <MarkdownMessage content={renderedContent} />
                  ) : (
                    <pre>{renderedContent}</pre>
                  )}
                </div>
                );
              })}
              {!chatHistory.length ? <p className="muted">Open a session to load its message history.</p> : null}
            </div>
          </article>

          <form className="chat-form" onSubmit={(event) => void submitChat(event)}>
            <textarea
              value={chatInput}
              onChange={(event) => setChatInput(event.target.value)}
              placeholder={selectedSoul?.running ? "Send a message to the running soul" : "Start a soul to chat"}
              disabled={!selectedSoul?.running || socketState !== "open"}
            />
            <button type="submit" disabled={!chatInput.trim() || !selectedSoul?.running || socketState !== "open"}>
              Send
            </button>
          </form>
        </section>
        ) : null}

      </main>
    </div>
  );
}
