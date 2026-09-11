/**
 * HTTP client for evaluation APIs.
 */
import type {
  EvalDataset,
  EvalRunDetail,
  EvalRunRequest,
  EvalRunSummary,
} from "@/types/eval";

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

/** List available JSONL evaluation datasets. */
export async function fetchEvalDatasets(): Promise<EvalDataset[]> {
  const res = await fetch(`${API_BASE}/eval/datasets`);
  return parseJson<EvalDataset[]>(res);
}

/** Start an evaluation run for a dataset. */
export async function startEvalRun(
  body: EvalRunRequest,
): Promise<EvalRunSummary> {
  const res = await fetch(`${API_BASE}/eval/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseJson<EvalRunSummary>(res);
}

/** List all evaluation runs. */
export async function fetchEvalRuns(): Promise<EvalRunSummary[]> {
  const res = await fetch(`${API_BASE}/eval/runs`);
  return parseJson<EvalRunSummary[]>(res);
}

/** Get a single evaluation run with case results. */
export async function fetchEvalRun(runId: string): Promise<EvalRunDetail> {
  const res = await fetch(
    `${API_BASE}/eval/runs/${encodeURIComponent(runId)}`,
  );
  return parseJson<EvalRunDetail>(res);
}
