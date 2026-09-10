/**
 * useReviewStore — manages the review-agent side panel state.
 *
 * The review agent runs in a separate thread on the backend. This hook
 * streams its events into a dedicated UI without polluting the main chat.
 */
import { useCallback, useRef, useState } from "react";

import { sendReviewStream } from "@/api/chat";
import type { ChatStreamHandle } from "@/api/chat";
import type {
  ReviewComparisonPayload,
  ReviewRequestBody,
  ToolCall,
} from "@/types/chat";

export interface ReviewState {
  visible: boolean;
  loading: boolean;
  mainReport: string;
  reviewReport: string;
  reviewThreadId: string;
  toolCalls: ToolCall[];
  comparison: ReviewComparisonPayload | null;
  error: string | null;
}

export interface ReviewStore extends ReviewState {
  openReview: () => void;
  closeReview: () => void;
  resetReview: () => void;
  runReview: (threadId: string) => void;
  stopReview: () => void;
}

const INITIAL_STATE: ReviewState = {
  visible: false,
  loading: false,
  mainReport: "",
  reviewReport: "",
  reviewThreadId: "",
  toolCalls: [],
  comparison: null,
  error: null,
};

export function useReviewStore(): ReviewStore {
  const [state, setState] = useState<ReviewState>(INITIAL_STATE);
  const cancelRef = useRef<ChatStreamHandle["cancel"] | null>(null);
  const bufferRef = useRef<string>("");
  const toolCallsRef = useRef<Map<string, ToolCall>>(new Map());

  const resetReview = useCallback(() => {
    if (cancelRef.current) {
      cancelRef.current();
      cancelRef.current = null;
    }
    bufferRef.current = "";
    toolCallsRef.current = new Map();
    setState(INITIAL_STATE);
  }, []);

  const openReview = useCallback(() => {
    setState((s) => ({ ...s, visible: true }));
  }, []);

  const closeReview = useCallback(() => {
    setState((s) => ({ ...s, visible: false }));
  }, []);

  const stopReview = useCallback(() => {
    if (cancelRef.current) {
      cancelRef.current();
      cancelRef.current = null;
    }
    setState((s) => ({ ...s, loading: false }));
  }, []);

  const runReview = useCallback((threadId: string) => {
    if (cancelRef.current) {
      cancelRef.current();
    }
    bufferRef.current = "";
    toolCallsRef.current = new Map();

    setState((s) => ({
      ...INITIAL_STATE,
      visible: true,
      loading: true,
      mainReport: s.mainReport,
    }));

    const body: ReviewRequestBody = { thread_id: threadId, strategy: "reexecute" };
    let started = false;

    const handle = sendReviewStream(
      body,
      (ev) => {
        const event = ev.event;
        const data = ev.data as Record<string, unknown>;

        if (event === "review_started") {
          started = true;
          const payload = data as { review_thread_id?: string };
          const reviewThreadId = payload?.review_thread_id ?? "";
          setState((s) => ({ ...s, reviewThreadId }));
          return;
        }

        if (event === "review_token") {
          const token = typeof data === "string" ? data : String(data ?? "");
          bufferRef.current += token;
          setState((s) => ({ ...s, reviewReport: bufferRef.current }));
          return;
        }

        if (event === "review_tool_start") {
          const payload = data as { id?: string; name: string; input?: Record<string, unknown> };
          if (!payload || typeof payload !== "object") return;
          const key = payload.id || payload.name;
          toolCallsRef.current.set(key, {
            id: payload.id,
            name: payload.name,
            input: payload.input,
          });
          setState((s) => ({ ...s, toolCalls: Array.from(toolCallsRef.current.values()) }));
          return;
        }

        if (event === "review_tool_end") {
          const payload = data as { id?: string; name?: string; output: string };
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
          setState((s) => ({ ...s, toolCalls: Array.from(toolCallsRef.current.values()) }));
          return;
        }

        if (event === "review_message") {
          const msg = data as { content?: string };
          const content = msg?.content ?? "";
          bufferRef.current = content;
          setState((s) => ({ ...s, reviewReport: content }));
          return;
        }

        if (event === "review_comparison") {
          setState((s) => ({
            ...s,
            comparison: data as unknown as ReviewComparisonPayload,
          }));
          return;
        }

        if (event === "review_error") {
          const detail = typeof data === "string" ? data : JSON.stringify(data);
          setState((s) => ({ ...s, loading: false, error: detail }));
          return;
        }

        if (event === "review_end" || event === "end") {
          if (started) {
            setState((s) => ({ ...s, loading: false }));
          }
        }
      },
      (err) => {
        setState((s) => ({ ...s, loading: false, error: err.message }));
      },
    );

    cancelRef.current = handle.cancel;
  }, []);

  return {
    ...state,
    openReview,
    closeReview,
    resetReview,
    runReview,
    stopReview,
  };
}
