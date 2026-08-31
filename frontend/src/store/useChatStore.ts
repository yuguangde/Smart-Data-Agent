/**
 * useChatStore — minimal client-side chat state for the Smart Data Agent.
 *
 * Tied to the FastAPI backend (`/chat`, `/chat/stream`, `/threads/{id}`).
 *
 * Design notes:
 *   - One `useReducer` for canonical state (messages, conversation list).
 *   - Three refs (`assistantIdRef`, `streamBufferRef`, `toolCallsRef`)
 *     store streaming-only buffers so the reducer isn't dispatched once
 *     per token (which would re-render the whole list per token).
 *   - LocalStorage mirrors the conversation list and the last opened
 *     thread id, so reloads reopen the same conversation.
 */
import { useCallback, useEffect, useReducer, useRef } from "react";

import {
  createThread,
  fetchHistory,
  sendChatStream,
} from "@/api/chat";
import type { ChatStreamHandle } from "@/api/chat";
import type {
  ChatMessage,
  Conversation,
  StreamEvent,
  ToolCall,
} from "@/types/chat";

const CONV_STORAGE_KEY = "smart-data-agent:conversations-v1";
const THREAD_STORAGE_KEY = "smart-data-agent:active-thread-v1";

// ---------------------------------------------------------------------------
// State + reducer

export interface ChatState {
  threadId: string | null;
  messages: ChatMessage[];
  conversations: Conversation[];
  loading: boolean;
  pendingHistory: boolean;
  error: string | null;
}

type Action =
  | { type: "SET_THREAD"; threadId: string | null }
  | { type: "SET_LOADING"; loading: boolean }
  | { type: "SET_PENDING_HISTORY"; pending: boolean }
  | { type: "SET_ERROR"; error: string | null }
  | { type: "RESET_MESSAGES" }
  | { type: "SET_MESSAGES"; messages: ChatMessage[] }
  | { type: "APPEND_MESSAGE"; message: ChatMessage }
  | { type: "PATCH_MESSAGE"; id: string; patch: Partial<ChatMessage> }
  | { type: "UPSERT_CONVERSATION"; c: Conversation }
  | { type: "REMOVE_CONVERSATION"; id: string };

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(CONV_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? (parsed as Conversation[]) : [];
  } catch {
    return [];
  }
}

function reducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case "SET_THREAD":
      return { ...state, threadId: action.threadId };
    case "SET_LOADING":
      return { ...state, loading: action.loading };
    case "SET_PENDING_HISTORY":
      return { ...state, pendingHistory: action.pending };
    case "SET_ERROR":
      return { ...state, error: action.error };
    case "RESET_MESSAGES":
      return { ...state, messages: [] };
    case "SET_MESSAGES":
      return { ...state, messages: action.messages };
    case "APPEND_MESSAGE":
      return { ...state, messages: [...state.messages, action.message] };
    case "PATCH_MESSAGE":
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.id === action.id ? { ...m, ...action.patch } : m,
        ),
      };
    case "UPSERT_CONVERSATION": {
      const rest = state.conversations.filter((c) => c.id !== action.c.id);
      return { ...state, conversations: [action.c, ...rest] };
    }
    case "REMOVE_CONVERSATION": {
      const left = state.conversations.filter((c) => c.id !== action.id);
      const cleared = state.threadId === action.id;
      return cleared
        ? { ...state, conversations: left, threadId: null, messages: [] }
        : { ...state, conversations: left };
    }
    default:
      return state;
  }
}

function truncate(s: string, n: number): string {
  if (!s) return "";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

// ---------------------------------------------------------------------------
// Hook

export interface ChatStore extends ChatState {
  sendMessage: (content: string) => Promise<void>;
  newConversation: () => Promise<string>;
  selectConversation: (threadId: string) => Promise<void>;
  removeConversation: (threadId: string) => void;
  stop: () => void;
}

export function useChatStore(): ChatStore {
  const [state, dispatch] = useReducer(reducer, {
    threadId: null,
    messages: [],
    conversations: loadConversations(),
    loading: false,
    pendingHistory: false,
    error: null,
  });

  // Streaming-only refs (live outside the reducer; mutated mid-token).
  const assistantIdRef = useRef<string | null>(null);
  const streamBufferRef = useRef<string>("");
  const toolCallsRef = useRef<Map<string, ToolCall>>(new Map());
  const cancelRef = useRef<ChatStreamHandle["cancel"] | null>(null);

  // Mirror active thread id into a ref so callbacks don't close over stale state.
  const threadIdRef = useRef<string | null>(state.threadId);
  useEffect(() => {
    threadIdRef.current = state.threadId;
  }, [state.threadId]);

  // Persist conversation list.
  useEffect(() => {
    try {
      localStorage.setItem(
        CONV_STORAGE_KEY,
        JSON.stringify(state.conversations),
      );
    } catch {
      /* ignore quota errors */
    }
  }, [state.conversations]);

  // Persist active thread id.
  useEffect(() => {
    if (state.threadId) {
      localStorage.setItem(THREAD_STORAGE_KEY, state.threadId);
    } else {
      localStorage.removeItem(THREAD_STORAGE_KEY);
    }
  }, [state.threadId]);

  // ----- helpers ------------------------------------------------------------

  const cancelInFlight = useCallback(() => {
    if (cancelRef.current) {
      cancelRef.current();
      cancelRef.current = null;
    }
  }, []);

  const resetBuffers = useCallback(() => {
    streamBufferRef.current = "";
    toolCallsRef.current = new Map();
    assistantIdRef.current = null;
  }, []);

  const snapshotToolCalls = useCallback((): ToolCall[] => {
    return Array.from(toolCallsRef.current.values());
  }, []);

  const upsertConversation = useCallback(
    (threadId: string, fallbackTitle: string, preview?: string) => {
      dispatch({
        type: "UPSERT_CONVERSATION",
        c: {
          id: threadId,
          title: truncate(fallbackTitle, 28) || "新会话",
          preview: preview ? truncate(preview, 80) : undefined,
          updatedAt: Date.now(),
        },
      });
    },
    [],
  );

  // ----- public actions -----------------------------------------------------

  const newConversation = useCallback(async () => {
    cancelInFlight();
    resetBuffers();
    dispatch({ type: "RESET_MESSAGES" });
    dispatch({ type: "SET_LOADING", loading: false });
    dispatch({ type: "SET_ERROR", error: null });

    try {
      const threadId = await createThread();
      dispatch({ type: "SET_THREAD", threadId });
      threadIdRef.current = threadId;
      upsertConversation(threadId, "新会话");
      return threadId;
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "failed to create thread";
      dispatch({ type: "SET_ERROR", error: msg });
      throw err;
    }
  }, [cancelInFlight, resetBuffers, upsertConversation]);

  const selectConversation = useCallback(
    async (threadId: string) => {
      cancelInFlight();
      resetBuffers();
      dispatch({ type: "SET_THREAD", threadId });
      threadIdRef.current = threadId;
      dispatch({ type: "RESET_MESSAGES" });
      dispatch({ type: "SET_PENDING_HISTORY", pending: true });

      try {
        const data = await fetchHistory(threadId);
        const msgs: ChatMessage[] = (data.messages || []).map(
          (m, i): ChatMessage => ({
            id: `${threadId}-h-${i}-${m.role}`,
            role: m.role,
            content: m.content || "",
            toolCalls:
              (m.tool_calls as ToolCall[] | undefined) || undefined,
            createdAt: Date.now() - (data.messages.length - i),
          }),
        );
        dispatch({ type: "SET_MESSAGES", messages: msgs });
      } catch (err) {
        // Backend may have lost the thread (in-memory checkpointer).
        console.warn("history fetch failed", err);
      } finally {
        dispatch({ type: "SET_PENDING_HISTORY", pending: false });
      }
    },
    [cancelInFlight, resetBuffers],
  );

  const removeConversation = useCallback(
    (threadId: string) => {
      cancelInFlight();
      resetBuffers();
      dispatch({ type: "REMOVE_CONVERSATION", id: threadId });
      if (threadIdRef.current === threadId) {
        threadIdRef.current = null;
      }
    },
    [cancelInFlight, resetBuffers],
  );

  const stop = useCallback(() => {
    cancelInFlight();
    dispatch({ type: "SET_LOADING", loading: false });
    if (assistantIdRef.current) {
      dispatch({
        type: "PATCH_MESSAGE",
        id: assistantIdRef.current,
        patch: { streaming: false },
      });
    }
  }, [cancelInFlight]);

  /**
   * Send `content` as a new user turn. Lazily creates a thread_id on the
   * backend if the conversation is brand-new. Streams the assistant reply
   * token-by-token; resolves once the `done` / `end` frame is received.
   */
  const sendMessage = useCallback(
    async (content: string) => {
      const text = content.trim();
      if (!text) return;

      // 1. Ensure there is a thread_id (create lazily).
      let threadId = threadIdRef.current;
      if (!threadId) {
        try {
          threadId = await createThread();
          dispatch({ type: "SET_THREAD", threadId });
          threadIdRef.current = threadId;
        } catch (err) {
          dispatch({
            type: "SET_ERROR",
            error:
              err instanceof Error
                ? err.message
                : "failed to create thread",
          });
          return;
        }
      }

      // 2. Spawn user + assistant bubbles.
      const stamp = Date.now();
      const userMsg: ChatMessage = {
        id: `${threadId}-u-${stamp}`,
        role: "user",
        content: text,
        createdAt: stamp,
      };
      const assistantMsg: ChatMessage = {
        id: `${threadId}-a-${stamp}`,
        role: "assistant",
        content: "",
        toolCalls: [],
        streaming: true,
        createdAt: stamp,
      };
      assistantIdRef.current = assistantMsg.id;
      streamBufferRef.current = "";
      toolCallsRef.current = new Map();

      dispatch({ type: "APPEND_MESSAGE", message: userMsg });
      dispatch({ type: "APPEND_MESSAGE", message: assistantMsg });
      upsertConversation(threadId, text);
      dispatch({ type: "SET_LOADING", loading: true });
      dispatch({ type: "SET_ERROR", error: null });

      const assistantId = assistantMsg.id;

      // 3. Open the stream and route each SSE frame.
      const handle = sendChatStream(
        { thread_id: threadId, message: text, user_id: "anonymous" },
        (ev: StreamEvent<unknown>) => {
          const name = String(ev.event);
          const data = ev.data as Record<string, unknown> | string | null;

          switch (name) {
            case "thread":
            case "message": {
              if (data && typeof data === "object") {
                const obj = data as Record<string, unknown>;
                if (
                  typeof obj.thread_id === "string" &&
                  !threadIdRef.current
                ) {
                  threadIdRef.current = obj.thread_id;
                  dispatch({
                    type: "SET_THREAD",
                    threadId: obj.thread_id,
                  });
                }
                if (typeof obj.content === "string") {
                  streamBufferRef.current = obj.content;
                  dispatch({
                    type: "PATCH_MESSAGE",
                    id: assistantId,
                    patch: { content: obj.content },
                  });
                }
              }
              break;
            }
            case "token": {
              const piece =
                typeof data === "string" ? data : String(data ?? "");
              if (!piece) break;
              streamBufferRef.current += piece;
              dispatch({
                type: "PATCH_MESSAGE",
                id: assistantId,
                patch: { content: streamBufferRef.current },
              });
              break;
            }
            case "tool_start": {
              if (!data || typeof data !== "object") break;
              const payload = data as {
                id?: string;
                name: string;
                input?: Record<string, unknown>;
                args?: Record<string, unknown>;
              };
              const key = payload.id || payload.name;
              toolCallsRef.current.set(key, {
                id: payload.id,
                name: payload.name,
                input: payload.input ?? payload.args,
              });
              dispatch({
                type: "PATCH_MESSAGE",
                id: assistantId,
                patch: { toolCalls: snapshotToolCalls() },
              });
              break;
            }
            case "tool_end": {
              if (!data || typeof data !== "object") break;
              const payload = data as {
                id?: string;
                name?: string;
                output: string;
              };
              const key =
                payload.id ||
                payload.name ||
                Array.from(toolCallsRef.current.keys()).pop() ||
                "tool";
              const existing = toolCallsRef.current.get(key);
              if (existing) {
                existing.output = payload.output;
                toolCallsRef.current.set(key, existing);
              } else {
                toolCallsRef.current.set(key, {
                  id: payload.id,
                  name: payload.name || key,
                  output: payload.output,
                });
              }
              dispatch({
                type: "PATCH_MESSAGE",
                id: assistantId,
                patch: { toolCalls: snapshotToolCalls() },
              });
              break;
            }
            case "done":
            case "end": {
              dispatch({ type: "SET_LOADING", loading: false });
              dispatch({
                type: "PATCH_MESSAGE",
                id: assistantId,
                patch: { streaming: false },
              });
              upsertConversation(
                threadId,
                text,
                streamBufferRef.current || undefined,
              );
              break;
            }
            case "error": {
              const msg =
                typeof data === "string"
                  ? data
                  : (data as { message?: string })?.message ||
                    "streaming error";
              const appended = streamBufferRef.current
                ? `${streamBufferRef.current}\n\n⚠️ ${msg}`
                : `⚠️ ${msg}`;
              dispatch({ type: "SET_LOADING", loading: false });
              dispatch({
                type: "PATCH_MESSAGE",
                id: assistantId,
                patch: { streaming: false, content: appended },
              });
              dispatch({ type: "SET_ERROR", error: msg });
              break;
            }
            default:
              break;
          }
        },
        (err) => {
          const appended = streamBufferRef.current
            ? `${streamBufferRef.current}\n\n⚠️ ${
                err.message || "stream failed"
              }`
            : `⚠️ ${err.message || "stream failed"}`;
          dispatch({ type: "SET_LOADING", loading: false });
          dispatch({
            type: "PATCH_MESSAGE",
            id: assistantId,
            patch: { streaming: false, content: appended },
          });
          dispatch({
            type: "SET_ERROR",
            error: err.message || "stream failed",
          });
        },
      );

      cancelRef.current = handle.cancel;
      try {
        await handle.promise;
      } finally {
        cancelRef.current = null;
      }
    },
    [upsertConversation, snapshotToolCalls],
  );

  // Try to reopen the last thread on first mount (best-effort).
  useEffect(() => {
    const last = localStorage.getItem(THREAD_STORAGE_KEY);
    if (!last) return;
    selectConversation(last);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    ...state,
    sendMessage,
    newConversation,
    selectConversation,
    removeConversation,
    stop,
  };
}