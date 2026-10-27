import type {
  StreamChunkMessage,
  StreamFinalizedMessage,
  StreamResetMessage,
  ToolStreamEvent,
} from "../types";

export type PendingUserMessage = {
  id: string;
  content: string;
  sentAt: string;
};

export type ChatTranscriptState = {
  messages: Array<Record<string, unknown>>;
  pending: PendingUserMessage[];
  answer: string;
  reasoning: string;
  tools: ToolStreamEvent[];
  toolHints: string[];
};

export type ChatTranscriptAction =
  | { type: "clear" }
  | { type: "replace_history"; messages: Array<Record<string, unknown>> }
  | { type: "merge_history"; messages: Array<Record<string, unknown>> }
  | { type: "send"; message: PendingUserMessage }
  | { type: "reset_stream"; message: StreamResetMessage }
  | { type: "stream_chunk"; message: StreamChunkMessage }
  | { type: "finalize"; message: StreamFinalizedMessage };

export const emptyChatTranscript: ChatTranscriptState = {
  messages: [],
  pending: [],
  answer: "",
  reasoning: "",
  tools: [],
  toolHints: [],
};

function historyIndex(message: Record<string, unknown>): number | null {
  return typeof message.history_index === "number" ? message.history_index : null;
}

function mergeMessages(
  current: Array<Record<string, unknown>>,
  incoming: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  const byIndex = new Map<number, Record<string, unknown>>();
  const withoutIndex: Array<Record<string, unknown>> = [];
  for (const message of [...current, ...incoming]) {
    const index = historyIndex(message);
    if (index === null) {
      withoutIndex.push(message);
    } else {
      byIndex.set(index, message);
    }
  }
  return [
    ...Array.from(byIndex.entries())
      .sort(([left], [right]) => left - right)
      .map(([, message]) => message),
    ...withoutIndex,
  ];
}

function mergeToolEvents(current: ToolStreamEvent[], incoming: ToolStreamEvent[]): ToolStreamEvent[] {
  const byId = new Map(current.map((event) => [event.call_id, event]));
  for (const event of incoming) {
    byId.set(event.call_id, event);
  }
  return Array.from(byId.values());
}

export function chatTranscriptReducer(
  state: ChatTranscriptState,
  action: ChatTranscriptAction,
): ChatTranscriptState {
  if (action.type === "clear") {
    return emptyChatTranscript;
  }
  if (action.type === "replace_history") {
    return { ...emptyChatTranscript, messages: mergeMessages([], action.messages) };
  }
  if (action.type === "merge_history") {
    return { ...state, messages: mergeMessages(state.messages, action.messages) };
  }
  if (action.type === "send") {
    return { ...state, pending: [...state.pending, action.message] };
  }
  if (action.type === "reset_stream") {
    return {
      ...state,
      answer: action.message.content ?? "",
      reasoning: action.message.reasoning_content ?? "",
      tools: action.message.tool_events ?? [],
      toolHints: [],
    };
  }
  if (action.type === "stream_chunk") {
    const content = action.message.content ?? "";
    if (action.message.kind === "tool") {
      const toolEvents = action.message.tool_events ?? [];
      return {
        ...state,
        tools: mergeToolEvents(state.tools, toolEvents),
        toolHints: content && !toolEvents.length ? [...state.toolHints, content] : state.toolHints,
      };
    }
    if (action.message.kind === "reasoning") {
      return { ...state, reasoning: state.reasoning + content };
    }
    return { ...state, answer: state.answer + content };
  }

  return {
    ...state,
    messages: mergeMessages(state.messages, [action.message]),
    pending: action.message.client_message_id
      ? state.pending.filter((message) => message.id !== action.message.client_message_id)
      : state.pending,
    answer: "",
    reasoning: "",
    tools: [],
    toolHints: [],
  };
}
