import type { ToolStreamEvent } from "../types";
import { renderContent } from "../lib/format";

type StreamingToolsProps = {
  events: ToolStreamEvent[];
  hints: string[];
};

export function StreamingTools({ events, hints }: StreamingToolsProps) {
  if (!events.length && !hints.length) {
    return null;
  }

  return (
    <details className="tool-result-details" open>
      <summary>Tools ({events.length || hints.length})</summary>
      {events.map((event) => (
        <div className="tool-call-payload" key={event.call_id}>
          <div className="tool-call-payload-title">
            {event.name || "(unnamed tool)"} · {event.phase}
          </div>
          {Object.keys(event.arguments).length ? (
            <pre className="tool-call-payload-value">{renderContent(event.arguments)}</pre>
          ) : null}
          {event.phase === "end" && event.result !== null ? (
            <pre className="tool-call-payload-value">{renderContent(event.result)}</pre>
          ) : null}
          {event.error ? <pre className="tool-call-payload-value">{event.error}</pre> : null}
        </div>
      ))}
      {hints.map((hint, index) => <pre key={index}>{hint}</pre>)}
    </details>
  );
}
