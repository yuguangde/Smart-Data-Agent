/** A JSONL evaluation dataset available on the server. */
export interface EvalDataset {
  name: string;
  path: string;
  size_bytes: number;
}

/** A single golden case inside a JSONL evaluation dataset. */
export interface EvalDatasetCase {
  index: number;
  question: string;
  expected_tool: string | null;
  expected_args: Record<string, unknown>;
  expected_in_answer: string[];
  tags: string[];
}

/** Detailed view of an evaluation dataset, including all its cases. */
export interface EvalDatasetDetail extends EvalDataset {
  total: number;
  cases: EvalDatasetCase[];
}

/** Request body for starting an evaluation run. */
export interface EvalRunRequest {
  dataset: string;
}

/** Per-metric score for a single case. */
export interface EvalMetricScore {
  passed: boolean;
  reason: string;
}

/** A single case result inside an evaluation run. */
export interface EvalCaseResult {
  result_id: string;
  run_id: string;
  case_index: number;
  question: string;
  passed: boolean;
  scores: Record<string, EvalMetricScore>;
  answer: string;
  error: string | null;
  created_at: string;
}

/** Summary of an evaluation run. */
export interface EvalRunSummary {
  run_id: string;
  dataset: string;
  dataset_path: string;
  status: "pending" | "running" | "completed" | "failed";
  total: number;
  processed: number;
  passed: number;
  errored: number;
  metrics: Record<string, number | null>;
  error: string | null;
  created_at: string;
  updated_at: string;
}

/** Full evaluation run with all case results. */
export interface EvalRunDetail extends EvalRunSummary {
  results: EvalCaseResult[];
}
