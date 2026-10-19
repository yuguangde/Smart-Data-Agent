/**
 * useEvalStore — small global helper for evaluation state.
 *
 * Most evaluation data is fetched directly by the routed page components.
 * This store keeps lightweight cross-cutting state such as whether any run
 * is currently active.
 */
import { useCallback, useState } from "react";

import {
  fetchEvalDatasets,
  fetchEvalRuns,
  startEvalRun,
} from "@/api/eval";
import type {
  EvalDataset,
  EvalRunSummary,
} from "@/types/eval";

export interface EvalStore {
  visible: boolean;
  loading: boolean;
  running: boolean;
  datasets: EvalDataset[];
  runs: EvalRunSummary[];
  error: string | null;
  openPanel: () => void;
  closePanel: () => void;
  loadDatasets: () => Promise<void>;
  loadRuns: () => Promise<void>;
  startRun: (dataset: string) => Promise<EvalRunSummary>;
}

const INITIAL_STATE = {
  visible: false,
  loading: false,
  running: false,
  datasets: [] as EvalDataset[],
  runs: [] as EvalRunSummary[],
  error: null as string | null,
};

export function useEvalStore(): EvalStore {
  const [state, setState] = useState(INITIAL_STATE);

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
      setState((s) => ({ ...s, runs, running }));
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setState((s) => ({ ...s, error: detail }));
    }
  }, []);

  const startRun = useCallback(async (dataset: string): Promise<EvalRunSummary> => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const run = await startEvalRun({ dataset });
      setState((s) => ({ ...s, loading: false, running: true }));
      return run;
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setState((s) => ({ ...s, loading: false, error: detail }));
      throw err;
    }
  }, []);

  return {
    ...state,
    openPanel,
    closePanel,
    loadDatasets,
    loadRuns,
    startRun,
  };
}
