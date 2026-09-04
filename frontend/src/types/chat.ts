/**
 * Type definitions for the Smart Data Agent chat UI.
 *
 * These mirror the wire format produced by the FastAPI backend
 * (`POST /chat`, `POST /chat/stream`, `GET /threads/{id}`),
 * which in turn wraps the LangGraph agent.
 */

/** Roles understood by the LangChain checkpointer. */
export type ChatRole = "user" | "assistant" | "system" | "tool" | "ai" | "human";

/** A single message rendered in the conversation list. */
export interface ChatMessage {
  /** Stable client-side id, e.g. `${threadId}-${index}-${role}`. */
  id: string;
  role: ChatRole;
  /** Plain-text content. May stream in token-by-token. */
  content: string;
  /** Tool invocations captured for this assistant turn. */
  toolCalls?: ToolCall[];
  /** True while the backend is still streaming this message. */
  streaming?: boolean;
  /** Used only for ordering in memory. */
  createdAt?: number;
}

/**
 * A tool invocation recorded from `tool_start` / `tool_end` SSE events.
 * `input` is captured from `tool_start`; `output` from `tool_end`
 * once the backend finishes invoking the tool.
 */
export interface ToolCall {
  id?: string;
  name: string;
  input?: Record<string, unknown>;
  output?: string;
}

/**
 * Generic SSE frame produced by `POST /chat/stream`.
 * The backend emits `event` names like `thread`, `token`, `tool_start`,
 * `tool_end`, `done`, `error`, `end`. Consumers should narrow on `event`
 * before reading `data`.
 */
export interface StreamEvent<T = unknown> {
  event: string;
  data: T;
}

/** Stored conversation summary used by the sidebar. */
export interface Conversation {
  /** Backend `thread_id`. */
  id: string;
  /** First user message (truncated) or "新会话". */
  title: string;
  /** Last assistant message preview (truncated). */
  preview?: string;
  updatedAt: number;
}

/** Request body for `POST /chat` and `POST /chat/stream`. */
export interface SendMessageBody {
  thread_id?: string | null;
  message?: string;
  user_id?: string;
  metadata?: Record<string, unknown>;
  resume?: Record<string, unknown> | null;
}

/** Response body for `GET /threads/{threadId}`. */
export interface ThreadHistoryResponse {
  thread_id: string;
  messages: Array<{
    role: ChatRole;
    content?: string;
    name?: string | null;
    tool_calls?: unknown[] | null;
    tool_call_id?: string | null;
  }>;
}

/** Response body for `POST /threads`. */
export interface ThreadCreateResponse {
  thread_id: string;
  created_at: string;
}

/** Wire payload for the `thread` SSE frame. */
export interface ThreadEventPayload {
  thread_id: string;
}

/** Wire payload for the `token` SSE frame. */
export type TokenEventPayload = string;

/** Wire payload for the `tool_start` SSE frame. */
export interface ToolStartPayload {
  id?: string;
  name: string;
  input?: Record<string, unknown>;
  /** Backend sometimes ships the args and sometimes only the name; union both. */
  args?: Record<string, unknown>;
}

/** Wire payload for the `tool_end` SSE frame. */
export interface ToolEndPayload {
  id?: string;
  name?: string;
  output: string;
}

/** Wire payload for the `done` SSE frame. */
export interface DonePayload {
  thread_id?: string;
  iterations?: number;
  tool_calls?: unknown[];
}

/** Wire payload for the `error` SSE frame. */
export type ErrorEventPayload = string | { message?: string; detail?: string };