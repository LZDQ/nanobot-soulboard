import { renderContent, summarizeToolResult } from "../lib/format";

type ToolResultDetailsProps = {
  content: unknown;
};

type ParsedResult = {
  isJson: boolean;
  value: unknown;
};

function parseResult(content: unknown): ParsedResult {
  if (typeof content !== "string") {
    return { isJson: content !== undefined, value: content };
  }

  try {
    return { isJson: true, value: JSON.parse(content) as unknown };
  } catch {
    return { isJson: false, value: content };
  }
}

function JsonPrimitive({ value }: { value: unknown }) {
  if (typeof value === "string") {
    return <span className="json-string">{JSON.stringify(value)}</span>;
  }
  if (value === null) {
    return <span className="json-null">null</span>;
  }
  return <span className="json-primitive">{String(value)}</span>;
}

function JsonEntries({ entries }: { entries: Array<[string, unknown]> }) {
  if (!entries.length) {
    return <span className="muted json-empty">empty</span>;
  }

  return (
    <div className="json-fields">
      {entries.map(([key, value]) => (
        <div className="json-field" key={key}>
          <code className="json-key">{key}</code>
          <div className="json-value"><JsonValue value={value} /></div>
        </div>
      ))}
    </div>
  );
}

function JsonValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    return (
      <details className="json-nested" open>
        <summary>Array ({value.length})</summary>
        {value.length ? (
          <div className="json-array">
            {value.map((item, index) => (
              <div className="json-array-item" key={index}><JsonValue value={item} /></div>
            ))}
          </div>
        ) : (
          <span className="muted json-empty">empty</span>
        )}
      </details>
    );
  }

  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>);
    return (
      <details className="json-nested" open>
        <summary>Object ({entries.length})</summary>
        <JsonEntries entries={entries} />
      </details>
    );
  }

  return <JsonPrimitive value={value} />;
}

export function ToolResultDetails({ content }: ToolResultDetailsProps) {
  const parsed = parseResult(content);

  return (
    <details className="tool-result-details">
      <summary>Result: {summarizeToolResult(content)}</summary>
      {parsed.isJson ? (
        <div className="json-result-viewer"><JsonValue value={parsed.value} /></div>
      ) : (
        <pre>{renderContent(content) || "(empty)"}</pre>
      )}
    </details>
  );
}
