from fastapi import APIRouter, Depends

from app.schemas.search import NoticeSearchRequest, NoticeSearchResponse
from app.services.notice_search import NoticeSearchService

router = APIRouter(prefix="/search", tags=["search"])


def get_notice_search_service() -> NoticeSearchService:
    return NoticeSearchService()


@router.post("/notice", response_model=NoticeSearchResponse)
def search_notice(
    payload: NoticeSearchRequest,
    service: NoticeSearchService = Depends(get_notice_search_service),
) -> NoticeSearchResponse:
    return service.search_notice(payload)
