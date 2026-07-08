import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Toaster, toast } from "sonner";

import { CreateSoulDialog } from "./components/CreateSoulDialog";
import { GroupListEditor } from "./components/GroupListEditor";
import { MarkdownMessage } from "./components/MarkdownMessage";
import { api, getWsBase } from "./lib/api";
import { copyToClipboard } from "./lib/clipboard";
import {
  draftToMcpPayload,
  draftToOverrides,
  getEmptyDraftOverrides,
  getEmptyMcpDraft,
  getEmptyPromptDraft,
  getEmptyPromptSelection,
  mcpConfigToDraft,
  overridesToDraft,
  promptFilesToDraft,
  promptFilesToSelection,
  updateSoulMcpSelection,
} from "./lib/drafts";
import { getErrorMessage, notifyError } from "./lib/errors";
import {
  formatCronSchedule,
  formatDate,
  formatTimestampMs,
  getMessageReasoning,
  renderContent,
  renderEnabledList,
  renderHeaderOverrideSummary,
  renderMcpTypeLabel,
  renderOverrideValue,
  summarizeToolResult,
} from "./lib/format";
import { appendMessages, prependSessionWindow } from "./lib/sessionWindows";
import { getFocusFromUrl, syncFocusToUrl } from "./lib/urlFocus";
import {
  DEFAULT_CHAT_CHANNEL,
  DEFAULT_CHAT_ID,
  EMPTY_CRON_CREATE_DRAFT,
  SOUL_PROMPT_FILE_NAMES,
  type CreateSoulSkillDraft,
  type CronJob,
  type CronJobCreateDraft,
  type CronJobEditDraft,
  type CronJobRegistryEntry,
  type CronJobRegistryEntryDraft,
  type CronJobRegistryResponse,
  type DraftOverrides,
  type MCPServer,
  type MCPServerDraft,
  type SessionDetail,
  type SessionListResponse,
  type SessionSummary,
  type SkillPool,
  type SkillRegistryResponse,
  type Soul,
  type SoulPromptDraft,
  type SoulPromptFile,
  type SoulPromptFileName,
  type SoulPromptFilesResponse,
  type SoulSkill,
  type StreamFinalizedMessage,
  type StreamMessage,
} from "./types";

export default function App() {
  const initialFocusRef = useRef(getFocusFromUrl());

  const [souls, setSouls] = useState<Soul[]>([]);
  const [selectedSoulId, setSelectedSoulId] = useState<string>(initialFocusRef.current.soulId);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);
  const [skillPools, setSkillPools] = useState<SkillPool[]>([]);
  const [cronJobRegistry, setCronJobRegistry] = useState<CronJobRegistryEntry[]>([]);
  const [promptFiles, setPromptFiles] = useState<SoulPromptFile[]>([]);
  const [cronJobs, setCronJobs] = useState<CronJob[] | null>(null);
  const [selectedMcpServerName, setSelectedMcpServerName] = useState<string>("");
  const [createMcpServerName, setCreateMcpServerName] = useState("");
  const [createSessionKey, setCreateSessionKey] = useState("");
  const [newSkillRegistryPath, setNewSkillRegistryPath] = useState("");
  const [sessionDetail, setSessionDetail] = useState<SessionDetail | null>(null);
  const [sessionKey, setSessionKey] = useState<string | null>(initialFocusRef.current.sessionKey);
  const [olderMessagesPending, setOlderMessagesPending] = useState(false);
  const [socketEpoch, setSocketEpoch] = useState(0);
  const [chatInput, setChatInput] = useState("");
  const [chatReasoning, setChatReasoning] = useState("");
  const [chatContent, setChatContent] = useState("");
  const [finalizedMessages, setFinalizedMessages] = useState<StreamFinalizedMessage[]>([]);
  const [pending, setPending] = useState<string>("");
  const [soulError, setSoulError] = useState<string>("");
  const [createSoulId, setCreateSoulId] = useState("");
  const [draft, setDraft] = useState<DraftOverrides>(getEmptyDraftOverrides());
  const [createSoulDraft, setCreateSoulDraft] = useState<DraftOverrides>(getEmptyDraftOverrides());
  const [mcpDraft, setMcpDraft] = useState<MCPServerDraft>(getEmptyMcpDraft());
  const [createMcpDraft, setCreateMcpDraft] = useState<MCPServerDraft>(getEmptyMcpDraft());
  const [promptDraft, setPromptDraft] = useState<SoulPromptDraft>(getEmptyPromptDraft());
  const [promptSelection, setPromptSelection] = useState<Record<SoulPromptFileName, boolean>>(getEmptyPromptSelection());
  const [isEditingSoul, setIsEditingSoul] = useState(false);
  const [isEditingSkillRegistry, setIsEditingSkillRegistry] = useState(false);
  const [isEditingCronJobRegistry, setIsEditingCronJobRegistry] = useState(false);
  const [cronJobRegistryDraft, setCronJobRegistryDraft] = useState<CronJobRegistryEntryDraft>({
    name: "", label: "", cron_expr: "", every_seconds: "", tz: Intl.DateTimeFormat().resolvedOptions().timeZone, message: "",
    deliver: false, channel: "", chat_id: "", session_key: "", recurring_session_key_format: "",
  });
  const [addCronJobRegistrySelection, setAddCronJobRegistrySelection] = useState("");
  const [createSoulCronJobNames, setCreateSoulCronJobNames] = useState<string[]>([]);
  const [createSoulSkillDrafts, setCreateSoulSkillDrafts] = useState<Record<string, CreateSoulSkillDraft>>({});
  const [addSkillSelection, setAddSkillSelection] = useState("");
  const [addSkillMode, setAddSkillMode] = useState<"symlink" | "copy">("symlink");
  const [addSkillTargetName, setAddSkillTargetName] = useState("");
  const [isEditingPromptFiles, setIsEditingPromptFiles] = useState(false);
  const [isCreatingSoul, setIsCreatingSoul] = useState(false);
  const [showOnlySelectedSessionCronJobs, setShowOnlySelectedSessionCronJobs] = useState(true);
  const [editingCronJobId, setEditingCronJobId] = useState<string | null>(null);
  const [cronJobEditDraft, setCronJobEditDraft] = useState<CronJobEditDraft>({
    name: "", enabled: true, message: "", deliver: false, channel: "", chat_id: "", session_key: "",
    delete_after_run: false, schedule_kind: "cron", every_seconds: "", cron_expr: "", tz: "",
  });
  const [isCreatingCronJob, setIsCreatingCronJob] = useState(false);
  const [cronJobCreateDraft, setCronJobCreateDraft] = useState<CronJobCreateDraft>(EMPTY_CRON_CREATE_DRAFT);
  const [mcpMode, setMcpMode] = useState<"view" | "edit" | "create">("view");
  const [socketState, setSocketState] = useState<"closed" | "connecting" | "open">("closed");
  const [soulGroupFilter, setSoulGroupFilter] = useState<string>("");
  const [sessionsPage, setSessionsPage] = useState<number>(0);
  const [sessionsPerPage, setSessionsPerPage] = useState<number>(10);
  const [sessionSortOrder, setSessionSortOrder] = useState<"desc" | "asc">("desc");
  const [sessionsTotal, setSessionsTotal] = useState<number>(0);
  const socketRef = useRef<WebSocket | null>(null);
  const initializedRef = useRef(false);

  const selectedSoul = useMemo(
    () => souls.find((soul) => soul.soul_id === selectedSoulId) ?? null,
    [souls, selectedSoulId],
  );
  const allSoulGroups = useMemo(() => {
    const seen = new Set<string>();
    for (const soul of souls) {
      for (const group of soul.overrides.groups ?? []) {
        if (group) seen.add(group);
      }
    }
    return Array.from(seen).sort((a, b) => a.localeCompare(b));
  }, [souls]);
  const visibleSouls = useMemo(() => {
    if (!soulGroupFilter) return souls;
    return souls.filter((soul) => (soul.overrides.groups ?? []).includes(soulGroupFilter));
  }, [souls, soulGroupFilter]);
  const sessionsTotalPages = Math.max(1, Math.ceil(sessionsTotal / sessionsPerPage));
  const clampedSessionsPage = Math.min(sessionsPage, sessionsTotalPages - 1);
  const visibleCronJobs = useMemo(() => {
    if (!cronJobs) {
      return cronJobs;
    }
    if (!showOnlySelectedSessionCronJobs || !sessionKey) {
      return cronJobs;
    }
    return cronJobs.filter((job) => job.session_key === sessionKey);
  }, [cronJobs, sessionKey, showOnlySelectedSessionCronJobs]);

  async function refreshSouls(preferredSoulId?: string, reloadConfig = false): Promise<void> {
    let nextSouls: Soul[];
    try {
      nextSouls = reloadConfig
        ? await api<Soul[]>("/api/souls/refresh", { method: "POST" })
        : await api<Soul[]>("/api/souls");
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

  async function refreshSkillRegistry(): Promise<void> {
    const response = await api<SkillRegistryResponse>("/api/skill-registry");
    setSkillPools(response.pools);
  }

  async function reloadSkillPools(): Promise<void> {
    await runAction("skill-registry-refresh", async () => {
      const response = await api<SkillRegistryResponse>("/api/skill-registry/refresh", {
        method: "POST",
      });
      setSkillPools(response.pools);
      toast.success("Skill pools reloaded");
    }).catch((cause) => {
      notifyError(cause);
    });
  }

  async function refreshCronJobRegistry(): Promise<void> {
    const response = await api<CronJobRegistryResponse>("/api/cron-job-registry");
    setCronJobRegistry(response.items);
  }

  async function saveCronJobRegistry(items: CronJobRegistryEntry[]) {
    await runAction("cron-job-registry", async () => {
      const response = await api<CronJobRegistryResponse>("/api/cron-job-registry", {
        method: "PATCH",
        body: JSON.stringify({ items }),
      });
      setCronJobRegistry(response.items);
      setIsEditingCronJobRegistry(false);
      setCronJobRegistryDraft({
        name: "", label: "", cron_expr: "", every_seconds: "", tz: Intl.DateTimeFormat().resolvedOptions().timeZone, message: "",
        deliver: false, channel: "", chat_id: "", session_key: "", recurring_session_key_format: "",
      });
      if (addCronJobRegistrySelection && !response.items.some((e) => e.name === addCronJobRegistrySelection)) {
        setAddCronJobRegistrySelection("");
      }
    }).catch((cause) => {
      notifyError(cause);
    });
  }

  async function addCronJobRegistryEntry() {
    const name = cronJobRegistryDraft.name.trim();
    if (!name) { notifyError("Entry name is required"); return; }
    if (!cronJobRegistryDraft.cron_expr.trim() && !cronJobRegistryDraft.every_seconds.trim()) {
      notifyError("Provide cron_expr or every_seconds");
      return;
    }
    const everySeconds = cronJobRegistryDraft.every_seconds.trim()
      ? parseInt(cronJobRegistryDraft.every_seconds.trim(), 10)
      : null;
    if (cronJobRegistryDraft.every_seconds.trim() && (isNaN(everySeconds!) || everySeconds! <= 0)) {
      notifyError("every_seconds must be a positive integer");
      return;
    }
    const entry: CronJobRegistryEntry = {
      name,
      label: cronJobRegistryDraft.label.trim() || null,
      cron_expr: cronJobRegistryDraft.cron_expr.trim() || null,
      every_seconds: everySeconds,
      tz: cronJobRegistryDraft.tz.trim() || null,
      message: cronJobRegistryDraft.message,
      deliver: cronJobRegistryDraft.deliver,
      channel: cronJobRegistryDraft.channel.trim() || null,
      chat_id: cronJobRegistryDraft.chat_id.trim() || null,
      session_key: cronJobRegistryDraft.session_key.trim() || null,
      recurring_session_key_format: cronJobRegistryDraft.recurring_session_key_format.trim() || null,
    };
    await saveCronJobRegistry([...cronJobRegistry, entry]);
  }

  async function deleteCronJobRegistryEntry(name: string) {
    await saveCronJobRegistry(cronJobRegistry.filter((e) => e.name !== name));
  }

  async function moveCronJobRegistryEntry(name: string, direction: -1 | 1) {
    const idx = cronJobRegistry.findIndex((e) => e.name === name);
    if (idx < 0) return;
    const next = idx + direction;
    if (next < 0 || next >= cronJobRegistry.length) return;
    const reordered = [...cronJobRegistry];
    [reordered[idx], reordered[next]] = [reordered[next], reordered[idx]];
    await saveCronJobRegistry(reordered);
  }

  async function addSoulCronJobsFromRegistry() {
    if (!selectedSoul || !addCronJobRegistrySelection) return;
    await runAction("cron-from-registry", async () => {
      await api<CronJob[]>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/cron-jobs-from-registry`, {
        method: "POST",
        body: JSON.stringify({ names: [addCronJobRegistrySelection] }),
      });
      setAddCronJobRegistrySelection("");
      await refreshCronJobs(selectedSoul.soul_id);
    }).catch((cause) => {
      notifyError(cause);
    });
  }

  async function deleteSoulCronJob(jobId: string) {
    if (!selectedSoul) return;
    await runAction("cron-delete", async () => {
      await api<void>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/cron-jobs/${encodeURIComponent(jobId)}`, {
        method: "DELETE",
      });
      await refreshCronJobs(selectedSoul.soul_id);
    }).catch((cause) => {
      notifyError(cause);
    });
  }

  function startEditCronJob(job: CronJob) {
    setCronJobEditDraft({
      name: job.name,
      enabled: job.enabled,
      message: job.message,
      deliver: job.deliver,
      channel: job.channel ?? "",
      chat_id: job.chat_id ?? "",
      session_key: job.session_key ?? "",
      delete_after_run: job.delete_after_run,
      schedule_kind: job.schedule.kind === "every" ? "every" : "cron",
      every_seconds: job.schedule.every_ms ? String(job.schedule.every_ms / 1000) : "",
      cron_expr: job.schedule.expr ?? "",
      tz: job.schedule.tz ?? "",
    });
    setEditingCronJobId(job.id);
  }

  async function updateSoulCronJob(jobId: string) {
    if (!selectedSoul) return;
    if (cronJobEditDraft.schedule_kind === "every") {
      const everySeconds = parseInt(cronJobEditDraft.every_seconds, 10);
      if (isNaN(everySeconds) || everySeconds <= 0) {
        notifyError("every_seconds must be a positive integer");
        return;
      }
    }
    await runAction("cron-update", async () => {
      const schedule = cronJobEditDraft.schedule_kind === "every"
        ? { kind: "every", every_ms: parseInt(cronJobEditDraft.every_seconds, 10) * 1000 }
        : { kind: "cron", expr: cronJobEditDraft.cron_expr.trim(), tz: cronJobEditDraft.tz.trim() || null };
      const body: Record<string, unknown> = {
        name: cronJobEditDraft.name.trim() || null,
        enabled: cronJobEditDraft.enabled,
        message: cronJobEditDraft.message,
        deliver: cronJobEditDraft.deliver,
        channel: cronJobEditDraft.channel.trim() || null,
        chat_id: cronJobEditDraft.chat_id.trim() || null,
        session_key: cronJobEditDraft.session_key.trim() || null,
        delete_after_run: cronJobEditDraft.delete_after_run,
        schedule,
      };
      await api<CronJob>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/cron-jobs/${encodeURIComponent(jobId)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setEditingCronJobId(null);
      await refreshCronJobs(selectedSoul.soul_id);
    }).catch((cause) => {
      notifyError(cause);
    });
  }

  async function createSoulCronJob() {
    if (!selectedSoul) return;
    const name = cronJobCreateDraft.name.trim();
    if (!name) {
      notifyError("Cron job name is required");
      return;
    }
    let schedule: Record<string, unknown>;
    if (cronJobCreateDraft.schedule_kind === "every") {
      const everySeconds = parseInt(cronJobCreateDraft.every_seconds, 10);
      if (isNaN(everySeconds) || everySeconds <= 0) {
        notifyError("every_seconds must be a positive integer");
        return;
      }
      schedule = { kind: "every", every_ms: everySeconds * 1000 };
    } else {
      const expr = cronJobCreateDraft.cron_expr.trim();
      if (!expr) {
        notifyError("Cron expression is required");
        return;
      }
      schedule = { kind: "cron", expr, tz: cronJobCreateDraft.tz.trim() || null };
    }
    await runAction("cron-create", async () => {
      const body = {
        name,
        message: cronJobCreateDraft.message,
        deliver: cronJobCreateDraft.deliver,
        channel: cronJobCreateDraft.channel.trim() || null,
        chat_id: cronJobCreateDraft.chat_id.trim() || null,
        session_key: cronJobCreateDraft.session_key.trim() || null,
        recurring_session_key_format:
          cronJobCreateDraft.recurring_session_key_format.trim() || null,
        delete_after_run: cronJobCreateDraft.delete_after_run,
        schedule,
      };
      await api<CronJob>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/cron-jobs`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      setIsCreatingCronJob(false);
      setCronJobCreateDraft(EMPTY_CRON_CREATE_DRAFT);
      await refreshCronJobs(selectedSoul.soul_id);
    }).catch((cause) => {
      notifyError(cause);
    });
  }

  async function refreshSessions(
    soulId: string,
    overrides?: { page?: number; perPage?: number; order?: "asc" | "desc" },
  ): Promise<SessionSummary[]> {
    const page = overrides?.page ?? sessionsPage;
    const perPage = overrides?.perPage ?? sessionsPerPage;
    const order = overrides?.order ?? sessionSortOrder;
    const params = new URLSearchParams({
      limit: String(perPage),
      offset: String(page * perPage),
      order,
    });
    const response = await api<SessionListResponse>(
      `/api/souls/${encodeURIComponent(soulId)}/sessions?${params.toString()}`,
    );
    setSessions(response.items);
    setSessionsTotal(response.total);
    return response.items;
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
        await refreshSkillRegistry();
        await refreshCronJobRegistry();
        await refreshMcpServers();
      } catch (cause) {
        notifyError(cause);
      }
    })();
  }, []);

  useEffect(() => {
    if (!isCreatingSoul) {
      return;
    }
    const previousOverflow = document.body.style.overflow;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !pending) {
        closeCreateSoulDialog();
      }
    }
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isCreatingSoul, pending]);

  useEffect(() => {
    if (!selectedSoul) {
      return;
    }
    const pendingSessionKey = initialFocusRef.current.soulId === selectedSoul.soul_id ? initialFocusRef.current.sessionKey : null;
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
    setSessionKey(pendingSessionKey);
    setChatContent("");
    setChatReasoning("");
    setFinalizedMessages([]);
    socketRef.current?.close();
    socketRef.current = null;
    setSocketState("closed");
    void refreshPromptFiles(selectedSoul.soul_id).catch((cause) => {
      notifyError(cause);
    });
    void refreshCronJobs(selectedSoul.soul_id).catch((cause) => {
      notifyError(cause);
    });
    // Restore the session focused via the URL once the soul is resolved.
    // This must happen here (not in the sessions-list effect below) because
    // loadSession needs `selectedSoul`, which is null on the first render —
    // at that point only `selectedSoulId` is known from the URL. The list
    // effect runs first and would consume/clear initialFocusRef too early.
    if (pendingSessionKey) {
      void loadSession(pendingSessionKey);
    }
    initialFocusRef.current = { soulId: "", sessionKey: null };
  }, [selectedSoul?.soul_id]);

  useEffect(() => {
    if (!selectedSoulId) {
      setSessions([]);
      setSessionsTotal(0);
      return;
    }
    setSessions([]);
    setSessionsTotal(0);
    let cancelled = false;
    void (async () => {
      try {
        await refreshSessions(selectedSoulId);
      } catch (cause) {
        if (!cancelled) notifyError(cause);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedSoulId, sessionsPage, sessionsPerPage, sessionSortOrder]);

  useEffect(() => {
    syncFocusToUrl(selectedSoulId, sessionKey);
  }, [selectedSoulId, sessionKey]);

  useEffect(() => {
    setSessionsPage(0);
  }, [selectedSoulId, sessionsPerPage, sessionSortOrder]);

  useEffect(() => {
    if (soulGroupFilter && !allSoulGroups.includes(soulGroupFilter)) {
      setSoulGroupFilter("");
    }
  }, [soulGroupFilter, allSoulGroups]);

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
    const url = new URL(`${getWsBase()}/api/ws/souls/${encodeURIComponent(soulId)}/chat`);
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
      setChatContent("");
      setChatReasoning("");
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

  function resetCreateSoulForm() {
    setCreateSoulId("");
    setCreateSoulDraft(getEmptyDraftOverrides());
    setCreateSoulCronJobNames([]);
    setCreateSoulSkillDrafts({});
  }

  function openCreateSoulDialog() {
    resetCreateSoulForm();
    setSoulError("");
    setIsCreatingSoul(true);
  }

  function closeCreateSoulDialog() {
    setIsCreatingSoul(false);
    resetCreateSoulForm();
  }

  function toggleCreateSoulSkill(skillPath: string, enabled: boolean) {
    setCreateSoulSkillDrafts((current) => {
      if (!enabled) {
        const next = { ...current };
        delete next[skillPath];
        return next;
      }
      return {
        ...current,
        [skillPath]: {
          enabled: true,
          mode: current[skillPath]?.mode ?? "symlink",
          target_name: current[skillPath]?.target_name ?? "",
        },
      };
    });
  }

  function updateCreateSoulSkillMode(skillPath: string, mode: "symlink" | "copy") {
    setCreateSoulSkillDrafts((current) => ({
      ...current,
      [skillPath]: {
        enabled: true,
        mode,
        target_name: current[skillPath]?.target_name ?? "",
      },
    }));
  }

  function updateCreateSoulSkillTarget(skillPath: string, targetName: string) {
    setCreateSoulSkillDrafts((current) => ({
      ...current,
      [skillPath]: {
        enabled: true,
        mode: current[skillPath]?.mode ?? "symlink",
        target_name: targetName,
      },
    }));
  }

  async function createSoul() {
    if (!createSoulId.trim()) {
      notifyError("soul_id is required");
      return;
    }
    let createdSoulId: string | null = null;
    try {
      await runAction("create", async () => {
        const selectedSkills = Object.entries(createSoulSkillDrafts).filter(([, skill]) => skill.enabled);
        const requestedOverrides = draftToOverrides(createSoulDraft);
        const shouldDelayAutostart = selectedSkills.length > 0 && requestedOverrides.autostart;
        const createOverrides = shouldDelayAutostart
          ? { ...requestedOverrides, autostart: false }
          : requestedOverrides;
        const created = await api<Soul>("/api/souls", {
          method: "POST",
          body: JSON.stringify({
            soul_id: createSoulId.trim(),
            overrides: createOverrides,
            cron_job_registry_names: createSoulCronJobNames,
          }),
        });
        createdSoulId = created.soul_id;
        for (const [skillPath, skill] of selectedSkills) {
          await api<SoulSkill[]>(`/api/souls/${encodeURIComponent(created.soul_id)}/skills`, {
            method: "POST",
            body: JSON.stringify({
              skill_path: skillPath,
              name: skill.target_name.trim() || null,
              mode: skill.mode,
            }),
          });
        }
        if (shouldDelayAutostart) {
          await api<Soul>(`/api/souls/${encodeURIComponent(created.soul_id)}`, {
            method: "PATCH",
            body: JSON.stringify({ overrides: requestedOverrides }),
          });
          await api<Soul>(`/api/souls/${encodeURIComponent(created.soul_id)}/start`, {
            method: "POST",
          });
        }
        resetCreateSoulForm();
        setIsCreatingSoul(false);
        await refreshSouls(created.soul_id);
        await refreshSessions(created.soul_id);
      });
    } catch (cause) {
      notifyError(cause);
      if (createdSoulId) {
        await refreshSouls(createdSoulId).catch((refreshCause) => {
          notifyError(refreshCause);
        });
      }
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

  async function fetchSessionWindow(
    key: string,
    options?: { before?: number; limit?: number },
  ): Promise<SessionDetail> {
    if (!selectedSoul) {
      throw new Error("No soul selected");
    }
    const params = new URLSearchParams();
    if (typeof options?.before === "number") {
      params.set("before", String(options.before));
    }
    if (typeof options?.limit === "number") {
      params.set("limit", String(options.limit));
    }
    const queryString = params.toString();
    const query = queryString ? `?${queryString}` : "";
    return api<SessionDetail>(
      `/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/sessions/${encodeURIComponent(key)}${query}`,
    );
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
        const detail = await fetchSessionWindow(key);
        setSessionDetail(detail);
        setSessionKey(key);
        setOlderMessagesPending(false);
        setChatContent("");
        setChatReasoning("");
        setFinalizedMessages([]);
        setSocketEpoch((current) => current + 1);
      });
    } catch (cause) {
      notifyError(cause);
    }
  }

  async function loadOlderMessages() {
    if (!sessionKey || !sessionDetail || !canLoadOlderMessages) {
      return;
    }
    setOlderMessagesPending(true);
    try {
      const detail = await fetchSessionWindow(sessionKey, {
        before: sessionDetail.history_start,
        limit: 20,
      });
      setSessionDetail((current) => prependSessionWindow(current, detail));
    } catch (cause) {
      notifyError(cause);
    } finally {
      setOlderMessagesPending(false);
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
      setSessionsPage(0);
      await refreshSessions(selectedSoul.soul_id, { page: 0 });
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

  async function saveSkillRegistry(items: string[]) {
    const normalized = items.map((item) => item.trim()).filter(Boolean);
    await runAction("skill-registry", async () => {
      const response = await api<SkillRegistryResponse>("/api/skill-registry", {
        method: "PATCH",
        body: JSON.stringify({ items: normalized }),
      });
      setSkillPools(response.pools);
      setIsEditingSkillRegistry(false);
      setNewSkillRegistryPath("");
      const stillExists = response.pools.some((pool) =>
        pool.skills.some((skill) => skill.skill_path === addSkillSelection),
      );
      if (addSkillSelection && !stillExists) {
        setAddSkillSelection("");
      }
      setCreateSoulSkillDrafts((current) => {
        const validSkillPaths = new Set(
          response.pools.flatMap((pool) => pool.skills.map((skill) => skill.skill_path)),
        );
        return Object.fromEntries(
          Object.entries(current).filter(([skillPath]) => validSkillPaths.has(skillPath)),
        );
      });
    }).catch((cause) => {
      notifyError(cause);
    });
  }

  async function addSkillRegistryEntry() {
    const item = newSkillRegistryPath.trim();
    if (!item) {
      notifyError("Skill pool path is required");
      return;
    }
    await saveSkillRegistry([...skillPools.map((entry) => entry.path), item]);
  }

  async function deleteSkillRegistryEntry(path: string) {
    await saveSkillRegistry(skillPools.map((entry) => entry.path).filter((item) => item !== path));
  }

  async function addSoulSkill() {
    if (!selectedSoul) return;
    const skill_path = addSkillSelection;
    if (!skill_path) {
      notifyError("Pick a skill from the pools");
      return;
    }
    const targetName = addSkillTargetName.trim();
    await runAction("soul-skill-add", async () => {
      const response = await api<SoulSkill[]>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/skills`, {
        method: "POST",
        body: JSON.stringify({
          skill_path,
          name: targetName || null,
          mode: addSkillMode,
        }),
      });
      setSouls((current) => current.map((soul) => (
        soul.soul_id === selectedSoul.soul_id ? { ...soul, skills: response } : soul
      )));
      setAddSkillSelection("");
      setAddSkillTargetName("");
      setAddSkillMode("symlink");
    }).catch((cause) => {
      notifyError(cause);
    });
  }

  async function deleteSoulSkill(name: string) {
    if (!selectedSoul) return;
    const skill = selectedSoul.skills.find((entry) => entry.name === name);
    const isLink = !!skill?.link_target;
    const message = isLink
      ? `Remove the soft link "${name}" from this soul? The pool source will be untouched.`
      : `Permanently delete the soul-specific skill "${name}"? Its workspace files will be removed.`;
    if (!window.confirm(message)) {
      return;
    }
    await runAction("soul-skill-delete", async () => {
      await api<void>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/skills/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      const response = await api<SoulSkill[]>(`/api/souls/${encodeURIComponent(selectedSoul.soul_id)}/skills`);
      setSouls((current) => current.map((soul) => (
        soul.soul_id === selectedSoul.soul_id ? { ...soul, skills: response } : soul
      )));
    }).catch((cause) => {
      notifyError(cause);
    });
  }

  const runningCount = souls.filter((soul) => soul.running).length;
  const chatHistory = [...(sessionDetail?.messages ?? []), ...finalizedMessages] as Array<Record<string, unknown>>;
  const hasStreamingTurn = !!chatReasoning || !!chatContent;
  const canLoadOlderMessages = !!sessionDetail && sessionDetail.history_start > 0;
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
            {allSoulGroups.length ? (
              <select
                className="soul-group-filter"
                value={soulGroupFilter}
                onChange={(event) => setSoulGroupFilter(event.target.value)}
                aria-label="Filter souls by group"
              >
                <option value="">All groups</option>
                {allSoulGroups.map((group) => (
                  <option key={group} value={group}>
                    {group}
                  </option>
                ))}
              </select>
            ) : null}
            <button
              className="ghost"
              onClick={() => {
                void (async () => {
                  try {
                    await refreshSouls(selectedSoulId, true);
                    await refreshSkillRegistry();
                    toast.success("Souls refreshed");
                  } catch (cause) {
                    notifyError(cause);
                  }
                })();
              }}
              disabled={!!pending}
            >
              Refresh
            </button>
          </div>
          <div className="soul-list">
            {visibleSouls.map((soul) => (
              <button
                key={soul.soul_id}
                className={`soul-card ${selectedSoulId === soul.soul_id ? "active" : ""}`}
                onClick={() => setSelectedSoulId(soul.soul_id)}
              >
                <div className="soul-card-head">
                  <strong>{soul.soul_id}</strong>
                  <div className="soul-card-head-right">
                    {soul.overrides.groups && soul.overrides.groups.length ? (
                      <div className="soul-card-groups">
                        {soul.overrides.groups.map((group) => (
                          <span key={group} className="group-chip">{group}</span>
                        ))}
                      </div>
                    ) : null}
                    <span className={`pill ${soul.running ? "live" : "idle"}`}>{soul.running ? "running" : "stopped"}</span>
                  </div>
                </div>
                <code>{soul.workspace}</code>
              </button>
            ))}
            {!souls.length ? (
              <p className="muted">No souls configured yet.</p>
            ) : !visibleSouls.length ? (
              <p className="muted">No souls in group "{soulGroupFilter}".</p>
            ) : null}
          </div>

          <div className="create-box">
            <h3>Create soul</h3>
            <button
              onClick={() => {
                openCreateSoulDialog();
              }}
              disabled={!!pending}
            >
              New soul
            </button>
          </div>

        </section>

        <section className="panel sessions-panel">
          <div className="panel-head">
            <h2>Sessions</h2>
            {selectedSoul ? (
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
          <>
              {sessionsTotal > 0 ? (
                <div className="session-toolbar">
                  <label className="session-toolbar-field">
                    <span>Sort</span>
                    <select
                      value={sessionSortOrder}
                      onChange={(event) => setSessionSortOrder(event.target.value === "asc" ? "asc" : "desc")}
                    >
                      <option value="desc">Newest first</option>
                      <option value="asc">Oldest first</option>
                    </select>
                  </label>
                  <label className="session-toolbar-field">
                    <span>Per page</span>
                    <select
                      value={sessionsPerPage}
                      onChange={(event) => setSessionsPerPage(Number(event.target.value))}
                    >
                      {[5, 10, 20, 50].map((size) => (
                        <option key={size} value={size}>{size}</option>
                      ))}
                    </select>
                  </label>
                </div>
              ) : null}
              <div className="session-list">
                {sessions.map((session) => (
                  <button key={session.key} className="session-card" onClick={() => void loadSession(session.key)}>
                    <strong>{session.key}</strong>
                    <span>updated {formatDate(session.updated_at)}</span>
                    <code>{session.path}</code>
                  </button>
                ))}
                {sessionsTotal === 0 ? <p className="muted">No sessions found for this soul.</p> : null}
              </div>
              {sessionsTotal > sessionsPerPage ? (
                <div className="session-pager">
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => setSessionsPage((current) => Math.max(0, current - 1))}
                    disabled={clampedSessionsPage === 0}
                  >
                    ‹ Prev
                  </button>
                  <span className="muted">
                    Page {clampedSessionsPage + 1} of {sessionsTotalPages} · {sessionsTotal} total
                  </span>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => setSessionsPage((current) => Math.min(sessionsTotalPages - 1, current + 1))}
                    disabled={clampedSessionsPage >= sessionsTotalPages - 1}
                  >
                    Next ›
                  </button>
                </div>
              ) : null}

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
        </section>

        <section className="panel tools-panel">
          <details className="tools-master">
            <summary className="panel-head tools-summary tools-master-summary">
              <h2>Registries & MCP servers</h2>
              <span className="muted tools-fold-hint">click to expand</span>
            </summary>
            <div className="tools-stack">
              <details className="tools-subpanel">
                <summary className="panel-head tools-summary">
                  <h3>Skill pools</h3>
                  <div style={{ display: "flex", gap: "0.5rem" }}>
                    <button
                      type="button"
                      className="ghost"
                      onClick={(event) => {
                        event.preventDefault();
                        void reloadSkillPools();
                      }}
                      disabled={!!pending}
                    >
                      Refresh
                    </button>
                    <button
                      type="button"
                      className="ghost"
                      onClick={(event) => {
                        event.preventDefault();
                        setIsEditingSkillRegistry((current) => !current);
                      }}
                      disabled={!!pending}
                    >
                      {isEditingSkillRegistry ? "Done" : "Edit"}
                    </button>
                  </div>
                </summary>
                <div className="tools-subpanel-body">
                  {isEditingSkillRegistry ? (
                    <div className="app-links-editor">
                      <div className="app-links-editor-row">
                        <input
                          value={newSkillRegistryPath}
                          onChange={(event) => setNewSkillRegistryPath(event.target.value)}
                          placeholder="~/skills"
                          disabled={!!pending}
                        />
                        <button type="button" onClick={() => void addSkillRegistryEntry()} disabled={!!pending}>
                          Add pool
                        </button>
                      </div>
                    </div>
                  ) : null}
                  <div className="registry-card-list">
                    {skillPools.length ? skillPools.map((pool) => (
                      <article key={pool.path} className="registry-card">
                        <div className="registry-card-head">
                          <code>{pool.path}</code>
                          {isEditingSkillRegistry ? (
                            <button
                              type="button"
                              className="ghost"
                              onClick={() => void deleteSkillRegistryEntry(pool.path)}
                              disabled={!!pending}
                            >
                              Remove
                            </button>
                          ) : null}
                        </div>
                        <div className="registry-pills">
                          <span className={`pill ${pool.exists ? "live" : "idle"}`}>
                            {pool.exists ? "pool present" : "missing"}
                          </span>
                          <span className="pill idle">
                            {pool.skills.length} skill{pool.skills.length === 1 ? "" : "s"}
                          </span>
                        </div>
                        {pool.skills.length ? (
                          <div className="skill-list" style={{ marginTop: "0.5rem" }}>
                            {pool.skills.map((skill) => (
                              <details key={skill.skill_path} className="skill-entry-details">
                                <summary className="skill-entry-summary">
                                  <div>
                                    <strong>{skill.name}</strong>
                                    <code style={{ marginLeft: "0.5rem" }}>{skill.relative_path}</code>
                                    {skill.token_count !== null && skill.token_count !== undefined ? (
                                      <span className="pill idle" style={{ marginLeft: "0.5rem" }}>
                                        {skill.token_count.toLocaleString()} tokens
                                      </span>
                                    ) : null}
                                  </div>
                                </summary>
                                {skill.description ? (
                                  <p className="muted skill-registry-desc">{skill.description}</p>
                                ) : null}
                                <p className="muted skill-entry-target">
                                  <code>{skill.skill_path}</code>
                                </p>
                              </details>
                            ))}
                          </div>
                        ) : pool.exists ? (
                          <p className="muted skill-registry-desc">No skills with valid SKILL.md frontmatter.</p>
                        ) : null}
                      </article>
                    )) : <p className="muted">No skill pools configured.</p>}
                  </div>
                </div>
              </details>

              <details className="tools-subpanel">
                <summary className="panel-head tools-summary">
                  <h3>Cron job registry</h3>
                  <button
                    type="button"
                    className="ghost"
                    onClick={(event) => {
                      event.preventDefault();
                      setIsEditingCronJobRegistry((current) => !current);
                    }}
                    disabled={!!pending}
                  >
                    {isEditingCronJobRegistry ? "Done" : "Edit"}
                  </button>
                </summary>
                <div className="tools-subpanel-body">
                  {isEditingCronJobRegistry ? (
                    <div className="app-links-editor">
                      <div className="details-stack" style={{ gap: "0.5rem" }}>
                        <label>
                          <span>Name (unique ID)</span>
                          <input
                            value={cronJobRegistryDraft.name}
                            onChange={(e) => setCronJobRegistryDraft((d) => ({ ...d, name: e.target.value }))}
                            placeholder="daily-digest"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Label (display name)</span>
                          <input
                            value={cronJobRegistryDraft.label}
                            onChange={(e) => setCronJobRegistryDraft((d) => ({ ...d, label: e.target.value }))}
                            placeholder="Daily digest"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Cron expr</span>
                          <input
                            value={cronJobRegistryDraft.cron_expr}
                            onChange={(e) => setCronJobRegistryDraft((d) => ({ ...d, cron_expr: e.target.value }))}
                            placeholder="0 9 * * 1-5"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Every (seconds)</span>
                          <input
                            type="number"
                            value={cronJobRegistryDraft.every_seconds}
                            onChange={(e) => setCronJobRegistryDraft((d) => ({ ...d, every_seconds: e.target.value }))}
                            placeholder="3600"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Timezone</span>
                          <input
                            value={cronJobRegistryDraft.tz}
                            onChange={(e) => setCronJobRegistryDraft((d) => ({ ...d, tz: e.target.value }))}
                            placeholder="e.g. America/New_York"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Message</span>
                          <input
                            value={cronJobRegistryDraft.message}
                            onChange={(e) => setCronJobRegistryDraft((d) => ({ ...d, message: e.target.value }))}
                            placeholder="Run the daily summary"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Channel</span>
                          <input
                            value={cronJobRegistryDraft.channel}
                            onChange={(e) => setCronJobRegistryDraft((d) => ({ ...d, channel: e.target.value }))}
                            placeholder="whatsapp"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Chat ID</span>
                          <input
                            value={cronJobRegistryDraft.chat_id}
                            onChange={(e) => setCronJobRegistryDraft((d) => ({ ...d, chat_id: e.target.value }))}
                            placeholder="(optional, channel-local id)"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Session key</span>
                          <input
                            value={cronJobRegistryDraft.session_key}
                            onChange={(e) => setCronJobRegistryDraft((d) => ({ ...d, session_key: e.target.value }))}
                            placeholder="(optional, e.g. cli:direct)"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Recurring session key format</span>
                          <input
                            value={cronJobRegistryDraft.recurring_session_key_format}
                            onChange={(e) => setCronJobRegistryDraft((d) => ({ ...d, recurring_session_key_format: e.target.value }))}
                            placeholder="%Y-%m-%d"
                            disabled={!!pending}
                          />
                        </label>
                        <label className="checkbox">
                          <input
                            type="checkbox"
                            checked={cronJobRegistryDraft.deliver}
                            onChange={(e) => setCronJobRegistryDraft((d) => ({ ...d, deliver: e.target.checked }))}
                            disabled={!!pending}
                          />
                          <span>Deliver response</span>
                        </label>
                        <button type="button" onClick={() => void addCronJobRegistryEntry()} disabled={!!pending}>
                          Add entry
                        </button>
                      </div>
                    </div>
                  ) : null}
                  <div className="registry-card-list">
                    {cronJobRegistry.length ? cronJobRegistry.map((entry) => (
                      <article key={entry.name} className="registry-card">
                        <div className="registry-card-head">
                          <code>{entry.name}</code>
                          {isEditingCronJobRegistry ? (
                            <div style={{ display: "flex", gap: "0.4rem" }}>
                              <button
                                type="button"
                                className="ghost"
                                onClick={() => void moveCronJobRegistryEntry(entry.name, -1)}
                                disabled={!!pending || cronJobRegistry.indexOf(entry) === 0}
                                aria-label="Move up"
                              >
                                ↑
                              </button>
                              <button
                                type="button"
                                className="ghost"
                                onClick={() => void moveCronJobRegistryEntry(entry.name, 1)}
                                disabled={!!pending || cronJobRegistry.indexOf(entry) === cronJobRegistry.length - 1}
                                aria-label="Move down"
                              >
                                ↓
                              </button>
                              <button
                                type="button"
                                className="ghost"
                                onClick={() => void deleteCronJobRegistryEntry(entry.name)}
                                disabled={!!pending}
                              >
                                Remove
                              </button>
                            </div>
                          ) : null}
                        </div>
                        <div className="registry-pills">
                          {entry.label ? <span className="pill live">{entry.label}</span> : null}
                          {entry.cron_expr ? <span className="pill idle">{entry.cron_expr}{entry.tz ? ` (${entry.tz})` : ""}</span> : null}
                          {entry.every_seconds ? <span className="pill idle">every {entry.every_seconds}s</span> : null}
                          {entry.recurring_session_key_format ? <span className="pill live">session: {entry.recurring_session_key_format}</span> : null}
                          {entry.channel ? <span className="pill idle">{entry.channel}</span> : null}
                        </div>
                        {entry.message ? <p className="muted skill-registry-desc">{entry.message}</p> : null}
                      </article>
                    )) : <p className="muted">No cron job templates registered.</p>}
                  </div>
                </div>
              </details>

              <details className="tools-subpanel">
                <summary className="panel-head tools-summary">
                  <h3>MCP servers</h3>
                  <button
                    className="ghost"
                    onClick={(event) => {
                      event.preventDefault();
                      void refreshMcpServers(selectedMcpServerName).catch((cause) => {
                        notifyError(cause);
                      });
                    }}
                    disabled={!!pending}
                  >
                    Reload
                  </button>
                </summary>
                <div className="tools-subpanel-body">
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
                </div>
              </details>
            </div>
          </details>
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
                      <label htmlFor="edit-soul-groups-input">
                        <span>Groups (display only)</span>
                        <GroupListEditor
                          inputId="edit-soul-groups-input"
                          value={draft.groups}
                          onChange={(next) => setDraft((current) => ({ ...current, groups: next }))}
                          suggestions={allSoulGroups}
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
                                  setDraft((current) => updateSoulMcpSelection(current, server.name, event.target.checked));
                                }}
                              />
                              <span>{server.name}</span>
                            </label>
                          ))}
                          {!mcpServers.length ? <p className="muted">No MCP server definitions available.</p> : null}
                        </div>
                      </label>
                      {draft.mcp_servers.length ? (
                        <div className="field-grid">
                          {draft.mcp_servers.map((serverName) => (
                            <label key={serverName}>
                              <span>MCP headers: {serverName}</span>
                              <textarea
                                value={draft.mcp_http_headers[serverName] ?? "{}"}
                                onChange={(event) => {
                                  const value = event.target.value;
                                  setDraft((current) => ({
                                    ...current,
                                    mcp_http_headers: {
                                      ...current.mcp_http_headers,
                                      [serverName]: value,
                                    },
                                  }));
                                }}
                                rows={6}
                                spellCheck={false}
                              />
                            </label>
                          ))}
                        </div>
                      ) : null}
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
                      <article className="override-card override-card-wide">
                        <span>Skills</span>
                        {selectedSoul.skills.length ? (
                          <div className="skill-list">
                            {selectedSoul.skills.map((skill) => (
                              <details key={skill.path} className="skill-entry-details">
                                <summary className="skill-entry-summary">
                                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                                    <strong>{skill.name}</strong>
                                    <span className={`pill ${skill.link_target ? "live" : "idle"}`}>
                                      {skill.link_target ? "soft link" : "copy"}
                                    </span>
                                    {skill.token_count !== null && skill.token_count !== undefined ? (
                                      <span className="pill idle">{skill.token_count.toLocaleString()} tokens</span>
                                    ) : null}
                                    <button
                                      type="button"
                                      className="ghost skill-delete-button"
                                      onClick={(event) => {
                                        event.preventDefault();
                                        void deleteSoulSkill(skill.name);
                                      }}
                                      disabled={!!pending}
                                    >
                                      Delete
                                    </button>
                                  </div>
                                  <button
                                    type="button"
                                    className="ghost skill-path-button"
                                    title={`Copy ${skill.path}`}
                                    onClick={(event) => {
                                      event.preventDefault();
                                      void copyToClipboard(skill.path).then(() => {
                                        toast.success(`Copied ${skill.name} path`);
                                      });
                                    }}
                                  >
                                    <code>{skill.path}</code>
                                  </button>
                                </summary>
                                {skill.description ? (
                                  <p className="muted skill-entry-desc">{skill.description}</p>
                                ) : null}
                                {skill.link_target ? (
                                  <p className="muted skill-entry-target">
                                    soft link → <code>{skill.link_target}</code>
                                  </p>
                                ) : null}
                                <div className="skill-content">
                                  <MarkdownMessage content={skill.content} />
                                </div>
                              </details>
                            ))}
                          </div>
                        ) : (
                          <strong>none</strong>
                        )}
                        <div className="skill-add-form">
                          <select
                            value={addSkillSelection}
                            onChange={(event) => setAddSkillSelection(event.target.value)}
                            disabled={!skillPools.some((pool) => pool.skills.length) || !!pending}
                          >
                            <option value="">
                              {skillPools.some((pool) => pool.skills.length)
                                ? "Add from pools…"
                                : "No skills loaded"}
                            </option>
                            {skillPools.map((pool) => (
                              pool.skills.length ? (
                                <optgroup key={pool.path} label={pool.path}>
                                  {pool.skills.map((skill) => (
                                    <option key={skill.skill_path} value={skill.skill_path}>
                                      {skill.name} ({skill.relative_path})
                                    </option>
                                  ))}
                                </optgroup>
                              ) : null
                            ))}
                          </select>
                          {addSkillSelection ? (
                            <input
                              value={addSkillTargetName}
                              onChange={(event) => setAddSkillTargetName(event.target.value)}
                              placeholder="rename (optional)"
                              disabled={!!pending}
                            />
                          ) : null}
                          {addSkillSelection ? (<fieldset className="registry-mode" disabled={!!pending}>
                            <legend>Mode</legend>
                            <label className="registry-mode-option">
                              <input
                                type="radio"
                                name="add-skill-mode"
                                value="symlink"
                                checked={addSkillMode === "symlink"}
                                onChange={() => setAddSkillMode("symlink")}
                              />
                              <span><strong>Soft link</strong> &mdash; track the pool source live</span>
                            </label>
                            <label className="registry-mode-option">
                              <input
                                type="radio"
                                name="add-skill-mode"
                                value="copy"
                                checked={addSkillMode === "copy"}
                                onChange={() => setAddSkillMode("copy")}
                              />
                              <span><strong>Copy</strong> &mdash; soul-specific writable copy</span>
                            </label>
                          </fieldset>) : null}
                          <button
                            type="button"
                            onClick={() => void addSoulSkill()}
                            disabled={!addSkillSelection || !!pending}
                          >
                            Add skill
                          </button>
                        </div>
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
                        <span>Groups</span>
                        <strong>{renderEnabledList(selectedSoul.overrides.groups ?? [])}</strong>
                      </article>
                      <article className="override-card">
                        <span>Enabled MCP servers</span>
                        <strong>{renderEnabledList(selectedSoul.overrides.mcp_servers)}</strong>
                      </article>
                      <article className="override-card">
                        <span>MCP header overrides</span>
                        <strong>{renderHeaderOverrideSummary(selectedSoul.overrides.mcp_http_headers ?? {})}</strong>
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
                        <article key={job.id} className="session-card cron-job-card">
                          {editingCronJobId === job.id ? (
                            <div className="details-stack" style={{ gap: "0.5rem", marginTop: 0 }}>
                              <label>
                                <span>Name</span>
                                <input
                                  value={cronJobEditDraft.name}
                                  onChange={(e) => setCronJobEditDraft((d) => ({ ...d, name: e.target.value }))}
                                  disabled={!!pending}
                                />
                              </label>
                              <label>
                                <span>Schedule type</span>
                                <select
                                  value={cronJobEditDraft.schedule_kind}
                                  onChange={(e) => setCronJobEditDraft((d) => ({ ...d, schedule_kind: e.target.value as "every" | "cron" }))}
                                  disabled={!!pending}
                                >
                                  <option value="cron">cron expression</option>
                                  <option value="every">every N seconds</option>
                                </select>
                              </label>
                              {cronJobEditDraft.schedule_kind === "cron" ? (
                                <>
                                  <label>
                                    <span>Cron expression</span>
                                    <input
                                      value={cronJobEditDraft.cron_expr}
                                      onChange={(e) => setCronJobEditDraft((d) => ({ ...d, cron_expr: e.target.value }))}
                                      placeholder="0 9 * * *"
                                      disabled={!!pending}
                                    />
                                  </label>
                                  <label>
                                    <span>Timezone</span>
                                    <input
                                      value={cronJobEditDraft.tz}
                                      onChange={(e) => setCronJobEditDraft((d) => ({ ...d, tz: e.target.value }))}
                                      placeholder="UTC"
                                      disabled={!!pending}
                                    />
                                  </label>
                                </>
                              ) : (
                                <label>
                                  <span>Every (seconds)</span>
                                  <input
                                    type="number"
                                    value={cronJobEditDraft.every_seconds}
                                    onChange={(e) => setCronJobEditDraft((d) => ({ ...d, every_seconds: e.target.value }))}
                                    placeholder="3600"
                                    disabled={!!pending}
                                  />
                                </label>
                              )}
                              <label>
                                <span>Message</span>
                                <input
                                  value={cronJobEditDraft.message}
                                  onChange={(e) => setCronJobEditDraft((d) => ({ ...d, message: e.target.value }))}
                                  disabled={!!pending}
                                />
                              </label>
                              <label>
                                <span>Channel</span>
                                <input
                                  value={cronJobEditDraft.channel}
                                  onChange={(e) => setCronJobEditDraft((d) => ({ ...d, channel: e.target.value }))}
                                  placeholder="(optional)"
                                  disabled={!!pending}
                                />
                              </label>
                              <label>
                                <span>Chat ID</span>
                                <input
                                  value={cronJobEditDraft.chat_id}
                                  onChange={(e) => setCronJobEditDraft((d) => ({ ...d, chat_id: e.target.value }))}
                                  placeholder="(optional, channel-local id)"
                                  disabled={!!pending}
                                />
                              </label>
                              <label>
                                <span>Session key</span>
                                <input
                                  value={cronJobEditDraft.session_key}
                                  onChange={(e) => setCronJobEditDraft((d) => ({ ...d, session_key: e.target.value }))}
                                  placeholder="(optional, e.g. cli:direct)"
                                  disabled={!!pending}
                                />
                              </label>
                              <div style={{ display: "flex", gap: "1.2rem", flexWrap: "wrap" }}>
                                <label style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
                                  <input
                                    type="checkbox"
                                    checked={cronJobEditDraft.enabled}
                                    onChange={(e) => setCronJobEditDraft((d) => ({ ...d, enabled: e.target.checked }))}
                                    disabled={!!pending}
                                  />
                                  Enabled
                                </label>
                                <label style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
                                  <input
                                    type="checkbox"
                                    checked={cronJobEditDraft.deliver}
                                    onChange={(e) => setCronJobEditDraft((d) => ({ ...d, deliver: e.target.checked }))}
                                    disabled={!!pending}
                                  />
                                  Deliver
                                </label>
                                <label style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
                                  <input
                                    type="checkbox"
                                    checked={cronJobEditDraft.delete_after_run}
                                    onChange={(e) => setCronJobEditDraft((d) => ({ ...d, delete_after_run: e.target.checked }))}
                                    disabled={!!pending}
                                  />
                                  Delete after run
                                </label>
                              </div>
                              <div className="app-links-editor-row">
                                <button
                                  type="button"
                                  onClick={() => void updateSoulCronJob(job.id)}
                                  disabled={!!pending}
                                >
                                  Save
                                </button>
                                <button
                                  type="button"
                                  className="ghost"
                                  onClick={() => setEditingCronJobId(null)}
                                  disabled={!!pending}
                                >
                                  Cancel
                                </button>
                              </div>
                            </div>
                          ) : (
                            <>
                              <div className="cron-job-meta-row">
                                <strong>{job.name}</strong>
                                <span>{formatCronSchedule(job.schedule)}</span>
                              </div>
                              <div className="cron-job-meta-row">
                                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                                  <code>{job.session_key || "no session"}</code>
                                  {job.recurring_session_key_format ? (
                                    <span className="pill live">session fmt: {job.recurring_session_key_format}</span>
                                  ) : null}
                                </div>
                                <div style={{ display: "flex", gap: "0.5rem" }}>
                                  <button
                                    type="button"
                                    className="ghost"
                                    onClick={() => startEditCronJob(job)}
                                    disabled={!!pending}
                                  >
                                    Edit
                                  </button>
                                  <button
                                    type="button"
                                    className="ghost"
                                    onClick={() => void deleteSoulCronJob(job.id)}
                                    disabled={!!pending}
                                  >
                                    Remove
                                  </button>
                                </div>
                              </div>
                              <div className="cron-job-meta-row">
                                <span>
                                  {job.enabled ? "enabled" : "disabled"}
                                  {job.state.last_status ? ` · last ${job.state.last_status}` : ""}
                                </span>
                                <span>next {formatTimestampMs(job.state.next_run_at_ms)}</span>
                              </div>
                              <p>{job.message}</p>
                            </>
                          )}
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
                  {cronJobRegistry.length > 0 ? (
                    <div className="create-box" style={{ marginTop: "0.75rem" }}>
                      <h4>Add from registry</h4>
                      <div className="app-links-editor-row">
                        <select
                          value={addCronJobRegistrySelection}
                          onChange={(e) => setAddCronJobRegistrySelection(e.target.value)}
                          disabled={!!pending}
                        >
                          <option value="">Add from registry…</option>
                          {cronJobRegistry.map((entry) => (
                            <option key={entry.name} value={entry.name}>
                              {entry.label ?? entry.name}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={() => void addSoulCronJobsFromRegistry()}
                          disabled={!!pending || !addCronJobRegistrySelection}
                        >
                          Add
                        </button>
                      </div>
                    </div>
                  ) : null}

                  <div className="create-box" style={{ marginTop: "0.75rem" }}>
                    <div className="panel-head">
                      <h4 style={{ margin: 0 }}>New cron job</h4>
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => {
                          setIsCreatingCronJob((current) => {
                            const next = !current;
                            if (next) {
                              setCronJobCreateDraft({
                                ...EMPTY_CRON_CREATE_DRAFT,
                                tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
                              });
                            }
                            return next;
                          });
                        }}
                        disabled={!!pending || !selectedSoul}
                      >
                        {isCreatingCronJob ? "Cancel" : "New"}
                      </button>
                    </div>
                    {isCreatingCronJob ? (
                      <div className="details-stack" style={{ gap: "0.5rem" }}>
                        <label>
                          <span>Name</span>
                          <input
                            value={cronJobCreateDraft.name}
                            onChange={(e) => setCronJobCreateDraft((d) => ({ ...d, name: e.target.value }))}
                            placeholder="hourly-check"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Schedule type</span>
                          <select
                            value={cronJobCreateDraft.schedule_kind}
                            onChange={(e) =>
                              setCronJobCreateDraft((d) => ({
                                ...d,
                                schedule_kind: e.target.value as "every" | "cron",
                              }))
                            }
                            disabled={!!pending}
                          >
                            <option value="cron">cron expression</option>
                            <option value="every">every N seconds</option>
                          </select>
                        </label>
                        {cronJobCreateDraft.schedule_kind === "cron" ? (
                          <>
                            <label>
                              <span>Cron expression</span>
                              <input
                                value={cronJobCreateDraft.cron_expr}
                                onChange={(e) => setCronJobCreateDraft((d) => ({ ...d, cron_expr: e.target.value }))}
                                placeholder="0 9 * * *"
                                disabled={!!pending}
                              />
                            </label>
                            <label>
                              <span>Timezone</span>
                              <input
                                value={cronJobCreateDraft.tz}
                                onChange={(e) => setCronJobCreateDraft((d) => ({ ...d, tz: e.target.value }))}
                                placeholder="UTC"
                                disabled={!!pending}
                              />
                            </label>
                          </>
                        ) : (
                          <label>
                            <span>Every (seconds)</span>
                            <input
                              type="number"
                              value={cronJobCreateDraft.every_seconds}
                              onChange={(e) => setCronJobCreateDraft((d) => ({ ...d, every_seconds: e.target.value }))}
                              placeholder="3600"
                              disabled={!!pending}
                            />
                          </label>
                        )}
                        <label>
                          <span>Message</span>
                          <input
                            value={cronJobCreateDraft.message}
                            onChange={(e) => setCronJobCreateDraft((d) => ({ ...d, message: e.target.value }))}
                            placeholder="Run the hourly summary"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Channel</span>
                          <input
                            value={cronJobCreateDraft.channel}
                            onChange={(e) => setCronJobCreateDraft((d) => ({ ...d, channel: e.target.value }))}
                            placeholder="(optional)"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Chat ID</span>
                          <input
                            value={cronJobCreateDraft.chat_id}
                            onChange={(e) => setCronJobCreateDraft((d) => ({ ...d, chat_id: e.target.value }))}
                            placeholder="(optional, channel-local id)"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Session key</span>
                          <input
                            value={cronJobCreateDraft.session_key}
                            onChange={(e) => setCronJobCreateDraft((d) => ({ ...d, session_key: e.target.value }))}
                            placeholder="(optional, e.g. cli:direct)"
                            disabled={!!pending}
                          />
                        </label>
                        <label>
                          <span>Recurring session key format</span>
                          <input
                            value={cronJobCreateDraft.recurring_session_key_format}
                            onChange={(e) =>
                              setCronJobCreateDraft((d) => ({
                                ...d,
                                recurring_session_key_format: e.target.value,
                              }))
                            }
                            placeholder="%Y-%m-%d"
                            disabled={!!pending}
                          />
                        </label>
                        <div style={{ display: "flex", gap: "1.2rem", flexWrap: "wrap" }}>
                          <label style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
                            <input
                              type="checkbox"
                              checked={cronJobCreateDraft.deliver}
                              onChange={(e) => setCronJobCreateDraft((d) => ({ ...d, deliver: e.target.checked }))}
                              disabled={!!pending}
                            />
                            Deliver
                          </label>
                          <label style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
                            <input
                              type="checkbox"
                              checked={cronJobCreateDraft.delete_after_run}
                              onChange={(e) => setCronJobCreateDraft((d) => ({ ...d, delete_after_run: e.target.checked }))}
                              disabled={!!pending}
                            />
                            Delete after run
                          </label>
                        </div>
                        <button
                          type="button"
                          onClick={() => void createSoulCronJob()}
                          disabled={!!pending}
                        >
                          Schedule
                        </button>
                      </div>
                    ) : null}
                  </div>
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
                              <MarkdownMessage content={content} />
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

                </div>
            </>
          ) : (
            <p className="muted">Select a soul to inspect or create one from the Souls panel.</p>
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
              {sessionDetail ? (
                <span className="muted">
                  last consolidated {sessionDetail.last_consolidated} · showing {sessionDetail.history_start}-{sessionDetail.history_end} of{" "}
                  {sessionDetail.total_messages}
                </span>
              ) : null}
            </div>
            <div className="message-list">
              {canLoadOlderMessages ? (
                <div className="message-history-actions">
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => {
                      void loadOlderMessages();
                    }}
                    disabled={olderMessagesPending}
                  >
                    {olderMessagesPending ? "Loading..." : "Load 20 older messages"}
                  </button>
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
      {isCreatingSoul ? (
        <CreateSoulDialog
          pending={pending}
          soulId={createSoulId}
          draft={createSoulDraft}
          allSoulGroups={allSoulGroups}
          mcpServers={mcpServers}
          cronJobRegistry={cronJobRegistry}
          cronJobNames={createSoulCronJobNames}
          skillPools={skillPools}
          skillDrafts={createSoulSkillDrafts}
          setSoulId={setCreateSoulId}
          setDraft={setCreateSoulDraft}
          setCronJobNames={setCreateSoulCronJobNames}
          onCancel={closeCreateSoulDialog}
          onToggleSkill={toggleCreateSoulSkill}
          onUpdateSkillMode={updateCreateSoulSkillMode}
          onUpdateSkillTarget={updateCreateSoulSkillTarget}
          onCreate={() => void createSoul()}
        />
      ) : null}
    </div>
  );
}
