/**
 * useEvalStore — manages the evaluation side panel state.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchEvalDatasets,
  fetchEvalRun,
  fetchEvalRuns,
  startEvalRun,
} from "@/api/eval";
import type {
  EvalDataset,
  EvalRunDetail,
  EvalRunSummary,
} from "@/types/eval";

export interface EvalState {
  visible: boolean;
  loading: boolean;
  running: boolean;
  datasets: EvalDataset[];
  runs: EvalRunSummary[];
  selectedRunId: string | null;
  runDetail: EvalRunDetail | null;
  error: string | null;
}

export interface EvalStore extends EvalState {
  openPanel: () => void;
  closePanel: () => void;
  loadDatasets: () => Promise<void>;
  loadRuns: () => Promise<void>;
  startRun: (dataset: string) => Promise<void>;
  selectRun: (runId: string) => Promise<void>;
}

const INITIAL_STATE: EvalState = {
  visible: false,
  loading: false,
  running: false,
  datasets: [],
  runs: [],
  selectedRunId: null,
  runDetail: null,
  error: null,
};

export function useEvalStore(): EvalStore {
  const [state, setState] = useState<EvalState>(INITIAL_STATE);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const openPanel = useCallback(() => {
    setState((s) => ({ ...s, visible: true }));
  }, []);

  const closePanel = useCallback(() => {
    setState((s) => ({ ...s, visible: false }));
  }, []);

  const loadDatasets = useCallback(async () => {
    try {
      const datasets = await fetchEvalDatasets();
      setState((s) => ({ ...s, datasets }));
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setState((s) => ({ ...s, error: detail }));
    }
  }, []);

  const loadRuns = useCallback(async () => {
    try {
      const runs = await fetchEvalRuns();
      const running = runs.some((r) => r.status === "running");
      setState((s) => ({
        ...s,
        runs,
        running: s.running || running,
      }));
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setState((s) => ({ ...s, error: detail }));
    }
  }, []);

  const selectRun = useCallback(async (runId: string) => {
    setState((s) => ({ ...s, selectedRunId: runId }));
    try {
      const detail = await fetchEvalRun(runId);
      setState((s) => ({ ...s, runDetail: detail }));
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setState((s) => ({ ...s, error: detail }));
    }
  }, []);

  const startRun = useCallback(
    async (dataset: string) => {
      setState((s) => ({ ...s, loading: true, error: null }));
      try {
        await startEvalRun({ dataset });
        await loadRuns();
        setState((s) => ({ ...s, running: true }));
      } catch (err) {
        const detail = err instanceof Error ? err.message : String(err);
        setState((s) => ({ ...s, error: detail }));
      } finally {
        setState((s) => ({ ...s, loading: false }));
      }
    },
    [loadRuns],
  );

  // Initial data load when panel opens.
  useEffect(() => {
    if (!state.visible) return;
    void loadDatasets();
    void loadRuns();
    if (state.selectedRunId) {
      void selectRun(state.selectedRunId);
    }
  }, [state.visible]);

  // Poll while any run is active.
  useEffect(() => {
    if (!state.running || intervalRef.current) return;

    intervalRef.current = setInterval(() => {
      void loadRuns();
      if (state.selectedRunId) {
        void selectRun(state.selectedRunId);
      }
    }, 2000);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [state.running, state.selectedRunId, loadRuns, selectRun]);

  // Stop polling when no runs are running.
  useEffect(() => {
    if (!state.running && intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, [state.running]);

  return {
    ...state,
    openPanel,
    closePanel,
    loadDatasets,
    loadRuns,
    startRun,
    selectRun,
  };
}
