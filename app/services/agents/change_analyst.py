from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Callable

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.schemas.search import NoticeDiffResponse, NoticeSearchRequest
from app.services.agents.tool_registry import ToolRegistry, ToolSpec
from app.services.agents.tools.change_analyst_tools import ArticleDiffTool, LawArticleMatchTool
from app.services.agents.tools.llm_change_analysis import LlmChangeAnalysisTool
from app.services.agents.tools.notice_parser import NoticeParserService


class ChangeAnalystService:
    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager] = SessionLocal,
        parser: NoticeParserService | None = None,
        article_match_tool: LawArticleMatchTool | None = None,
        article_diff_tool: ArticleDiffTool | None = None,
        llm_change_tool: LlmChangeAnalysisTool | None = None,
    ) -> None:
        self.parser = parser or NoticeParserService()
        self.article_match_tool = article_match_tool or LawArticleMatchTool(session_factory=session_factory)
        self.article_diff_tool = article_diff_tool or ArticleDiffTool()
        self.llm_change_tool = llm_change_tool

    def analyze_notice(self, payload: NoticeSearchRequest) -> NoticeDiffResponse:
        tool_registry = self._build_tool_registry()
        parsed_notice = tool_registry.execute("parse_notice_structure", payload=payload)

        try:
            collection = self.article_match_tool.get_collection()
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Vector index is not initialized.") from exc

        if collection.count() == 0:
            raise HTTPException(status_code=409, detail="Vector index is empty. Run /admin/reindex first.")

        article_diffs = []
        for candidate in parsed_notice.article_candidates:
            matched_article, match_score = tool_registry.execute(
                "lawdb_get_article",
                law_name=parsed_notice.law_name,
                candidate=candidate,
            )
            match_method = "exact_article_no"
            if matched_article is None:
                if parsed_notice.change_type == "제정" and candidate.article_no:
                    tool_registry.record_skip(
                        "vector_search_law",
                        input_summary=self._summarize_match_input(
                            {
                                "law_name": parsed_notice.law_name,
                                "candidate": candidate,
                            }
                        ),
                        output_summary="new article insertion skips fallback matching",
                    )
                    match_method = "unmatched"
                else:
                    matched_article, match_score = tool_registry.execute(
                        "vector_search_law",
                        collection=collection,
                        law_name=parsed_notice.law_name,
                        candidate=candidate,
                    )
                    match_method = "vector_search" if matched_article is not None else "unmatched"
            else:
                tool_registry.record_skip(
                    "vector_search_law",
                    input_summary=self._summarize_match_input(
                        {
                            "law_name": parsed_notice.law_name,
                            "candidate": candidate,
                        }
                    ),
                    output_summary="exact match already found",
                )

            target_locator, target_exists, validation_message = tool_registry.execute(
                "validate_target_locator",
                candidate=candidate,
                matched_article=matched_article,
            )

            article_diff = tool_registry.execute(
                "diff_generate_article",
                candidate=candidate,
                matched_article=matched_article,
                match_score=match_score,
                match_method=match_method,
                change_type=parsed_notice.change_type,
                target_locator=target_locator,
                target_exists=target_exists,
                validation_message=validation_message,
            )

            if article_diff.fact_status == "invalid_target":
                tool_registry.record_skip(
                    "classify_change_labels",
                    input_summary=self._summarize_llm_input(
                        {
                            "current_text": article_diff.current_text,
                            "source_text": article_diff.source_text,
                        }
                    ),
                    output_summary="invalid target locator",
                )
            elif self.llm_change_tool and (
                article_diff.current_text or parsed_notice.change_type == "제정"
            ):
                try:
                    article_diff = tool_registry.execute(
                        "classify_change_labels",
                        current_text=article_diff.current_text or "",
                        source_text=article_diff.source_text,
                        base_diff=article_diff,
                    )
                except Exception:
                    # Keep the confirmed fact result even if LLM enrichment times out or fails.
                    pass
            else:
                tool_registry.record_skip(
                    "classify_change_labels",
                    input_summary=self._summarize_llm_input(
                        {
                            "current_text": article_diff.current_text,
                            "source_text": article_diff.source_text,
                        }
                    ),
                    output_summary="llm tool not configured or insufficient context",
                )

            article_diffs.append(article_diff)

        return NoticeDiffResponse(
            parsed_notice=parsed_notice,
            article_diffs=article_diffs,
            tool_audit=tool_registry.audit,
        )

    def _build_tool_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="parse_notice_structure",
                handler=self.parser.parse,
                summarize_input=lambda kwargs: self._summarize_parse_input(kwargs["payload"]),
                summarize_output=lambda result: f"law_name={result.law_name or '미상'}, candidates={len(result.article_candidates)}",
            )
        )
        registry.register(
            ToolSpec(
                name="lawdb_get_article",
                handler=self.article_match_tool.lookup_exact,
                summarize_input=self._summarize_match_input,
                summarize_output=lambda result: self._summarize_match_output(result, method="exact"),
            )
        )
        registry.register(
            ToolSpec(
                name="vector_search_law",
                handler=self.article_match_tool.search_vector,
                summarize_input=self._summarize_match_input,
                summarize_output=lambda result: self._summarize_match_output(result, method="vector"),
            )
        )
        registry.register(
            ToolSpec(
                name="validate_target_locator",
                handler=self.article_diff_tool.validate_target,
                summarize_input=lambda kwargs: f"article_no={kwargs['candidate'].article_no or '미상'}, locator={self.article_diff_tool._extract_target_locator(kwargs['candidate']) or '없음'}",
                summarize_output=lambda result: self._summarize_validation_output(result),
            )
        )
        registry.register(
            ToolSpec(
                name="diff_generate_article",
                handler=self.article_diff_tool.build_base_diff,
                summarize_input=lambda kwargs: f"article_no={kwargs['candidate'].article_no or '미상'}, match_method={kwargs['match_method']}",
                summarize_output=lambda result: f"fact_status={result.fact_status}, before={'yes' if result.before_text else 'no'}, after={'yes' if result.after_text else 'no'}",
            )
        )
        if self.llm_change_tool is not None:
            registry.register(
                ToolSpec(
                    name="classify_change_labels",
                    handler=self.llm_change_tool.analyze,
                    summarize_input=self._summarize_llm_input,
                    summarize_output=lambda result: f"labels={len(result.labels)}, highlights={len(result.highlights)}",
                )
            )
        return registry

    def _summarize_parse_input(self, payload: NoticeSearchRequest) -> str:
        return f"input_type={payload.input_type}, title={payload.title or '미상'}"

    def _summarize_match_input(self, kwargs: dict) -> str:
        candidate = kwargs["candidate"]
        law_name = kwargs.get("law_name") or "미상"
        return f"law_name={law_name}, article_no={candidate.article_no or '미상'}"

    def _summarize_match_output(self, result, *, method: str) -> str:
        article, score = result
        if article is None:
            return f"method={method}, matched=no"
        return f"method={method}, matched=yes, article_key={article.article_key}, score={score}"

    def _summarize_llm_input(self, kwargs: dict) -> str:
        current_text = kwargs.get("current_text")
        source_text = kwargs.get("source_text")
        return f"current_text={'yes' if current_text else 'no'}, source_text={'yes' if source_text else 'no'}"

    def _summarize_validation_output(self, result) -> str:
        target_locator, target_exists, validation_message = result
        if target_locator is None:
            return "locator=없음, target_exists=unknown"
        if target_exists is True:
            return f"locator={target_locator}, target_exists=yes"
        if target_exists is False:
            return f"locator={target_locator}, target_exists=no, message={validation_message}"
        return f"locator={target_locator}, target_exists=unknown"
