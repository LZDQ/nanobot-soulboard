import type { Dispatch, SetStateAction } from "react";

import {
  getToolChoices,
  getToolOverrideState,
  updateDraftToolOverride,
  updateSoulMcpSelection,
} from "../lib/drafts";
import type {
  CreateSoulSkillDraft,
  CronJobRegistryEntry,
  DraftOverrides,
  MCPServer,
  NanobotTool,
  SkillPool,
  ToolOverrideState,
} from "../types";
import { GroupListEditor } from "./GroupListEditor";

type CreateSoulDialogProps = {
  pending: string;
  soulId: string;
  draft: DraftOverrides;
  enabledChannels: string[];
  allSoulGroups: string[];
  mcpServers: MCPServer[];
  nanobotTools: NanobotTool[];
  globalToolOverrides: Record<string, boolean>;
  cronJobRegistry: CronJobRegistryEntry[];
  cronJobNames: string[];
  skillPools: SkillPool[];
  skillDrafts: Record<string, CreateSoulSkillDraft>;
  setSoulId: (value: string) => void;
  setDraft: Dispatch<SetStateAction<DraftOverrides>>;
  setCronJobNames: Dispatch<SetStateAction<string[]>>;
  onCancel: () => void;
  onToggleSkill: (skillPath: string, enabled: boolean) => void;
  onUpdateSkillMode: (skillPath: string, mode: "symlink" | "copy") => void;
  onUpdateSkillTarget: (skillPath: string, targetName: string) => void;
  onCreate: () => void;
};

export function CreateSoulDialog({
  pending,
  soulId,
  draft,
  enabledChannels,
  allSoulGroups,
  mcpServers,
  nanobotTools,
  globalToolOverrides,
  cronJobRegistry,
  cronJobNames,
  skillPools,
  skillDrafts,
  setSoulId,
  setDraft,
  setCronJobNames,
  onCancel,
  onToggleSkill,
  onUpdateSkillMode,
  onUpdateSkillTarget,
  onCreate,
}: CreateSoulDialogProps) {
  const selectedSkillCount = Object.values(skillDrafts).filter((skill) => skill.enabled).length;
  const selectedChannels = draft.channels
    .split(",")
    .map((channel) => channel.trim())
    .filter(Boolean);
  const toolChoices = getToolChoices(nanobotTools, globalToolOverrides, draft.tool_overrides);

  return (
    <div className="modal-backdrop">
      <section
        className="create-soul-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-soul-title"
      >
        <div className="create-soul-modal-head">
          <button
            type="button"
            className="ghost"
            onClick={onCancel}
            disabled={!!pending}
          >
            Cancel
          </button>
          <div>
            <h2 id="create-soul-title">Create new soul</h2>
            <p className="muted">Prompt files are configured after creation from the soul details panel.</p>
          </div>
        </div>

        <div className="create-soul-modal-body">
          <section className="create-soul-block">
            <div className="create-soul-block-head">
              <h3>Options</h3>
              <span className="muted">Identity, runtime overrides, groups, and MCP access.</span>
            </div>
            <div className="field-grid create-soul-options-grid">
              <label>
                <span>Soul ID</span>
                <input
                  autoFocus
                  value={soulId}
                  onChange={(event) => setSoulId(event.target.value)}
                  placeholder="reviewer"
                />
              </label>
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
                <GroupListEditor
                  value={selectedChannels}
                  onChange={(next) => setDraft((current) => ({ ...current, channels: next.join(", ") }))}
                  suggestions={enabledChannels}
                  allowCustom={false}
                  placeholder="Type an enabled channel, press Enter"
                  emptyLabel="No channels"
                  suggestionsLabel="Available:"
                />
                {!enabledChannels.length ? (
                  <span className="muted">No enabled channels found in the base config.</span>
                ) : null}
              </label>
              <label htmlFor="create-soul-modal-groups-input">
                <span>Groups (display only)</span>
                <GroupListEditor
                  inputId="create-soul-modal-groups-input"
                  value={draft.groups}
                  onChange={(next) => setDraft((current) => ({ ...current, groups: next }))}
                  suggestions={allSoulGroups}
                />
              </label>
              <label className="create-soul-wide-field">
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
                <div className="field-grid create-soul-wide-field">
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
              <label className="create-soul-wide-field">
                <span>Nanobot tool overrides</span>
                <div className="selection-grid tool-selection-grid">
                  {toolChoices.map((tool) => (
                    <label key={tool.name} className="check-tile tool-check-tile">
                      <span className="tool-choice-body">
                        <strong>{tool.name}</strong>
                        {tool.description ? <small title={tool.description}>{tool.description}</small> : null}
                      </span>
                      <select
                        value={getToolOverrideState(draft.tool_overrides, tool.name)}
                        onChange={(event) => {
                          setDraft((current) => updateDraftToolOverride(current, tool.name, event.target.value as ToolOverrideState));
                        }}
                      >
                        <option value="inherit">Inherit{tool.name in globalToolOverrides ? ` (${globalToolOverrides[tool.name] ? "enabled" : "disabled"})` : ""}</option>
                        <option value="enabled">Enable</option>
                        <option value="disabled">Disable</option>
                      </select>
                    </label>
                  ))}
                  {!toolChoices.length ? <p className="muted">No nanobot tools reported by the backend.</p> : null}
                </div>
              </label>
              <label className="checkbox create-soul-wide-field">
                <input
                  type="checkbox"
                  checked={draft.autostart}
                  onChange={(event) => setDraft((current) => ({ ...current, autostart: event.target.checked }))}
                />
                <span>Autostart on server boot</span>
              </label>
            </div>
          </section>

          <section className="create-soul-block">
            <div className="create-soul-block-head">
              <h3>Cron jobs</h3>
              <span className="muted">{cronJobNames.length} selected</span>
            </div>
            {cronJobRegistry.length > 0 ? (
              <div className="create-soul-toggle-list">
                {cronJobRegistry.map((entry) => (
                  <label key={entry.name} className="create-soul-toggle-row">
                    <input
                      type="checkbox"
                      checked={cronJobNames.includes(entry.name)}
                      onChange={(event) => {
                        setCronJobNames((current) =>
                          event.target.checked
                            ? [...current, entry.name]
                            : current.filter((name) => name !== entry.name)
                        );
                      }}
                    />
                    <span>
                      <strong>{entry.label || entry.name}</strong>
                      <code>{entry.name}</code>
                      {entry.cron_expr ? <small>{entry.cron_expr}{entry.tz ? ` (${entry.tz})` : ""}</small> : null}
                      {entry.every_seconds ? <small>every {entry.every_seconds}s</small> : null}
                    </span>
                  </label>
                ))}
              </div>
            ) : (
              <p className="muted">No cron job templates registered.</p>
            )}
          </section>

          <section className="create-soul-block">
            <div className="create-soul-block-head">
              <h3>Skills</h3>
              <span className="muted">{selectedSkillCount} selected</span>
            </div>
            {skillPools.some((pool) => pool.skills.length) ? (
              <div className="create-soul-skill-list">
                {skillPools.map((pool) => (
                  pool.skills.length ? (
                    <div key={pool.path} className="create-soul-skill-pool">
                      <h4><code>{pool.path}</code></h4>
                      {pool.skills.map((skill) => {
                        const skillDraft = skillDrafts[skill.skill_path];
                        const enabled = skillDraft?.enabled ?? false;
                        const mode = skillDraft?.mode ?? "symlink";
                        return (
                          <article key={skill.skill_path} className={`create-soul-skill-row ${enabled ? "enabled" : ""}`}>
                            <label className="create-soul-skill-choice">
                              <input
                                type="checkbox"
                                checked={enabled}
                                onChange={(event) => onToggleSkill(skill.skill_path, event.target.checked)}
                              />
                              <span>
                                <strong>{skill.name}</strong>
                                <code>{skill.relative_path}</code>
                                {skill.description ? <small>{skill.description}</small> : null}
                              </span>
                            </label>
                            {enabled ? (
                              <div className="create-soul-skill-controls">
                                <fieldset className="registry-mode" disabled={!!pending}>
                                  <legend>Mode</legend>
                                  <label className="registry-mode-option">
                                    <input
                                      type="radio"
                                      name={`create-soul-skill-mode-${skill.skill_path}`}
                                      value="symlink"
                                      checked={mode === "symlink"}
                                      onChange={() => onUpdateSkillMode(skill.skill_path, "symlink")}
                                    />
                                    <span><strong>Soft link</strong> &mdash; track the pool source live</span>
                                  </label>
                                  <label className="registry-mode-option">
                                    <input
                                      type="radio"
                                      name={`create-soul-skill-mode-${skill.skill_path}`}
                                      value="copy"
                                      checked={mode === "copy"}
                                      onChange={() => onUpdateSkillMode(skill.skill_path, "copy")}
                                    />
                                    <span><strong>Copy</strong> &mdash; soul-specific writable copy</span>
                                  </label>
                                </fieldset>
                                <label>
                                  <span>Skill folder name</span>
                                  <input
                                    value={skillDraft?.target_name ?? ""}
                                    onChange={(event) => onUpdateSkillTarget(skill.skill_path, event.target.value)}
                                    placeholder="optional rename"
                                    disabled={!!pending}
                                  />
                                </label>
                              </div>
                            ) : null}
                          </article>
                        );
                      })}
                    </div>
                  ) : null
                ))}
              </div>
            ) : (
              <p className="muted">No skills loaded from pools yet.</p>
            )}
          </section>
        </div>

        <div className="create-soul-modal-actions">
          <button
            type="button"
            onClick={onCreate}
            disabled={!!pending || !soulId.trim()}
          >
            {pending === "create" ? "Creating..." : "Create soul"}
          </button>
        </div>
      </section>
    </div>
  );
}
