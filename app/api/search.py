import logging

from fastapi import APIRouter, Depends

from app.schemas.search import NoticeDiffResponse, NoticeSearchRequest, NoticeSearchResponse
from app.services.agents.change_analyst import ChangeAnalystService
from app.services.agents.orchestrator import NoticeOrchestratorService
from app.services.agents.tools.llm_change_analysis import LlmChangeAnalysisTool
from app.services.agents.tools.llm_notice_parser import LlmNoticeParserTool
from app.services.law.notice_search import NoticeSearchService
from app.services.upload_payload import build_notice_request_from_upload

router = APIRouter(prefix="/search", tags=["search"])
logger = logging.getLogger(__name__)


def get_notice_search_service() -> NoticeSearchService:
    return NoticeSearchService()


def get_change_analyst_service() -> ChangeAnalystService:
    return ChangeAnalystService()


def get_change_analyst_llm_service() -> ChangeAnalystService:
    return ChangeAnalystService(llm_change_tool=LlmChangeAnalysisTool())


def get_orchestrator_service() -> NoticeOrchestratorService:
    return NoticeOrchestratorService()


def get_orchestrator_llm_service() -> NoticeOrchestratorService:
    return NoticeOrchestratorService(
        parser=LlmNoticeParserTool(),
        change_analyst_service=get_change_analyst_llm_service(),
    )


@router.post("/notice", response_model=NoticeSearchResponse, description="법령 공고 검색")
def search_notice(
    payload: NoticeSearchRequest,
    service: NoticeSearchService = Depends(get_notice_search_service),
) -> NoticeSearchResponse:
    return service.search_notice(payload)


@router.post("/notice/diff", response_model=NoticeDiffResponse, description="법령 변경 분석 (규칙 기반)")
def diff_notice(
    payload: NoticeSearchRequest,
    service: NoticeOrchestratorService = Depends(get_orchestrator_service),
) -> NoticeDiffResponse:
    return service.analyze_notice(payload)


@router.post("/notice/diff/llm", response_model=NoticeDiffResponse, description="법령 변경 분석 (LLM 활용)")
def diff_notice_with_llm(
    payload: NoticeSearchRequest,
    service: NoticeOrchestratorService = Depends(get_orchestrator_llm_service),
) -> NoticeDiffResponse:
    return service.analyze_notice(payload)

try:
    import multipart  # type: ignore # noqa: F401
    from fastapi import File, Form, HTTPException, UploadFile

    @router.post("/notice/diff/upload", response_model=NoticeDiffResponse, description="파일 업로드 기반 변경 분석 (txt/json/pdf)")
    async def diff_notice_upload(
        file: UploadFile = File(...),
        title: str | None = Form(default=None),
        input_type: str | None = Form(default=None),
        service: NoticeOrchestratorService = Depends(get_orchestrator_service),
    ) -> NoticeDiffResponse:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        payload = build_notice_request_from_upload(
            filename=file.filename,
            content_type=file.content_type,
            input_type_hint=input_type,
            title=title,
            file_bytes=file_bytes,
        )
        return service.analyze_notice(payload)


    @router.post("/notice/diff/llm/upload", response_model=NoticeDiffResponse, description="파일 업로드 기반 변경 분석 (LLM, txt/json/pdf)")
    async def diff_notice_upload_with_llm(
        file: UploadFile = File(...),
        title: str | None = Form(default=None),
        input_type: str | None = Form(default=None),
        service: NoticeOrchestratorService = Depends(get_orchestrator_llm_service),
    ) -> NoticeDiffResponse:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        payload = build_notice_request_from_upload(
            filename=file.filename,
            content_type=file.content_type,
            input_type_hint=input_type,
            title=title,
            file_bytes=file_bytes,
        )
        return service.analyze_notice(payload)
except Exception:
    logger.warning("Upload routes disabled because python-multipart is not installed.")
