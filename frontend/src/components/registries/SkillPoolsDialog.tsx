import { formatSkillTextStats } from "../../lib/format";
import type { SkillPool } from "../../types";

type SkillPoolsDialogProps = {
  pending: string;
  skillPools: SkillPool[];
  skillPoolCount: number;
  isEditing: boolean;
  newSkillRegistryPath: string;
  onClose: () => void;
  onRefresh: () => void;
  onToggleEdit: () => void;
  onNewSkillRegistryPathChange: (value: string) => void;
  onAddSkillRegistryEntry: () => void;
  onDeleteSkillRegistryEntry: (path: string) => void;
};

export function SkillPoolsDialog({
  pending,
  skillPools,
  skillPoolCount,
  isEditing,
  newSkillRegistryPath,
  onClose,
  onRefresh,
  onToggleEdit,
  onNewSkillRegistryPathChange,
  onAddSkillRegistryEntry,
  onDeleteSkillRegistryEntry,
}: SkillPoolsDialogProps) {
  return (
    <div className="modal-backdrop">
      <section
        className="registry-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="skill-pools-title"
      >
        <div className="registry-modal-head">
          <button type="button" className="ghost" onClick={onClose} disabled={!!pending}>
            Close
          </button>
          <div>
            <h2 id="skill-pools-title">Skill pools</h2>
            <p className="muted">{skillPoolCount} pools</p>
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
              <div className="app-links-editor-row">
                <input
                  value={newSkillRegistryPath}
                  onChange={(event) => onNewSkillRegistryPathChange(event.target.value)}
                  placeholder="~/skills"
                  disabled={!!pending}
                />
                <button type="button" onClick={onAddSkillRegistryEntry} disabled={!!pending}>
                  Add pool
                </button>
              </div>
            </div>
          ) : null}
          <div className="registry-card-list">
            {skillPools.length ? skillPools.map((pool) => (
              <article key={pool.path} className="registry-card">
                <div className="registry-card-head skill-pool-card-head">
                  <code>{pool.path}</code>
                  <div className="skill-pool-card-meta">
                    <div className="registry-pills skill-pool-card-pills">
                      <span className={`pill ${pool.exists ? "live" : "idle"}`}>
                        {pool.exists ? "pool present" : "missing"}
                      </span>
                      <span className="pill idle">
                        {pool.skills.length} skill{pool.skills.length === 1 ? "" : "s"}
                      </span>
                    </div>
                    {isEditing ? (
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => onDeleteSkillRegistryEntry(pool.path)}
                        disabled={!!pending}
                      >
                        Remove
                      </button>
                    ) : null}
                  </div>
                </div>
                {pool.skills.length ? (
                  <div className="skill-list" style={{ marginTop: "0.5rem" }}>
                    {pool.skills.map((skill) => (
                      <details key={skill.skill_path} className="skill-entry-details">
                        <summary className="skill-entry-summary">
                          <div className="skill-entry-title-line">
                            <strong>{skill.name}</strong>
                            <code>{skill.relative_path}</code>
                            {formatSkillTextStats(skill).length ? (
                              <span className="skill-text-stats">{formatSkillTextStats(skill).join(" / ")}</span>
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
      </section>
    </div>
  );
}
