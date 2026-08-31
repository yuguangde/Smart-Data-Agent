/**
 * HTTP / SSE client for the Smart Data Agent FastAPI backend.
 *
 * Routes consumed:
 *   POST /threads            -> { thread_id, created_at }
 *   POST /chat               -> { thread_id, message, iterations, tool_calls }
 *   POST /chat/stream        -> SSE stream
 *   GET  /threads/{id}       -> { thread_id, messages: [...] }
 */
import type {
  SendMessageBody,
  StreamEvent,
  ThreadCreateResponse,
  ThreadHistoryResponse,
} from "@/types/chat";

/** Resolved at build time via Vite (`VITE_API_BASE`). Defaults to `/api`. */
const API_BASE =
  (import.meta.env?.VITE_API_BASE as string | undefined) || "/api";

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(
      `API ${res.status} ${res.statusText}: ${body.slice(0, 200)}`,
    );
  }
  return (await res.json()) as T;
}

/** Create a new server-side thread and return its id. */
export async function createThread(): Promise<string> {
  const res = await fetch(`${API_BASE}/threads`, { method: "POST" });
  const json = await parseJson<ThreadCreateResponse>(res);
  return json.thread_id;
}

/** Fetch the persisted transcript for a thread. */
export async function fetchHistory(
  threadId: string,
): Promise<ThreadHistoryResponse> {
  const res = await fetch(
    `${API_BASE}/threads/${encodeURIComponent(threadId)}`,
  );
  return parseJson<ThreadHistoryResponse>(res);
}

/** Cancellable handle returned by {@link sendChatStream}. */
export interface ChatStreamHandle {
  /** Resolves once the server closes the stream. */
  promise: Promise<void>;
  /** Abort the underlying fetch; safe to call multiple times. */
  cancel: () => void;
}

/**
 * Stream a chat reply. Each decoded SSE frame is delivered to `onEvent`.
 * Network/parse errors are reported through `onError`.
 *
 * Event names emitted by the backend (see `app.services.agent_service.stream_events`):
 *   - `thread`     — initial frame carrying `{ thread_id }`  (FastAPI chat.py maps this as `message`)
 *   - `token`      — incremental text fragment, `data` is a string
 *   - `tool_start` — agent is invoking a tool, `data` is `{ id?, name, input? }`
 *   - `tool_end`   — tool finished, `data` is `{ id?, output }`
 *   - `message`    — final assistant message payload `{ role, content, ... }`
 *   - `done`       — run metadata `{ thread_id, iterations, tool_calls }`
 *   - `error`      — error frame, `data` is a string
 *   - `end`        — server-side sentinel instructing clients to close
 */
export function sendChatStream(
  body: SendMessageBody,
  onEvent: (ev: StreamEvent<unknown>) => void,
  onError?: (err: Error) => void,
): ChatStreamHandle {
  const controller = new AbortController();

  const promise = (async () => {
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/chat/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      if ((err as { name?: string }).name === "AbortError") return;
      onError?.(err instanceof Error ? err : new Error(String(err)));
      return;
    }

    if (!res.ok || !res.body) {
      const text = await res.text().catch(() => "");
      onError?.(new Error(`stream failed: ${res.status} ${text}`));
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let sep = buffer.indexOf("\n\n");
        while (sep !== -1) {
          const rawBlock = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          const parsed = parseSSEBlock(rawBlock);
          if (parsed) onEvent(parsed);
          sep = buffer.indexOf("\n\n");
        }
      }
      // Drain any trailing frame that didn't end with a blank line.
      if (buffer.trim().length > 0) {
        const parsed = parseSSEBlock(buffer);
        if (parsed) onEvent(parsed);
      }
      onEvent({ event: "end", data: null });
    } catch (err) {
      if ((err as { name?: string }).name === "AbortError") return;
      onError?.(err instanceof Error ? err : new Error(String(err)));
    }
  })();

  return {
    promise,
    cancel: () => controller.abort(),
  };
}

/**
 * Parse a single SSE message block (`event:` / `data:` lines, separated by
 * a blank line). Returns `null` for empty / comment-only blocks.
 */
function parseSSEBlock(block: string): StreamEvent<unknown> | null {
  let event = "message";
  let data = "";
  let hasData = false;
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const idx = line.indexOf(":");
    if (idx === -1) continue;
    const field = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trimStart();
    if (field === "event") {
      event = value;
    } else if (field === "data") {
      hasData = true;
      data += (data ? "\n" : "") + value;
    }
  }
  if (!hasData && event === "message") return null;

  let parsed: unknown = data;
  if (
    typeof data === "string" &&
    data.length > 0 &&
    (data.startsWith("{") ||
      data.startsWith("[") ||
      data.startsWith('"'))
  ) {
    try {
      parsed = JSON.parse(data);
    } catch {
      /* fall through with raw string */
    }
  }
  return { event, data: parsed };
}