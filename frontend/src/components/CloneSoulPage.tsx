import { useEffect, useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { api } from "../lib/api";
import {
  draftToOverrides,
  getEmptyPromptDraft,
  getEmptyPromptSelection,
  getToolChoices,
  getToolPolicyState,
  overridesToDraft,
  promptFilesToDraft,
  updateDraftToolPolicy,
  updateSoulMcpSelection,
} from "../lib/drafts";
import { notifyError } from "../lib/errors";
import { SOUL_PROMPT_FILE_NAMES } from "../types";
import type {
  CronJob,
  DraftOverrides,
  MCPServer,
  NanobotTool,
  Soul,
  SoulPromptDraft,
  SoulPromptFile,
  SoulPromptFileName,
  SoulPromptFilesResponse,
  ToolPolicyState,
} from "../types";
import { GroupListEditor } from "./GroupListEditor";

type CloneSoulPageProps = {
  source: Soul;
  enabledChannels: string[];
  allSoulGroups: string[];
  mcpServers: MCPServer[];
  nanobotTools: NanobotTool[];
  globalDisabledTools: string[];
  onCancel: () => void;
  onCloned: (soul: Soul) => Promise<void>;
};

type CloneCronJobDraft = {
  selected: boolean;
  name: string;
  enabled: boolean;
  message: string;
  origin_channel: string;
  origin_chat_id: string;
  origin_metadata: Record<string, unknown>;
  session_key: string;
  recurring_session_key_format: string;
  delete_after_run: boolean;
  schedule_kind: "every" | "cron";
  every_seconds: string;
  cron_expr: string;
  tz: string;
};

function cronJobToDraft(job: CronJob): CloneCronJobDraft {
  return {
    selected: true,
    name: job.name,
    enabled: job.enabled,
    message: job.message,
    origin_channel: job.origin_channel ?? "",
    origin_chat_id: job.origin_chat_id ?? "",
    origin_metadata: job.origin_metadata,
    session_key: job.session_key ?? "",
    recurring_session_key_format: job.recurring_session_key_format ?? "",
    delete_after_run: job.delete_after_run,
    schedule_kind: job.schedule.kind === "every" ? "every" : "cron",
    every_seconds: job.schedule.every_ms ? String(job.schedule.every_ms / 1000) : "",
    cron_expr: job.schedule.expr ?? "",
    tz: job.schedule.tz ?? "",
  };
}

export function CloneSoulPage({
  source,
  enabledChannels,
  allSoulGroups,
  mcpServers,
  nanobotTools,
  globalDisabledTools,
  onCancel,
  onCloned,
}: CloneSoulPageProps) {
  const [soulId, setSoulId] = useState(`${source.soul_id}-copy`);
  const [draft, setDraft] = useState<DraftOverrides>(() => overridesToDraft(source.overrides));
  const [promptFiles, setPromptFiles] = useState<SoulPromptFile[]>([]);
  const [promptDraft, setPromptDraft] = useState<SoulPromptDraft>(getEmptyPromptDraft());
  const [promptSelection, setPromptSelection] = useState<Record<SoulPromptFileName, boolean>>(getEmptyPromptSelection());
  const [promptPending, setPromptPending] = useState(true);
  const [promptError, setPromptError] = useState("");
  const [selectedSkillNames, setSelectedSkillNames] = useState<string[]>(() => source.skills.map((skill) => skill.name));
  const [cronJobs, setCronJobs] = useState<CronJob[]>([]);
  const [cronDrafts, setCronDrafts] = useState<Record<string, CloneCronJobDraft>>({});
  const [cronPending, setCronPending] = useState(true);
  const [cronError, setCronError] = useState("");
  const [startNow, setStartNow] = useState(false);
  const [pending, setPending] = useState(false);

  const selectedChannels = draft.channels
    .split(",")
    .map((channel) => channel.trim())
    .filter(Boolean);
  const toolChoices = useMemo(
    () => getToolChoices(
      nanobotTools,
      globalDisabledTools,
      draft.enabled_tools,
      draft.disabled_tools,
    ),
    [nanobotTools, globalDisabledTools, draft.enabled_tools, draft.disabled_tools],
  );

  async function loadPromptFiles(): Promise<void> {
    setPromptPending(true);
    setPromptError("");
    try {
      const response = await api<SoulPromptFilesResponse>(
        `/api/souls/${encodeURIComponent(source.soul_id)}/prompt-files`,
      );
      const selection = getEmptyPromptSelection();
      for (const file of response.files) {
        if (file.exists && SOUL_PROMPT_FILE_NAMES.some((name) => name === file.name)) {
          selection[file.name as SoulPromptFileName] = true;
        }
      }
      setPromptFiles(response.files);
      setPromptDraft(promptFilesToDraft(response.files));
      setPromptSelection(selection);
    } catch (cause) {
      setPromptError("Could not load prompt files from the source soul.");
      notifyError(cause);
    } finally {
      setPromptPending(false);
    }
  }

  async function loadCronJobs(): Promise<void> {
    setCronPending(true);
    setCronError("");
    try {
      const response = await api<CronJob[]>(
        `/api/souls/${encodeURIComponent(source.soul_id)}/cron-jobs`,
      );
      setCronJobs(response);
      setCronDrafts(Object.fromEntries(
        response.map((job) => [job.id, cronJobToDraft(job)]),
      ));
    } catch (cause) {
      setCronError("Could not load cron jobs from the source soul.");
      notifyError(cause);
    } finally {
      setCronPending(false);
    }
  }

  useEffect(() => {
    void loadPromptFiles();
    void loadCronJobs();
  }, [source.soul_id]);

  function togglePromptFile(name: SoulPromptFileName): void {
    setPromptSelection((current) => ({ ...current, [name]: !current[name] }));
  }

  function toggleSkill(name: string): void {
    setSelectedSkillNames((current) => (
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name]
    ));
  }

  function updateCronDraft(jobId: string, update: Partial<CloneCronJobDraft>): void {
    setCronDrafts((current) => ({
      ...current,
      [jobId]: { ...current[jobId], ...update },
    }));
  }

  function buildCronPayload(): Array<Record<string, unknown>> {
    return cronJobs.flatMap((job) => {
      const cronDraft = cronDrafts[job.id];
      if (!cronDraft?.selected) {
        return [];
      }
      const name = cronDraft.name.trim();
      if (!name) {
        throw new Error("Every preserved cron job requires a name");
      }
      let schedule: Record<string, unknown>;
      if (cronDraft.schedule_kind === "every") {
        const everySeconds = Number(cronDraft.every_seconds);
        if (!Number.isFinite(everySeconds) || everySeconds <= 0) {
          throw new Error(`Cron job ${name} requires a positive interval`);
        }
        schedule = { kind: "every", every_ms: everySeconds * 1000 };
      } else {
        const expr = cronDraft.cron_expr.trim();
        if (!expr) {
          throw new Error(`Cron job ${name} requires a cron expression`);
        }
        schedule = { kind: "cron", expr, tz: cronDraft.tz.trim() || null };
      }
      return [{
        name,
        enabled: cronDraft.enabled,
        message: cronDraft.message,
        origin_channel: cronDraft.origin_channel.trim() || null,
        origin_chat_id: cronDraft.origin_chat_id.trim() || null,
        origin_metadata: cronDraft.origin_metadata,
        session_key: cronDraft.session_key.trim() || null,
        recurring_session_key_format: cronDraft.recurring_session_key_format.trim() || null,
        delete_after_run: cronDraft.delete_after_run,
        schedule,
      }];
    });
  }

  async function cloneSoul(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const nextSoulId = soulId.trim();
    if (!nextSoulId) {
      notifyError("soul_id is required");
      return;
    }
    try {
      setPending(true);
      const created = await api<Soul>(`/api/souls/${encodeURIComponent(source.soul_id)}/clone`, {
        method: "POST",
        body: JSON.stringify({
          soul_id: nextSoulId,
          overrides: draftToOverrides(draft),
          prompt_files: SOUL_PROMPT_FILE_NAMES
            .filter((name) => promptSelection[name])
            .map((name) => ({ name, content: promptDraft[name] })),
          skill_names: selectedSkillNames,
          cron_jobs: buildCronPayload(),
          start: startNow,
        }),
      });
      toast.success(`${created.soul_id} cloned from ${source.soul_id}`);
      await onCloned(created);
    } catch (cause) {
      notifyError(cause);
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="panel clone-page-panel">
      <div className="panel-head clone-page-head">
        <div>
          <p className="eyebrow">Clone {source.soul_id}</p>
          <h2>Create an independent soul</h2>
          <p className="muted">
            Configure the clone before it is written.
          </p>
        </div>
        <button type="button" className="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </button>
      </div>

      <form className="clone-form" onSubmit={(event) => void cloneSoul(event)}>
        <section className="clone-section">
          <div className="create-soul-block-head">
            <h3>Identity and runtime</h3>
            <span className="muted">The directory name becomes the new soul ID.</span>
          </div>
          <div className="field-grid clone-config-grid">
            <label className="clone-wide-field">
              <span>Description</span>
              <textarea
                value={draft.description}
                onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
                placeholder="What this soul is for"
                rows={3}
              />
            </label>
            <label>
              <span>New soul ID</span>
              <input
                autoFocus
                value={soulId}
                onChange={(event) => setSoulId(event.target.value)}
                placeholder="reviewer-copy"
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
              <GroupListEditor
                value={selectedChannels}
                onChange={(next) => setDraft((current) => ({ ...current, channels: next.join(", ") }))}
                suggestions={enabledChannels}
                allowCustom={false}
                placeholder="Type an enabled channel, press Enter"
                emptyLabel="No channels"
                suggestionsLabel="Available:"
              />
            </label>
            <label htmlFor="clone-soul-groups-input">
              <span>Groups (display only)</span>
              <GroupListEditor
                inputId="clone-soul-groups-input"
                value={draft.groups}
                onChange={(next) => setDraft((current) => ({ ...current, groups: next }))}
                suggestions={allSoulGroups}
              />
            </label>
            <label className="clone-wide-field">
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
              <div className="field-grid clone-wide-field">
                {draft.mcp_servers.map((serverName) => (
                  <label key={serverName}>
                    <span>MCP headers: {serverName}</span>
                    <textarea
                      value={draft.mcp_http_headers[serverName] ?? "{}"}
                      onChange={(event) => {
                        const value = event.target.value;
                        setDraft((current) => ({
                          ...current,
                          mcp_http_headers: { ...current.mcp_http_headers, [serverName]: value },
                        }));
                      }}
                      rows={6}
                      spellCheck={false}
                    />
                  </label>
                ))}
              </div>
            ) : null}
            <label className="clone-wide-field">
              <span>Nanobot tool policy</span>
              <div className="selection-grid tool-selection-grid">
                {toolChoices.map((tool) => (
                  <label key={tool.name} className="check-tile tool-check-tile">
                    <span className="tool-choice-body">
                      <strong>{tool.name}</strong>
                      {tool.description ? <small title={tool.description}>{tool.description}</small> : null}
                    </span>
                    <select
                      value={getToolPolicyState(draft.enabled_tools, draft.disabled_tools, tool.name)}
                      onChange={(event) => {
                        setDraft((current) => updateDraftToolPolicy(
                          current,
                          tool.name,
                          event.target.value as ToolPolicyState,
                        ));
                      }}
                    >
                      <option value="inherit">
                        Inherit ({globalDisabledTools.includes(tool.name) ? "globally disabled" : "available"})
                      </option>
                      <option value="enabled">Enable for this soul</option>
                      <option value="disabled">Disable for this soul</option>
                    </select>
                  </label>
                ))}
              </div>
            </label>
          </div>
        </section>

        <section className="clone-section">
          <div className="create-soul-block-head">
            <h3>Prompt files</h3>
            <button
              type="button"
              className="ghost"
              onClick={() => void loadPromptFiles()}
              disabled={promptPending || pending}
            >
              {promptPending ? "Loading…" : "Refresh"}
            </button>
          </div>
          {promptError ? <div className="banner error">{promptError}</div> : null}
          <div className="md-file-list">
            {promptFiles.length ? SOUL_PROMPT_FILE_NAMES.map((name) => {
              const file = promptFiles.find((item) => item.name === name);
              const exists = file?.exists ?? false;
              const selected = promptSelection[name];
              return (
                <details key={name} className="md-file" open={selected}>
                  <summary
                    onClick={(event) => {
                      event.preventDefault();
                      togglePromptFile(name);
                    }}
                  >
                    <span className="md-file-title editable">
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => togglePromptFile(name)}
                        onClick={(event) => event.stopPropagation()}
                      />
                      <span>{name}</span>
                    </span>
                    <span className={`pill ${exists ? "live" : "idle"}`}>
                      {exists ? "present" : "missing"}
                    </span>
                  </summary>
                  {selected ? (
                    <label>
                      <span>{name}</span>
                      <textarea
                        value={promptDraft[name]}
                        onChange={(event) => setPromptDraft((current) => ({
                          ...current,
                          [name]: event.target.value,
                        }))}
                        placeholder={`Enter ${name} content`}
                      />
                    </label>
                  ) : (
                    <p className="muted">Enable this file to preserve or edit it in the cloned soul.</p>
                  )}
                </details>
              );
            }) : (
              <p className="muted">{promptPending ? "Loading prompt files…" : "No prompt files available."}</p>
            )}
          </div>
        </section>

        <section className="clone-section">
          <div className="create-soul-block-head">
            <h3>Skills</h3>
            <span className="muted">Select each skill to preserve its current link or directory form.</span>
          </div>
          <div className="selection-grid">
            {source.skills.map((skill) => (
              <label key={skill.name} className="check-tile clone-skill-tile">
                <input
                  type="checkbox"
                  checked={selectedSkillNames.includes(skill.name)}
                  onChange={() => toggleSkill(skill.name)}
                />
                <span>
                  <strong>{skill.name}</strong>
                  <small>{skill.link_target ? "symbolic link" : "directory copy"}</small>
                </span>
              </label>
            ))}
            {!source.skills.length ? <p className="muted">This soul has no installed skills.</p> : null}
          </div>
        </section>

        <section className="clone-section">
          <div className="create-soul-block-head">
            <div>
              <h3>Cron jobs</h3>
              <span className="muted">
                Select and edit the jobs to recreate with fresh runtime state.
              </span>
            </div>
            <button
              type="button"
              className="ghost"
              onClick={() => void loadCronJobs()}
              disabled={cronPending || pending}
            >
              {cronPending ? "Loading…" : "Refresh"}
            </button>
          </div>
          {cronError ? <div className="banner error">{cronError}</div> : null}
          <div className="md-file-list">
            {cronJobs.map((job) => {
              const cronDraft = cronDrafts[job.id];
              if (!cronDraft) {
                return null;
              }
              const disabled = pending || !cronDraft.selected;
              return (
                <details key={job.id} className="md-file clone-cron-job">
                  <summary>
                    <span className="md-file-title editable">
                      <input
                        type="checkbox"
                        checked={cronDraft.selected}
                        onChange={(event) => updateCronDraft(job.id, { selected: event.target.checked })}
                        onClick={(event) => event.stopPropagation()}
                      />
                      <span>{cronDraft.name || job.name}</span>
                    </span>
                    <span className={`pill ${cronDraft.selected ? "live" : "idle"}`}>
                      {cronDraft.schedule_kind}
                    </span>
                  </summary>
                  <div className="field-grid clone-cron-grid">
                    <label>
                      <span>Name</span>
                      <input
                        value={cronDraft.name}
                        onChange={(event) => updateCronDraft(job.id, { name: event.target.value })}
                        disabled={disabled}
                      />
                    </label>
                    <label>
                      <span>Schedule type</span>
                      <select
                        value={cronDraft.schedule_kind}
                        onChange={(event) => updateCronDraft(job.id, {
                          schedule_kind: event.target.value as "every" | "cron",
                        })}
                        disabled={disabled}
                      >
                        <option value="cron">cron expression</option>
                        <option value="every">every N seconds</option>
                      </select>
                    </label>
                    {cronDraft.schedule_kind === "cron" ? (
                      <>
                        <label>
                          <span>Cron expression</span>
                          <input
                            value={cronDraft.cron_expr}
                            onChange={(event) => updateCronDraft(job.id, { cron_expr: event.target.value })}
                            placeholder="0 9 * * *"
                            disabled={disabled}
                          />
                        </label>
                        <label>
                          <span>Timezone</span>
                          <input
                            value={cronDraft.tz}
                            onChange={(event) => updateCronDraft(job.id, { tz: event.target.value })}
                            placeholder="UTC"
                            disabled={disabled}
                          />
                        </label>
                      </>
                    ) : (
                      <label>
                        <span>Every (seconds)</span>
                        <input
                          type="number"
                          value={cronDraft.every_seconds}
                          onChange={(event) => updateCronDraft(job.id, { every_seconds: event.target.value })}
                          placeholder="3600"
                          disabled={disabled}
                        />
                      </label>
                    )}
                    <label className="clone-wide-field">
                      <span>Message</span>
                      <textarea
                        value={cronDraft.message}
                        onChange={(event) => updateCronDraft(job.id, { message: event.target.value })}
                        disabled={disabled}
                      />
                    </label>
                    <label>
                      <span>Origin channel</span>
                      <input
                        value={cronDraft.origin_channel}
                        onChange={(event) => updateCronDraft(job.id, { origin_channel: event.target.value })}
                        placeholder="optional"
                        disabled={disabled}
                      />
                    </label>
                    <label>
                      <span>Origin chat ID</span>
                      <input
                        value={cronDraft.origin_chat_id}
                        onChange={(event) => updateCronDraft(job.id, { origin_chat_id: event.target.value })}
                        placeholder="optional"
                        disabled={disabled}
                      />
                    </label>
                    <label>
                      <span>Session key</span>
                      <input
                        value={cronDraft.session_key}
                        onChange={(event) => updateCronDraft(job.id, { session_key: event.target.value })}
                        placeholder="optional, e.g. cli:direct"
                        disabled={disabled}
                      />
                    </label>
                    <label>
                      <span>Recurring session key format</span>
                      <input
                        value={cronDraft.recurring_session_key_format}
                        onChange={(event) => updateCronDraft(job.id, {
                          recurring_session_key_format: event.target.value,
                        })}
                        placeholder="%Y-%m-%d"
                        disabled={disabled}
                      />
                    </label>
                    <div className="clone-cron-flags clone-wide-field">
                      <label className="checkbox">
                        <input
                          type="checkbox"
                          checked={cronDraft.enabled}
                          onChange={(event) => updateCronDraft(job.id, { enabled: event.target.checked })}
                          disabled={disabled}
                        />
                        <span>Enabled</span>
                      </label>
                      <label className="checkbox">
                        <input
                          type="checkbox"
                          checked={cronDraft.delete_after_run}
                          onChange={(event) => updateCronDraft(job.id, {
                            delete_after_run: event.target.checked,
                          })}
                          disabled={disabled}
                        />
                        <span>Delete after run</span>
                      </label>
                    </div>
                  </div>
                </details>
              );
            })}
            {!cronJobs.length ? (
              <p className="muted">{cronPending ? "Loading cron jobs…" : "This soul has no cron jobs."}</p>
            ) : null}
          </div>
        </section>

        <section className="clone-section clone-start-section">
          <label className="checkbox">
            <input
              type="checkbox"
              checked={draft.autostart}
              onChange={(event) => setDraft((current) => ({ ...current, autostart: event.target.checked }))}
            />
            <span>Autostart on future Soulboard boots</span>
          </label>
          <label className="checkbox">
            <input type="checkbox" checked={startNow} onChange={(event) => setStartNow(event.target.checked)} />
            <span>Start cloned soul immediately</span>
          </label>
        </section>

        <div className="clone-submit-row">
          <button type="button" className="ghost" onClick={onCancel} disabled={pending}>Cancel</button>
          <button
            type="submit"
            disabled={pending || promptPending || cronPending || !!promptError || !!cronError || !soulId.trim()}
          >
            {pending ? "Cloning…" : "Clone soul"}
          </button>
        </div>
      </form>
    </section>
  );
}
