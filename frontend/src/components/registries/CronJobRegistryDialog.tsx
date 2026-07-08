import type { Dispatch, SetStateAction } from "react";

import type { CronJobRegistryEntry, CronJobRegistryEntryDraft } from "../../types";

type CronJobRegistryDialogProps = {
  pending: string;
  cronJobRegistry: CronJobRegistryEntry[];
  cronRegistryCount: number;
  isEditing: boolean;
  draft: CronJobRegistryEntryDraft;
  setDraft: Dispatch<SetStateAction<CronJobRegistryEntryDraft>>;
  onClose: () => void;
  onRefresh: () => void;
  onToggleEdit: () => void;
  onAddEntry: () => void;
  onDeleteEntry: (name: string) => void;
  onMoveEntry: (name: string, direction: -1 | 1) => void;
};

export function CronJobRegistryDialog({
  pending,
  cronJobRegistry,
  cronRegistryCount,
  isEditing,
  draft,
  setDraft,
  onClose,
  onRefresh,
  onToggleEdit,
  onAddEntry,
  onDeleteEntry,
  onMoveEntry,
}: CronJobRegistryDialogProps) {
  return (
    <div className="modal-backdrop">
      <section
        className="registry-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cron-registry-title"
      >
        <div className="registry-modal-head">
          <button type="button" className="ghost" onClick={onClose} disabled={!!pending}>
            Close
          </button>
          <div>
            <h2 id="cron-registry-title">Cron job registry</h2>
            <p className="muted">{cronRegistryCount} templates</p>
          </div>
          <div className="registry-modal-actions">
            <button type="button" className="ghost" onClick={onRefresh} disabled={!!pending}>
              Refresh
            </button>
            <button type="button" className="ghost" onClick={onToggleEdit} disabled={!!pending}>
              {isEditing ? "Done" : "Edit"}
            </button>
          </div>
        </div>
        <div className="registry-modal-body">
          {isEditing ? (
            <div className="app-links-editor">
              <div className="details-stack" style={{ gap: "0.5rem" }}>
                <label>
                  <span>Name (unique ID)</span>
                  <input
                    value={draft.name}
                    onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
                    placeholder="daily-digest"
                    disabled={!!pending}
                  />
                </label>
                <label>
                  <span>Label (display name)</span>
                  <input
                    value={draft.label}
                    onChange={(event) => setDraft((current) => ({ ...current, label: event.target.value }))}
                    placeholder="Daily digest"
                    disabled={!!pending}
                  />
                </label>
                <label>
                  <span>Cron expr</span>
                  <input
                    value={draft.cron_expr}
                    onChange={(event) => setDraft((current) => ({ ...current, cron_expr: event.target.value }))}
                    placeholder="0 9 * * 1-5"
                    disabled={!!pending}
                  />
                </label>
                <label>
                  <span>Every (seconds)</span>
                  <input
                    type="number"
                    value={draft.every_seconds}
                    onChange={(event) => setDraft((current) => ({ ...current, every_seconds: event.target.value }))}
                    placeholder="3600"
                    disabled={!!pending}
                  />
                </label>
                <label>
                  <span>Timezone</span>
                  <input
                    value={draft.tz}
                    onChange={(event) => setDraft((current) => ({ ...current, tz: event.target.value }))}
                    placeholder="e.g. America/New_York"
                    disabled={!!pending}
                  />
                </label>
                <label>
                  <span>Message</span>
                  <input
                    value={draft.message}
                    onChange={(event) => setDraft((current) => ({ ...current, message: event.target.value }))}
                    placeholder="Run the daily summary"
                    disabled={!!pending}
                  />
                </label>
                <label>
                  <span>Channel</span>
                  <input
                    value={draft.channel}
                    onChange={(event) => setDraft((current) => ({ ...current, channel: event.target.value }))}
                    placeholder="whatsapp"
                    disabled={!!pending}
                  />
                </label>
                <label>
                  <span>Chat ID</span>
                  <input
                    value={draft.chat_id}
                    onChange={(event) => setDraft((current) => ({ ...current, chat_id: event.target.value }))}
                    placeholder="(optional, channel-local id)"
                    disabled={!!pending}
                  />
                </label>
                <label>
                  <span>Session key</span>
                  <input
                    value={draft.session_key}
                    onChange={(event) => setDraft((current) => ({ ...current, session_key: event.target.value }))}
                    placeholder="(optional, e.g. cli:direct)"
                    disabled={!!pending}
                  />
                </label>
                <label>
                  <span>Recurring session key format</span>
                  <input
                    value={draft.recurring_session_key_format}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        recurring_session_key_format: event.target.value,
                      }))
                    }
                    placeholder="%Y-%m-%d"
                    disabled={!!pending}
                  />
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={draft.deliver}
                    onChange={(event) => setDraft((current) => ({ ...current, deliver: event.target.checked }))}
                    disabled={!!pending}
                  />
                  <span>Deliver response</span>
                </label>
                <button type="button" onClick={onAddEntry} disabled={!!pending}>
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
                  {isEditing ? (
                    <div style={{ display: "flex", gap: "0.4rem" }}>
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => onMoveEntry(entry.name, -1)}
                        disabled={!!pending || cronJobRegistry.indexOf(entry) === 0}
                        aria-label="Move up"
                      >
                        &uarr;
                      </button>
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => onMoveEntry(entry.name, 1)}
                        disabled={!!pending || cronJobRegistry.indexOf(entry) === cronJobRegistry.length - 1}
                        aria-label="Move down"
                      >
                        &darr;
                      </button>
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => onDeleteEntry(entry.name)}
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
      </section>
    </div>
  );
}
