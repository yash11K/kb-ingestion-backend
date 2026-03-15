"""FastAPI application factory and lifespan events."""

import logging
from contextlib import asynccontextmanager

import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.agents.extractor import ExtractorAgent
from src.agents.validator import ValidatorAgent
from src.api.router import api_router
from src.config import get_settings
from src.db.connection import close_pool, create_pool
from src.services.pipeline import PipelineService
from src.services.revalidation import RevalidationService
from src.services.s3_upload import S3UploadService
from src.services.kb_query import KBQueryService
from src.services.stream_manager import StreamManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown resources."""
    # Startup
    settings = get_settings()
    pool = await create_pool(settings.database_url)
    s3_client = boto3.client("s3", region_name=settings.aws_region)

    s3_service = S3UploadService(s3_client, settings.s3_bucket_name)
    stream_manager = StreamManager()
    extractor = ExtractorAgent(settings)
    validator = ValidatorAgent(settings, pool)
    pipeline_service = PipelineService(
        extractor, validator, pool, s3_service, settings, stream_manager
    )
    revalidation_service = RevalidationService(validator, pool, s3_service, settings)
    kb_query_service = KBQueryService(pool, settings)

    app.state.db_pool = pool
    app.state.s3_service = s3_service
    app.state.stream_manager = stream_manager
    app.state.pipeline_service = pipeline_service
    app.state.revalidation_service = revalidation_service
    app.state.kb_query_service = kb_query_service
    app.state.settings = settings

    yield

    # Shutdown
    await close_pool(pool)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    app = FastAPI(
        title="AEM Knowledge Base Ingestion System",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    return app
