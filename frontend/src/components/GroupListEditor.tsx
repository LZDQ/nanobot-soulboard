import { useState } from "react";

type GroupListEditorProps = {
  value: string[];
  onChange: (next: string[]) => void;
  suggestions: string[];
  inputId?: string;
};

export function GroupListEditor({
  value,
  onChange,
  suggestions,
  inputId,
}: GroupListEditorProps) {
  const [input, setInput] = useState("");

  function addRaw(raw: string) {
    const additions = raw
      .split(",")
      .map((token) => token.trim())
      .filter(Boolean);
    if (!additions.length) {
      setInput("");
      return;
    }
    const seen = new Set(value);
    const next = [...value];
    let added = false;
    for (const item of additions) {
      if (!seen.has(item)) {
        next.push(item);
        seen.add(item);
        added = true;
      }
    }
    if (added) onChange(next);
    setInput("");
  }

  function removeAt(index: number) {
    onChange(value.filter((_, idx) => idx !== index));
  }

  const available = suggestions.filter((group) => !value.includes(group));

  return (
    <div className="group-editor">
      <div className="group-editor-chips">
        {value.length ? (
          value.map((group, index) => (
            <span key={`${group}-${index}`} className="group-chip">
              <span>{group}</span>
              <button
                type="button"
                className="group-chip-remove"
                aria-label={`Remove ${group}`}
                onClick={() => removeAt(index)}
              >
                &times;
              </button>
            </span>
          ))
        ) : (
          <span className="muted group-editor-empty">No groups</span>
        )}
      </div>
      <div className="group-editor-input">
        <input
          id={inputId}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === ",") {
              event.preventDefault();
              addRaw(input);
            } else if (event.key === "Backspace" && !input && value.length) {
              event.preventDefault();
              removeAt(value.length - 1);
            }
          }}
          onBlur={() => {
            if (input.trim()) addRaw(input);
          }}
          placeholder="Type a group name, press Enter"
        />
        <button
          type="button"
          className="ghost"
          onClick={() => addRaw(input)}
          disabled={!input.trim()}
        >
          Add
        </button>
      </div>
      {available.length ? (
        <div className="group-editor-suggestions">
          <span className="muted">Existing:</span>
          {available.map((group) => (
            <button
              key={group}
              type="button"
              className="ghost group-suggestion"
              onClick={() => addRaw(group)}
            >
              + {group}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
