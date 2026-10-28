type ForkSessionDialogProps = {
  pending: string;
  sourceKey: string;
  carriedCount: number;
  totalCount: number;
  /** Preview of the reply the branch ends on. */
  cutPoint: string;
  targetKey: string;
  error: string;
  onTargetKeyChange: (value: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
};

export function ForkSessionDialog({
  pending,
  sourceKey,
  carriedCount,
  totalCount,
  cutPoint,
  targetKey,
  error,
  onTargetKeyChange,
  onCancel,
  onSubmit,
}: ForkSessionDialogProps) {
  const isPending = pending === "fork-session";
  return (
    <div className="modal-backdrop">
      <form
        className="registry-modal fork-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="fork-session-title"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <div className="registry-modal-head">
          <div>
            <h2 id="fork-session-title">Fork session</h2>
            <p className="muted">The branch starts as a copy and then runs on its own.</p>
          </div>
        </div>

        <div className="registry-modal-body fork-modal-body">
          <dl className="fork-summary">
            <div>
              <dt>Source</dt>
              <dd><code>{sourceKey}</code></dd>
            </div>
            <div>
              <dt>Carries</dt>
              <dd>
                {carriedCount} of {totalCount} {totalCount === 1 ? "message" : "messages"}
              </dd>
            </div>
            <div>
              <dt>Ends with</dt>
              <dd><q>{cutPoint}</q></dd>
            </div>
          </dl>

          <label>
            <span>New session key</span>
            <input
              autoFocus
              value={targetKey}
              onChange={(event) => onTargetKeyChange(event.target.value)}
              placeholder="cli:direct-fork"
              spellCheck={false}
              disabled={!!pending}
              aria-invalid={!!error}
              aria-describedby={error ? "fork-session-error" : undefined}
            />
          </label>
          {error ? (
            <p id="fork-session-error" className="fork-modal-error" role="alert">{error}</p>
          ) : (
            <p className="muted">Opens automatically once created.</p>
          )}
        </div>

        <div className="fork-modal-actions">
          <button type="button" className="ghost" onClick={onCancel} disabled={!!pending}>
            Cancel
          </button>
          <button type="submit" disabled={!!pending || !targetKey.trim() || !!error}>
            {isPending ? "Forking..." : "Create fork"}
          </button>
        </div>
      </form>
    </div>
  );
}
