import type { Dispatch, SetStateAction } from "react";

import { getEmptyMcpDraft, mcpConfigToDraft } from "../../lib/drafts";
import { renderEnabledList, renderMcpTypeLabel } from "../../lib/format";
import type { MCPServer, MCPServerDraft } from "../../types";

type McpMode = "view" | "edit" | "create";

type McpServersDialogProps = {
  pending: string;
  mcpServers: MCPServer[];
  mcpServerCount: number;
  selectedMcpServerName: string;
  createMcpServerName: string;
  mcpMode: McpMode;
  mcpDraft: MCPServerDraft;
  createMcpDraft: MCPServerDraft;
  setSelectedMcpServerName: (name: string) => void;
  setCreateMcpServerName: (name: string) => void;
  setMcpMode: Dispatch<SetStateAction<McpMode>>;
  setMcpDraft: Dispatch<SetStateAction<MCPServerDraft>>;
  setCreateMcpDraft: Dispatch<SetStateAction<MCPServerDraft>>;
  onClose: () => void;
  onReload: () => void;
  onCreate: () => void;
  onUpdate: () => void;
  onDelete: () => void;
};

export function McpServersDialog({
  pending,
  mcpServers,
  mcpServerCount,
  selectedMcpServerName,
  createMcpServerName,
  mcpMode,
  mcpDraft,
  createMcpDraft,
  setSelectedMcpServerName,
  setCreateMcpServerName,
  setMcpMode,
  setMcpDraft,
  setCreateMcpDraft,
  onClose,
  onReload,
  onCreate,
  onUpdate,
  onDelete,
}: McpServersDialogProps) {
  const selectedMcpServer = mcpServers.find((server) => server.name === selectedMcpServerName) ?? null;
  const activeMcpDraft = mcpMode === "create" ? createMcpDraft : mcpDraft;

  return (
    <div className="modal-backdrop">
      <section
        className="registry-modal registry-modal-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-servers-title"
      >
        <div className="registry-modal-head">
          <button type="button" className="ghost" onClick={onClose} disabled={!!pending}>
            Close
          </button>
          <div>
            <h2 id="mcp-servers-title">MCP servers</h2>
            <p className="muted">{mcpServerCount} definitions</p>
          </div>
          <div className="registry-modal-actions">
            <button type="button" className="ghost" onClick={onReload} disabled={!!pending}>
              Reload
            </button>
          </div>
        </div>
        <div className="registry-modal-body">
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
                  <button onClick={onCreate} disabled={!!pending}>
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
                      <button onClick={onUpdate} disabled={!!pending}>
                        Save MCP server
                      </button>
                      <button className="danger" onClick={onDelete} disabled={!!pending}>
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
                      <button className="danger" onClick={onDelete} disabled={!!pending}>
                        Delete
                      </button>
                    </>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}
