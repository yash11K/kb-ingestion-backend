"""FastAPI application factory and lifespan events."""

import logging
from contextlib import asynccontextmanager

import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.agents.context_agent import ContextAgent
from src.agents.discovery import DiscoveryAgent
from src.agents.extractor import ExtractorAgent
from src.agents.validator import ValidatorAgent
from src.api.router import api_router
from src.config import get_settings
from src.db.session import init_engine, create_session_factory
import src.db.session as session_module
from src.services.context_cache import ContextCache
from src.services.pipeline import PipelineService
from src.services.revalidation import RevalidationService
from src.services.s3_upload import S3UploadService
from src.services.kb_query import KBQueryService
from src.services.stream_manager import StreamManager
from src.tools.file_context import set_session_factory as set_context_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown resources."""
    settings = get_settings()

    # Validate DATABASE_URL format
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Please provide a valid PostgreSQL connection string "
            "in the format: postgresql+asyncpg://user:password@host:port/dbname"
        )
    if not settings.database_url.startswith("postgresql+asyncpg://"):
        raise RuntimeError(
            f"DATABASE_URL must use the 'postgresql+asyncpg://' scheme for SQLAlchemy async. "
            f"Got: {settings.database_url[:30]}..."
        )

    engine = init_engine(settings.database_url)
    sf = create_session_factory(engine)
    session_module.session_factory = sf

    s3_client = boto3.client("s3", region_name=settings.aws_region)

    s3_service = S3UploadService(s3_client, settings.s3_bucket_name)
    stream_manager = StreamManager()
    discovery = DiscoveryAgent(settings)
    extractor = ExtractorAgent(settings)
    validator = ValidatorAgent(settings, sf)
    pipeline_service = PipelineService(
        discovery, extractor, validator, sf, s3_service, settings, stream_manager
    )
    revalidation_service = RevalidationService(validator, sf, s3_service, settings)
    kb_query_service = KBQueryService(sf, settings)
    set_context_session_factory(sf)
    context_agent = ContextAgent(settings)
    context_cache = ContextCache()

    app.state.session_factory = sf
    app.state.s3_service = s3_service
    app.state.stream_manager = stream_manager
    app.state.pipeline_service = pipeline_service
    app.state.revalidation_service = revalidation_service
    app.state.kb_query_service = kb_query_service
    app.state.context_agent = context_agent
    app.state.context_cache = context_cache
    app.state.settings = settings

    yield

    await engine.dispose()


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
