"""Stats endpoint — aggregate counts and average score."""

from fastapi import APIRouter, Request

from src.db.queries import get_stats
from src.models.schemas import StatsResponse

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=StatsResponse)
async def stats(request: Request) -> StatsResponse:
    """Return aggregate file statistics."""
    async with request.app.state.session_factory() as session:
        data = await get_stats(session)
        await session.commit()
    return StatsResponse(
        total_files=data["total_files"],
        pending_review=data["pending_review"],
        approved=data["approved"],
        rejected=data["rejected"],
        avg_score=round(float(data["avg_score"]), 2),
    )
