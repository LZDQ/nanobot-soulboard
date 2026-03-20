import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

type SoulOverrides = {
  workspace?: string | null;
  model?: string | null;
  provider?: string | null;
  channels: string[];
  mcp_servers: string[];
  autostart: boolean;
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
  soul_id: string;
  key: string;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
  last_consolidated: number;
  messages: Array<Record<string, unknown>>;
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
  mcp_servers: string;
  autostart: boolean;
};

const DEFAULT_CHAT_CHANNEL = "cli";
const DEFAULT_CHAT_ID = "direct";

function getApiBase(): string {
  return (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";
}

function getWsBase(): string {
  const explicit = import.meta.env.VITE_WS_BASE as string | undefined;
  if (explicit) {
    return explicit.replace(/\/$/, "");
  }
  const url = new URL(window.location.href);
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

function overridesToDraft(overrides: SoulOverrides): DraftOverrides {
  return {
    workspace: overrides.workspace ?? "",
    model: overrides.model ?? "",
    provider: overrides.provider ?? "",
    channels: overrides.channels.join(", "),
    mcp_servers: overrides.mcp_servers.join(", "),
    autostart: overrides.autostart,
  };
}

function draftToOverrides(draft: DraftOverrides): SoulOverrides {
  return {
    workspace: draft.workspace || null,
    model: draft.model || null,
    provider: draft.provider || null,
    channels: splitCsv(draft.channels),
    mcp_servers: splitCsv(draft.mcp_servers),
    autostart: draft.autostart,
  };
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

export default function App() {
  const [souls, setSouls] = useState<Soul[]>([]);
  const [selectedSoulId, setSelectedSoulId] = useState<string>("");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionDetail, setSessionDetail] = useState<SessionDetail | null>(null);
  const [sessionKey, setSessionKey] = useState("cli:direct");
  const [chatInput, setChatInput] = useState("");
  const [chatReasoning, setChatReasoning] = useState("");
  const [chatContent, setChatContent] = useState("");
  const [finalizedMessages, setFinalizedMessages] = useState<StreamFinalizedMessage[]>([]);
  const [pending, setPending] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [createSoulId, setCreateSoulId] = useState("");
  const [draft, setDraft] = useState<DraftOverrides>({
    workspace: "",
    model: "",
    provider: "",
    channels: "",
    mcp_servers: "",
    autostart: false,
  });
  const [socketState, setSocketState] = useState<"closed" | "connecting" | "open">("closed");
  const socketRef = useRef<WebSocket | null>(null);

  const selectedSoul = useMemo(
    () => souls.find((soul) => soul.soul_id === selectedSoulId) ?? null,
    [souls, selectedSoulId],
  );

  async function refreshSouls(preferredSoulId?: string): Promise<void> {
    const nextSouls = await api<Soul[]>("/api/souls");
    setSouls(nextSouls);
    if (!nextSouls.length) {
      setSelectedSoulId("");
      setSessions([]);
      setSessionDetail(null);
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

  useEffect(() => {
    void (async () => {
      try {
        await refreshSouls();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedSoul) {
      return;
    }
    setDraft(overridesToDraft(selectedSoul.overrides));
    setError("");
    void refreshSessions(selectedSoul.soul_id).catch((cause) => {
      setError(cause instanceof Error ? cause.message : String(cause));
    });
  }, [selectedSoul?.soul_id]);

  useEffect(() => {
    const soulId = selectedSoul?.soul_id;
    if (!soulId || !selectedSoul.running) {
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
        setChatContent(message.content ?? "");
        setChatReasoning(message.reasoning_content ?? "");
        setFinalizedMessages([]);
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
  }, [selectedSoul?.soul_id, selectedSoul?.running, sessionKey]);

  async function runAction(action: string, work: () => Promise<void>) {
    setPending(action);
    setError("");
    try {
      await work();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPending("");
    }
  }

  async function createSoul() {
    if (!createSoulId.trim()) {
      setError("soul_id is required");
      return;
    }
    await runAction("create", async () => {
      const created = await api<Soul>("/api/souls", {
        method: "POST",
        body: JSON.stringify({
          soul_id: createSoulId.trim(),
          overrides: draftToOverrides(draft),
        }),
      });
      setCreateSoulId("");
      await refreshSouls(created.soul_id);
      await refreshSessions(created.soul_id);
    });
  }

  async function updateSoul() {
    if (!selectedSoul) {
      return;
    }
    await runAction("update", async () => {
      await api<Soul>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}`, {
        method: "PATCH",
        body: JSON.stringify({ overrides: draftToOverrides(draft) }),
      });
      await refreshSouls(selectedSoul.soul_id);
    });
  }

  async function toggleSoulRunning() {
    if (!selectedSoul) {
      return;
    }
    await runAction(selectedSoul.running ? "stop" : "start", async () => {
      const action = selectedSoul.running ? "stop" : "start";
      await api<Soul>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/${action}`, {
        method: "POST",
      });
      await refreshSouls(selectedSoul.soul_id);
    });
  }

  async function deleteSoul() {
    if (!selectedSoul) {
      return;
    }
    const soulId = selectedSoul.soul_id;
    await runAction("delete", async () => {
      await api<void>(`/api/souls/${encodeURIComponent(soulId)}`, { method: "DELETE" });
      await refreshSouls();
    });
  }

  async function loadSession(key: string) {
    if (!selectedSoul) {
      return;
    }
    await runAction("session", async () => {
      const detail = await api<SessionDetail>(
        `/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/sessions/${encodeURIComponent(key)}`,
      );
      setSessionDetail(detail);
      setSessionKey(key);
    });
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

  return (
    <div className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">nanobot soulboard</p>
          <h1>Operator console for soul switching, session review, and streamed chat.</h1>
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
          <div className="stat-card">
            <span>socket</span>
            <strong>{socketState}</strong>
          </div>
        </div>
      </header>

      {error ? <section className="banner error">{error}</section> : null}

      <main className="grid">
        <section className="panel souls-panel">
          <div className="panel-head">
            <h2>Souls</h2>
            <button className="ghost" onClick={() => void refreshSouls(selectedSoulId)} disabled={!!pending}>
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
            <label>
              <span>Soul ID</span>
              <input value={createSoulId} onChange={(event) => setCreateSoulId(event.target.value)} placeholder="reviewer" />
            </label>
            <button onClick={() => void createSoul()} disabled={!!pending}>
              Create from current draft
            </button>
          </div>
        </section>

        <section className="panel details-panel">
          <div className="panel-head">
            <h2>Selected soul</h2>
            {selectedSoul ? <code>{selectedSoul.soul_id}</code> : null}
          </div>
          {selectedSoul ? (
            <>
              <div className="action-row">
                <button onClick={() => void toggleSoulRunning()} disabled={!!pending}>
                  {selectedSoul.running ? "Stop soul" : "Start soul"}
                </button>
                <button className="ghost" onClick={() => void updateSoul()} disabled={!!pending}>
                  Save overrides
                </button>
                <button className="danger" onClick={() => void deleteSoul()} disabled={!!pending}>
                  Delete soul
                </button>
              </div>

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
                  <input
                    value={draft.mcp_servers}
                    onChange={(event) => setDraft((current) => ({ ...current, mcp_servers: event.target.value }))}
                    placeholder="filesystem, github"
                  />
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
            </>
          ) : (
            <p className="muted">Select a soul to inspect or create one from the draft form.</p>
          )}
        </section>

        <section className="panel sessions-panel">
          <div className="panel-head">
            <h2>Sessions</h2>
            {selectedSoul ? (
              <button className="ghost" onClick={() => void refreshSessions(selectedSoul.soul_id)} disabled={!!pending}>
                Reload
              </button>
            ) : null}
          </div>
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
          {sessionDetail ? (
            <article className="session-detail">
              <div className="panel-head">
                <h3>{sessionDetail.key}</h3>
                <span className="muted">last consolidated {sessionDetail.last_consolidated}</span>
              </div>
              <pre>{JSON.stringify(sessionDetail.messages, null, 2)}</pre>
            </article>
          ) : null}
        </section>

        <section className="panel chat-panel">
          <div className="panel-head">
            <h2>Live chat</h2>
            <div className="chat-meta">
              <span className={`pill ${socketState === "open" ? "live" : "idle"}`}>{socketState}</span>
              <input value={sessionKey} onChange={(event) => setSessionKey(event.target.value)} />
            </div>
          </div>

          <div className="stream-grid">
            <article className="stream-box reasoning">
              <h3>Reasoning stream</h3>
              <pre>{chatReasoning || "Waiting for stream output."}</pre>
            </article>
            <article className="stream-box answer">
              <h3>Content stream</h3>
              <pre>{chatContent || "No streamed content yet."}</pre>
            </article>
          </div>

          <article className="finalized-box">
            <h3>Finalized messages</h3>
            <div className="message-list">
              {finalizedMessages.map((message, index) => (
                <div key={`${message.role}-${index}`} className="message-card">
                  <div className="message-head">
                    <strong>{message.role}</strong>
                    {message.tool_call_id ? <code>{message.tool_call_id}</code> : null}
                  </div>
                  {message.tool_calls ? <pre>{JSON.stringify(message.tool_calls, null, 2)}</pre> : null}
                  <pre>{renderContent(message.content) || "(empty)"}</pre>
                </div>
              ))}
              {!finalizedMessages.length ? <p className="muted">No finalized messages for the current turn.</p> : null}
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
      </main>
    </div>
  );
}
