/**
 * HTTP client for evaluation APIs.
 */
import type {
  EvalDataset,
  EvalDatasetDetail,
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

/** Get detailed information for a single dataset, including its cases. */
export async function fetchEvalDataset(
  datasetName: string,
): Promise<EvalDatasetDetail> {
  const res = await fetch(
    `${API_BASE}/eval/datasets/${encodeURIComponent(datasetName)}`,
  );
  return parseJson<EvalDatasetDetail>(res);
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

/** List evaluation runs, optionally filtered by dataset name. */
export async function fetchEvalRuns(
  dataset?: string,
): Promise<EvalRunSummary[]> {
  const url = new URL(`${API_BASE}/eval/runs`, window.location.origin);
  if (dataset) {
    url.searchParams.set("dataset", dataset);
  }
  const res = await fetch(url.toString());
  return parseJson<EvalRunSummary[]>(res);
}

/** Get a single evaluation run with case results. */
export async function fetchEvalRun(runId: string): Promise<EvalRunDetail> {
  const res = await fetch(
    `${API_BASE}/eval/runs/${encodeURIComponent(runId)}`,
  );
  return parseJson<EvalRunDetail>(res);
}
