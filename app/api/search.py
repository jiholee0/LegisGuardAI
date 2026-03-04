from fastapi import APIRouter, Depends

from app.schemas.search import NoticeDiffResponse, NoticeSearchRequest, NoticeSearchResponse
from app.services.agents.change_analyst import ChangeAnalystService
from app.services.agents.tools.llm_change_analysis import LlmChangeAnalysisTool
from app.services.law.notice_search import NoticeSearchService

router = APIRouter(prefix="/search", tags=["search"])


def get_notice_search_service() -> NoticeSearchService:
    return NoticeSearchService()


def get_change_analyst_service() -> ChangeAnalystService:
    return ChangeAnalystService()


def get_change_analyst_llm_service() -> ChangeAnalystService:
    return ChangeAnalystService(llm_change_tool=LlmChangeAnalysisTool())


@router.post("/notice", response_model=NoticeSearchResponse)
def search_notice(
    payload: NoticeSearchRequest,
    service: NoticeSearchService = Depends(get_notice_search_service),
) -> NoticeSearchResponse:
    return service.search_notice(payload)


@router.post("/notice/diff", response_model=NoticeDiffResponse)
def diff_notice(
    payload: NoticeSearchRequest,
    service: ChangeAnalystService = Depends(get_change_analyst_service),
) -> NoticeDiffResponse:
    return service.analyze_notice(payload)


@router.post("/notice/diff/llm", response_model=NoticeDiffResponse)
def diff_notice_with_llm(
    payload: NoticeSearchRequest,
    service: ChangeAnalystService = Depends(get_change_analyst_llm_service),
) -> NoticeDiffResponse:
    return service.analyze_notice(payload)
