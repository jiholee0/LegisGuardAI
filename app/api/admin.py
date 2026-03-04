from fastapi import APIRouter, Body, Depends

from app.core.config import get_settings
from app.schemas.admin import IngestLawsRequest, IngestLawsResponse, ReindexRequest, ReindexResponse
from app.services.law.embedding_index import EmbeddingIndexService
from app.services.law.law_ingest import LawIngestService

router = APIRouter(prefix="/admin", tags=["admin"])


def get_law_ingest_service() -> LawIngestService:
    return LawIngestService()


def get_embedding_index_service() -> EmbeddingIndexService:
    return EmbeddingIndexService()


@router.post("/ingest/laws", response_model=IngestLawsResponse)
def ingest_laws(
    payload: IngestLawsRequest | None = Body(default=None),
    service: LawIngestService = Depends(get_law_ingest_service),
) -> IngestLawsResponse:
    settings = get_settings()
    targets = payload.targets if payload and payload.targets else settings.configured_targets
    return service.ingest(targets)


@router.post("/reindex", response_model=ReindexResponse)
def reindex(
    payload: ReindexRequest,
    service: EmbeddingIndexService = Depends(get_embedding_index_service),
) -> ReindexResponse:
    return service.reindex(recreate=payload.recreate)
