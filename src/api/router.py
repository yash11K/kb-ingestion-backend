"""Top-level API router aggregation."""

from fastapi import APIRouter

from src.api.files import router as files_router
from src.api.ingest import router as ingest_router
from src.api.queue import router as queue_router
from src.api.revalidate import router as revalidate_router
from src.api.sources import router as sources_router
from src.api.stats import router as stats_router
from src.api.stream import router as stream_router
from src.api.query import router as query_router
from src.api.nav import router as nav_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(ingest_router)
api_router.include_router(sources_router)
api_router.include_router(queue_router)
api_router.include_router(files_router)
api_router.include_router(stats_router)
api_router.include_router(revalidate_router)
api_router.include_router(stream_router)
api_router.include_router(query_router)
api_router.include_router(nav_router)
