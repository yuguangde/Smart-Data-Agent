"""HTTP routes for the review agent."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    ReviewComparison,
    ReviewRequest,
    ReviewResponse,
    ReviewSummary,
)
from app.config import get_settings
from app.memory.review_store import get_review_store
from app.services.review_service import run_review, stream_review

logger = logging.getLogger(__name__)

router = APIRouter(tags=["review"])


def _check_review_enabled() -> None:
    settings = get_settings()
    if not settings.review_enabled:
        raise HTTPException(status_code=503, detail="Review agent is disabled")


@router.post("/review", response_model=ReviewResponse)
async def review_report(req: ReviewRequest) -> ReviewResponse:
    """Run the review agent synchronously and return both reports + comparison."""
    _check_review_enabled()
    try:
        result = await run_review(req.thread_id, strategy=req.strategy)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Review failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    comparison = result.get("comparison") or {}
    return ReviewResponse(
        primary_thread_id=result["primary_thread_id"],
        review_thread_id=result["review_thread_id"],
        user_question=result["user_question"],
        main_report=result["main_report"],
        review_report=result["review_report"],
        comparison=ReviewComparison(
            verdict=comparison.get("verdict", "partial"),
            summary=comparison.get("summary", ""),
            differences=comparison.get("differences", []),
        ),
    )


@router.post("/review/stream")
async def review_stream(req: ReviewRequest) -> StreamingResponse:
    """Stream review-agent events and the final comparison as SSE."""
    _check_review_enabled()

    async def event_generator():
        try:
            async for event in stream_review(req.thread_id, strategy=req.strategy):
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:
            logger.exception("Review stream endpoint failed: %s", exc)
            yield f"event: review_error\ndata: {__import__('json').dumps({'detail': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@router.get("/threads/{thread_id}/reviews", response_model=list[ReviewSummary])
async def list_reviews(thread_id: str) -> list[ReviewSummary]:
    """Return previously stored reviews for a thread."""
    store = get_review_store()
    if store is None:
        return []
    rows = await store.list_reviews(thread_id)
    return [
        ReviewSummary(
            review_id=r["review_id"],
            thread_id=r["thread_id"],
            strategy=r["strategy"],
            review_model=r["review_model"],
            verdict=r["verdict"],
            summary=r["summary"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


__all__ = ["router"]
