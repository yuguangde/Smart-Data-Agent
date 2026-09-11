"""HTTP routes for evaluation runs and datasets."""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.schemas import (
    EvalDataset,
    EvalDatasetDetail,
    EvalRunDetail,
    EvalRunRequest,
    EvalRunSummary,
)
from app.config import get_settings
from app.memory.eval_store import get_eval_store
from app.services.eval_service import load_jsonl, resolve_dataset, run_eval

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evaluation"])


def _check_eval_enabled() -> None:
    settings = get_settings()
    if not settings.eval_enabled:
        raise HTTPException(status_code=503, detail="Evaluation API is disabled")


def _check_store():
    store = get_eval_store()
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Evaluation store is not available (checkpointer must be sqlite)",
        )
    return store


@router.get("/eval/datasets", response_model=list[EvalDataset])
async def list_datasets() -> list[EvalDataset]:
    """List JSONL evaluation datasets available on the server."""
    _check_eval_enabled()
    settings = get_settings()
    datasets_dir = settings.eval_datasets_dir_resolved

    if not datasets_dir.exists():
        return []

    results: list[EvalDataset] = []
    for path in sorted(datasets_dir.glob("*.jsonl")):
        if path.is_file():
            results.append(
                EvalDataset(
                    name=path.name,
                    path=str(path),
                    size_bytes=path.stat().st_size,
                )
            )
    return results


@router.get("/eval/datasets/{dataset_name}", response_model=EvalDatasetDetail)
async def get_dataset(dataset_name: str) -> EvalDatasetDetail:
    """Return detailed information for a single JSONL dataset."""
    _check_eval_enabled()

    try:
        dataset_path = resolve_dataset(dataset_name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        cases = load_jsonl(dataset_path)
    except Exception as exc:
        logger.exception("Failed to load dataset %s: %s", dataset_name, exc)
        raise HTTPException(
            status_code=400, detail=f"Failed to load dataset: {exc}"
        ) from exc

    return EvalDatasetDetail(
        name=dataset_name,
        path=str(dataset_path),
        size_bytes=dataset_path.stat().st_size,
        total=len(cases),
        cases=[
            {
                "index": idx,
                "question": case.get("question", ""),
                "expected_tool": case.get("expected_tool"),
                "expected_args": case.get("expected_args", {}),
                "expected_in_answer": case.get("expected_in_answer", []),
                "tags": case.get("tags", []),
            }
            for idx, case in enumerate(cases, start=1)
        ],
    )


@router.post("/eval/runs", response_model=EvalRunSummary, status_code=202)
async def start_run(req: EvalRunRequest) -> EvalRunSummary:
    """Start an evaluation run for the requested dataset.

    The run is executed in a background asyncio task and its progress is
    persisted. The returned summary reflects the initial state.
    """
    _check_eval_enabled()
    store = _check_store()

    try:
        dataset_path = resolve_dataset(req.dataset)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Peek at the dataset to get total case count up front.
    try:
        from app.services.eval_service import load_jsonl

        cases = load_jsonl(dataset_path)
    except Exception as exc:
        logger.exception("Failed to load dataset: %s", exc)
        raise HTTPException(status_code=400, detail=f"Failed to load dataset: {exc}") from exc

    run_id = uuid.uuid4().hex
    await store.create_run(
        run_id=run_id,
        dataset=req.dataset,
        dataset_path=str(dataset_path),
        total=len(cases),
    )

    asyncio.create_task(run_eval(run_id, dataset_path, store))

    run = await store.get_run(run_id)
    if run is None:  # pragma: no cover - should never happen
        raise HTTPException(status_code=500, detail="Run was not created")
    return EvalRunSummary(**run)


@router.get("/eval/runs", response_model=list[EvalRunSummary])
async def list_runs(dataset: str | None = None) -> list[EvalRunSummary]:
    """Return evaluation runs, newest first.

    Optionally filter by dataset name via the ``dataset`` query parameter.
    """
    _check_eval_enabled()
    store = _check_store()
    rows = await store.list_runs(dataset=dataset)
    return [EvalRunSummary(**r) for r in rows]


@router.get("/eval/runs/{run_id}", response_model=EvalRunDetail)
async def get_run(run_id: str) -> EvalRunDetail:
    """Return a single evaluation run including all case results."""
    _check_eval_enabled()
    store = _check_store()
    run = await store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    results = await store.get_run_results(run_id)
    return EvalRunDetail(**run, results=results)


__all__ = ["router"]
