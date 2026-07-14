import { useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { api } from "../lib/api";
import {
  draftToOverrides,
  getToolChoices,
  getToolPolicyState,
  overridesToDraft,
  updateDraftToolPolicy,
  updateSoulMcpSelection,
} from "../lib/drafts";
import { notifyError } from "../lib/errors";
import type {
  DraftOverrides,
  MCPServer,
  NanobotTool,
  Soul,
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
  const [copyPromptFiles, setCopyPromptFiles] = useState(true);
  const [copySkills, setCopySkills] = useState(true);
  const [materializeSkillLinks, setMaterializeSkillLinks] = useState(true);
  const [copyCronJobs, setCopyCronJobs] = useState(false);
  const [copyOtherFiles, setCopyOtherFiles] = useState(true);
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
          copy_prompt_files: copyPromptFiles,
          copy_skills: copySkills,
          materialize_skill_links: materializeSkillLinks,
          copy_cron_jobs: copyCronJobs,
          copy_other_files: copyOtherFiles,
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
            Configure the clone before it is written. Memory and sessions always start empty.
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
            <h3>Workspace content</h3>
            <span className="muted">Choose what follows the source into the new directory.</span>
          </div>
          <div className="clone-copy-grid">
            <label className="check-tile clone-copy-tile">
              <input type="checkbox" checked={copyPromptFiles} onChange={(event) => setCopyPromptFiles(event.target.checked)} />
              <span><strong>Prompt files</strong><small>AGENTS, SOUL, USER, TOOLS, and SYSTEM markdown.</small></span>
            </label>
            <div className="check-tile clone-copy-tile">
              <input
                type="checkbox"
                checked={copySkills}
                onChange={(event) => setCopySkills(event.target.checked)}
                aria-label="Copy skills"
              />
              <span>
                <strong>Skills</strong>
                <small>Copy installed skills and choose how links are handled.</small>
                <select
                  value={materializeSkillLinks ? "materialize" : "preserve"}
                  onChange={(event) => setMaterializeSkillLinks(event.target.value === "materialize")}
                  disabled={!copySkills}
                  aria-label="Cloned skill link handling"
                >
                  <option value="materialize">Materialize links</option>
                  <option value="preserve">Preserve links</option>
                </select>
              </span>
            </div>
            <label className="check-tile clone-copy-tile">
              <input type="checkbox" checked={copyCronJobs} onChange={(event) => setCopyCronJobs(event.target.checked)} />
              <span><strong>Cron jobs</strong><small>May duplicate schedules and external deliveries.</small></span>
            </label>
            <label className="check-tile clone-copy-tile">
              <input type="checkbox" checked={copyOtherFiles} onChange={(event) => setCopyOtherFiles(event.target.checked)} />
              <span><strong>Other workspace files</strong><small>Copies files not managed by the categories above.</small></span>
            </label>
            <div className="check-tile clone-copy-tile clone-reset-tile" aria-disabled="true">
              <input type="checkbox" checked={false} disabled readOnly />
              <span><strong>Memory</strong><small>Always initialized empty for the cloned soul.</small></span>
            </div>
            <div className="check-tile clone-copy-tile clone-reset-tile" aria-disabled="true">
              <input type="checkbox" checked={false} disabled readOnly />
              <span><strong>Sessions</strong><small>Always cleared; conversations never cross identities.</small></span>
            </div>
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
          <button type="submit" disabled={pending || !soulId.trim()}>
            {pending ? "Cloning…" : "Clone soul"}
          </button>
        </div>
      </form>
    </section>
  );
}
