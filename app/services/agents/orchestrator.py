from __future__ import annotations

import logging
from typing import Callable

from app.schemas.search import NoticeDiffResponse, NoticeSearchRequest
from app.services.agents.change_analyst import ChangeAnalystService
from app.services.agents.tool_registry import ToolRegistry, ToolSpec
from app.services.agents.tools.llm_notice_parser import LlmNoticeParserTool
from app.services.agents.tools.pdf_image_converter import PdfImageConverterTool

logger = logging.getLogger(__name__)


class NoticeOrchestratorService:
    def __init__(
        self,
        parser: LlmNoticeParserTool | None = None,
        change_analyst_service: ChangeAnalystService | None = None,
        pdf_image_converter: PdfImageConverterTool | None = None,
    ) -> None:
        self.parser = parser or LlmNoticeParserTool()
        self.change_analyst_service = change_analyst_service or ChangeAnalystService()
        self.pdf_image_converter = pdf_image_converter or PdfImageConverterTool()

    def analyze_notice(self, payload: NoticeSearchRequest) -> NoticeDiffResponse:
        return self.execute_agent_pipeline(payload)

    def execute_agent_pipeline(
        self,
        payload: NoticeSearchRequest,
        *,
        on_agent_status: Callable[[str, str], None] | None = None,
        on_parsed_notice=None,
        on_agent_result=None,
        on_final_result: Callable[[dict], None] | None = None,
    ) -> NoticeDiffResponse:
        logger.info(
            "Orchestrator input: input_type=%s, title=%s, body_len=%s, has_body_json=%s",
            payload.input_type,
            payload.title or "미상",
            len(payload.body or ""),
            bool(payload.body_json),
            extra={
                "agent": "orchestrator",
                "function": f"{self.__class__.__name__}.analyze_notice",
            },
        )
        logger.info(
            "Orchestrator step start: NoticeOrchestratorService.analyze_notice",
            extra={
                "agent": "orchestrator",
                "function": f"{self.__class__.__name__}.analyze_notice",
                "input_type": payload.input_type,
                "title": payload.title,
            },
        )
        if on_agent_status is not None:
            on_agent_status("orchestrator", "running")
        tool_registry = ToolRegistry()
        tool_registry.register(
            ToolSpec(
                name="pdf_image_converter",
                handler=self._convert_pdf_to_images,
                summarize_input=lambda kwargs: self._summarize_pdf_convert_input(kwargs["payload"]),
                summarize_output=lambda result: f"pages={len(result)}",
            )
        )
        tool_registry.register(
            ToolSpec(
                name="parse_notice_structure",
                handler=self.parser.parse,
                summarize_input=lambda kwargs: self._summarize_parse_input(kwargs["payload"]),
                summarize_output=lambda result: f"mode={result.analysis_mode}, law_name={result.law_name or '미상'}, candidates={len(result.article_candidates)}",
            )
        )
        image_data_urls: list[str] | None = None
        if payload.input_type == "pdf" and payload.raw_pdf_base64:
            try:
                image_data_urls = tool_registry.execute("pdf_image_converter", payload=payload)
            except Exception as exc:
                logger.warning(
                    "Orchestrator pdf_image_converter failed; proceeding without image_data_urls: %s",
                    " ".join(str(exc).split()).strip() or exc.__class__.__name__,
                    extra={
                        "agent": "orchestrator",
                        "function": f"{self.__class__.__name__}.execute_agent_pipeline",
                    },
                )
                image_data_urls = None
        else:
            tool_registry.record_skip(
                "pdf_image_converter",
                input_summary=self._summarize_pdf_convert_input(payload=payload),
                output_summary="non-pdf input or missing raw_pdf_base64",
            )

        parsed_notice = tool_registry.execute("parse_notice_structure", payload=payload, image_data_urls=image_data_urls)
        mode = parsed_notice.analysis_mode
        if on_parsed_notice is not None:
            on_parsed_notice(parsed_notice)
        if on_agent_result is not None:
            on_agent_result(
                "orchestrator",
                {
                    "parsed_notice": {
                        "doc_type": parsed_notice.doc_type,
                        "analysis_mode": parsed_notice.analysis_mode,
                        "law_name": parsed_notice.law_name,
                        "change_types": parsed_notice.change_types,
                    },
                    "next_agents": ["change_analyst"],
                },
                tool_registry.audit,
            )
        if on_agent_status is not None:
            on_agent_status("orchestrator", "completed")
        logger.info(
            "Orchestrator step complete: parse_notice_structure -> ChangeAnalystService.analyze_parsed_notice (mode=%s)",
            mode,
            extra={
                "agent": "orchestrator",
                "function": f"{self.__class__.__name__}.analyze_notice",
                "analysis_mode": mode,
                "law_name": parsed_notice.law_name,
                "change_types": parsed_notice.change_types,
                "candidate_count": len(parsed_notice.article_candidates),
            },
        )

        if on_agent_status is not None:
            on_agent_status("change_analyst", "running")
        change_analysis = self.change_analyst_service.analyze_parsed_notice(parsed_notice=parsed_notice)
        if on_agent_result is not None:
            on_agent_result(
                "change_analyst",
                {"article_diffs": [item.model_dump() for item in change_analysis.article_diffs]},
                change_analysis.tool_audit,
            )
        if on_agent_status is not None:
            on_agent_status("change_analyst", "completed")
        logger.info(
            "Orchestrator step complete: NoticeOrchestratorService.analyze_notice",
            extra={
                "agent": "orchestrator",
                "function": f"{self.__class__.__name__}.analyze_notice",
                "analysis_mode": mode,
                "delegate_function": f"{self.change_analyst_service.__class__.__name__}.analyze_parsed_notice",
                "article_diff_count": len(change_analysis.article_diffs),
            },
        )
        response = change_analysis.model_copy(
            update={
                "agent": "orchestrator",
                "analysis_mode": mode,
                "tool_audit": [*tool_registry.audit, *change_analysis.tool_audit],
            }
        )
        if on_final_result is not None:
            on_final_result(
                {
                    "analysis_mode": response.analysis_mode,
                    "law_name": response.parsed_notice.law_name,
                    "change_types": response.parsed_notice.change_types,
                    "total_changes": len(response.article_diffs),
                    "confirmed_changes": sum(1 for item in response.article_diffs if item.fact_status == "confirmed"),
                    "invalid_targets": sum(1 for item in response.article_diffs if item.fact_status == "invalid_target"),
                    "unmatched": sum(1 for item in response.article_diffs if item.fact_status == "unmatched"),
                }
            )
        logger.info(
            "Orchestrator output: mode=%s, law_name=%s, change_types=%s, candidates=%s, article_diffs=%s, tool_audit=%s",
            response.analysis_mode,
            response.parsed_notice.law_name or "미상",
            response.parsed_notice.change_types,
            len(response.parsed_notice.article_candidates),
            len(response.article_diffs),
            len(response.tool_audit),
            extra={
                "agent": "orchestrator",
                "function": f"{self.__class__.__name__}.analyze_notice",
            },
        )
        return response

    def _summarize_parse_input(self, payload: NoticeSearchRequest) -> str:
        return f"input_type={payload.input_type}, title={payload.title or '미상'}"

    def _summarize_pdf_convert_input(self, payload: NoticeSearchRequest) -> str:
        return f"input_type={payload.input_type}, raw_pdf_base64={'yes' if payload.raw_pdf_base64 else 'no'}"

    def _convert_pdf_to_images(self, *, payload: NoticeSearchRequest) -> list[str]:
        if not payload.raw_pdf_base64:
            return []
        return self.pdf_image_converter.convert(pdf_base64=payload.raw_pdf_base64)
