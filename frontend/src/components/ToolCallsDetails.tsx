import { useState } from "react";

type ToolCallsDetailsProps = {
  toolCalls: Array<Record<string, unknown>>;
};

function formatPayload(payload: unknown): string {
  if (typeof payload === "string") {
    return payload;
  }

  if (payload === undefined) {
    return "undefined";
  }

  return JSON.stringify(payload, null, 2) ?? String(payload);
}

function parsePayload(payload: unknown): unknown {
  if (typeof payload !== "string") {
    return payload;
  }

  try {
    return JSON.parse(payload) as unknown;
  } catch {
    return payload;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getToolCallLabel(toolCall: Record<string, unknown>): string {
  if (toolCall.type === "function" && isRecord(toolCall.function)) {
    const name = toolCall.function.name;
    return typeof name === "string" && name ? name : "(unnamed function)";
  }

  const id = typeof toolCall.id === "string" && toolCall.id ? toolCall.id : "(no id)";
  const type = typeof toolCall.type === "string" && toolCall.type ? toolCall.type : "(no type)";
  return `${id} · ${type}`;
}

function getToolCallPayload(toolCall: Record<string, unknown>): unknown {
  if (toolCall.type === "function" && isRecord(toolCall.function)) {
    return toolCall.function.arguments;
  }

  return toolCall;
}

export function ToolCallsDetails({ toolCalls }: ToolCallsDetailsProps) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const safeSelectedIndex = selectedIndex < toolCalls.length ? selectedIndex : 0;
  const selectedToolCall = toolCalls[safeSelectedIndex];
  const selectedToolCallLabel = selectedToolCall ? getToolCallLabel(selectedToolCall) : "";
  const selectedPayload = selectedToolCall ? parsePayload(getToolCallPayload(selectedToolCall)) : null;
  const payloadFields = isRecord(selectedPayload) ? Object.entries(selectedPayload) : null;

  return (
    <details className="tool-calls-details">
      <summary>Tool calls ({toolCalls.length})</summary>
      <div className="tool-calls-browser">
        <div className="tool-calls-list" aria-label="Tool calls">
          {toolCalls.map((toolCall, index) => (
            <button
              key={`${typeof toolCall.id === "string" ? toolCall.id : "tool-call"}-${index}`}
              type="button"
              className={`tool-call-row${safeSelectedIndex === index ? " selected" : ""}`}
              aria-pressed={safeSelectedIndex === index}
              onClick={() => setSelectedIndex(index)}
            >
              {getToolCallLabel(toolCall)}
            </button>
          ))}
        </div>
        <div className="tool-call-payload">
          <div className="tool-call-payload-title">{selectedToolCallLabel}</div>
          {payloadFields ? (
            payloadFields.length ? payloadFields.map(([name, value]) => (
              <div className="tool-call-payload-field" key={name}>
                <code className="tool-call-payload-name">{name}</code>
                <pre className="tool-call-payload-value">{formatPayload(value)}</pre>
              </div>
            )) : (
              <span className="muted">No fields</span>
            )
          ) : (
            <pre className="tool-call-payload-value">{formatPayload(selectedPayload)}</pre>
          )}
        </div>
      </div>
    </details>
  );
}
