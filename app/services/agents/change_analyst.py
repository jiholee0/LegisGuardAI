from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from typing import Callable

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.schemas.search import NoticeArticleDiff, NoticeDiffResponse, NoticeParseResult
from app.services.agents.tool_registry import ToolRegistry, ToolSpec
from app.services.agents.tools.change_analyst_tools import ArticleDiffTool, LawArticleMatchTool
from app.services.agents.tools.llm_change_analysis import LlmChangeAnalysisTool

logger = logging.getLogger(__name__)


class ChangeAnalystService:
    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager] = SessionLocal,
        article_match_tool: LawArticleMatchTool | None = None,
        article_diff_tool: ArticleDiffTool | None = None,
        llm_change_tool: LlmChangeAnalysisTool | None = None,
    ) -> None:
        self.article_match_tool = article_match_tool or LawArticleMatchTool(session_factory=session_factory)
        self.article_diff_tool = article_diff_tool or ArticleDiffTool()
        self.llm_change_tool = llm_change_tool or self._build_default_llm_tool()

    def _build_default_llm_tool(self) -> LlmChangeAnalysisTool | None:
        try:
            return LlmChangeAnalysisTool()
        except Exception:
            logger.exception(
                "Failed to initialize default LLM change tool; falling back to rule-based mode",
                extra={"agent": "change_analyst", "function": f"{self.__class__.__name__}.__init__"},
            )
            return None

    def analyze_parsed_notice(self, parsed_notice: NoticeParseResult) -> NoticeDiffResponse:
        analysis_mode = parsed_notice.analysis_mode
        logger.info(
            "Change analyst input: mode=%s, law_name=%s, change_types=%s, candidates=%s",
            analysis_mode,
            parsed_notice.law_name or "미상",
            parsed_notice.change_types,
            len(parsed_notice.article_candidates),
            extra={
                "agent": "change_analyst",
                "function": f"{self.__class__.__name__}.analyze_parsed_notice",
            },
        )
        logger.info(
            "Change analyst step start: ChangeAnalystService.analyze_parsed_notice (mode=%s)",
            analysis_mode,
            extra={
                "agent": "change_analyst",
                "function": f"{self.__class__.__name__}.analyze_parsed_notice",
                "analysis_mode": analysis_mode,
                "law_name": parsed_notice.law_name,
                "change_types": parsed_notice.change_types,
                "candidate_count": len(parsed_notice.article_candidates),
            },
        )
        tool_registry = self._build_tool_registry()

        try:
            collection = self.article_match_tool.get_collection()
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Vector index is not initialized.") from exc

        if collection.count() == 0:
            raise HTTPException(status_code=409, detail="Vector index is empty. Run /admin/reindex first.")

        article_diffs = []
        llm_candidates: list[dict] = []
        for index, candidate in enumerate(parsed_notice.article_candidates, start=1):
            logger.info(
                "Change analyst candidate step: ChangeAnalystService.analyze_parsed_notice[%s/%s]",
                index,
                len(parsed_notice.article_candidates),
                extra={
                    "agent": "change_analyst",
                    "function": f"{self.__class__.__name__}.analyze_parsed_notice",
                    "candidate_index": index,
                    "candidate_total": len(parsed_notice.article_candidates),
                    "article_no": candidate.article_no,
                },
            )
            candidate_mode = candidate.analysis_mode or ("STRUCTURE" if parsed_notice.analysis_mode == "STRUCTURE" else "DIFF")
            candidate_change_type = candidate.change_type or "미상"

            if candidate_mode == "STRUCTURE":
                summary_input = self._summarize_match_input(
                    {
                        "law_name": parsed_notice.law_name,
                        "candidate": candidate,
                    }
                )
                tool_registry.record_skip("lawdb_get_article", input_summary=summary_input, output_summary="skipped in STRUCTURE mode")
                tool_registry.record_skip("vector_search_law", input_summary=summary_input, output_summary="skipped in STRUCTURE mode")
                tool_registry.record_skip(
                    "validate_target_locator",
                    input_summary=f"article_no={candidate.article_no or '미상'}, locator=없음",
                    output_summary="skipped in STRUCTURE mode",
                )
                tool_registry.record_skip(
                    "diff_generate_article",
                    input_summary=f"article_no={candidate.article_no or '미상'}, match_method=unmatched",
                    output_summary="skipped in STRUCTURE mode",
                )
                article_diff = NoticeArticleDiff(
                    article_no=candidate.article_no,
                    target_locator=None,
                    target_exists=None,
                    fact_status="confirmed",
                    validation_message=None,
                    matched_law_name=None,
                    matched_article_no=None,
                    matched_article_key=None,
                    current_text=None,
                    before_text=None,
                    after_text=None,
                    diff_summary=None,
                    labels=[],
                    match_score=None,
                    match_method="unmatched",
                    analysis_method="rule_based",
                    diff_segments=[],
                    highlights=[],
                    numeric_changes=[],
                    source_text=candidate.source_text,
                )
            else:
                matched_article, match_score = tool_registry.execute(
                    "lawdb_get_article",
                    law_name=parsed_notice.law_name,
                    candidate=candidate,
                )
                match_method = "exact_article_no"
                if matched_article is None:
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
                    change_type=candidate_change_type,
                    target_locator=target_locator,
                    target_exists=target_exists,
                    validation_message=validation_message,
                )
            article_diffs.append(article_diff)
            llm_candidates.append(
                {
                    "article_no": candidate.article_no,
                    "article_ref_text": candidate.article_ref_text,
                    "change_type": candidate_change_type,
                    "analysis_mode": candidate_mode,
                    "source_text": candidate.source_text,
                }
            )
            if article_diff.fact_status == "invalid_target":
                tool_registry.record_skip(
                    "classify_change_labels",
                    input_summary=self._summarize_batch_llm_input(
                        {
                            "candidate_count": 1,
                            "eligible_count": 0,
                            "analysis_mode": candidate_mode,
                            "source_doc_type": parsed_notice.doc_type,
                        }
                    ),
                    output_summary="invalid target locator",
                )

        llm_eligible = [
            idx
            for idx, item in enumerate(article_diffs)
            if item.fact_status != "invalid_target" and (item.current_text or llm_candidates[idx]["analysis_mode"] == "STRUCTURE")
        ]
        if self.llm_change_tool and llm_eligible:
            try:
                article_diffs = tool_registry.execute(
                    "classify_change_labels",
                    source_doc_type=parsed_notice.doc_type,
                    candidates=llm_candidates,
                    base_diffs=article_diffs,
                )
            except Exception as exc:
                logger.warning(
                "LLM classify_change_labels_batch failed; fallback to rule-based result: %s",
                    " ".join(str(exc).split()).strip() or exc.__class__.__name__,
                    extra={
                        "agent": "change_analyst",
                        "function": f"{self.__class__.__name__}.analyze_parsed_notice",
                        "analysis_mode": analysis_mode,
                    },
                )
        else:
            skip_reason = "llm tool not configured" if self.llm_change_tool is None else "insufficient context for llm classify"
            tool_registry.record_skip(
                "classify_change_labels",
                input_summary=self._summarize_batch_llm_input(
                    {
                        "candidate_count": len(article_diffs),
                        "eligible_count": len(llm_eligible),
                        "analysis_mode": analysis_mode,
                        "source_doc_type": parsed_notice.doc_type,
                    }
                ),
                output_summary=skip_reason,
            )

        logger.info(
            "Change analyst step complete: ChangeAnalystService.analyze_parsed_notice",
            extra={
                "agent": "change_analyst",
                "function": f"{self.__class__.__name__}.analyze_parsed_notice",
                "analysis_mode": analysis_mode,
                "article_diff_count": len(article_diffs),
                "audit_count": len(tool_registry.audit),
            },
        )
        response = NoticeDiffResponse(
            agent="change_analyst",
            analysis_mode=analysis_mode,
            parsed_notice=parsed_notice,
            article_diffs=article_diffs,
            tool_audit=tool_registry.audit,
        )
        confirmed_count = sum(1 for item in response.article_diffs if item.fact_status == "confirmed")
        invalid_count = sum(1 for item in response.article_diffs if item.fact_status == "invalid_target")
        unmatched_count = sum(1 for item in response.article_diffs if item.fact_status == "unmatched")
        llm_count = sum(1 for item in response.article_diffs if item.analysis_method == "llm")
        logger.info(
            "Change analyst output: article_diffs=%s, confirmed=%s, invalid=%s, unmatched=%s, llm_applied=%s, tool_audit=%s",
            len(response.article_diffs),
            confirmed_count,
            invalid_count,
            unmatched_count,
            llm_count,
            len(response.tool_audit),
            extra={
                "agent": "change_analyst",
                "function": f"{self.__class__.__name__}.analyze_parsed_notice",
            },
        )
        return response

    def _build_tool_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
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
                    handler=self.llm_change_tool.analyze_batch,
                    summarize_input=self._summarize_batch_llm_input,
                    summarize_output=lambda result: f"batch_results={len(result)}",
                )
            )
        return registry

    def _summarize_match_input(self, kwargs: dict) -> str:
        candidate = kwargs["candidate"]
        law_name = kwargs.get("law_name") or "미상"
        return f"law_name={law_name}, article_no={candidate.article_no or '미상'}"

    def _summarize_match_output(self, result, *, method: str) -> str:
        article, score = result
        if article is None:
            return f"method={method}, matched=no"
        return f"method={method}, matched=yes, article_key={article.article_key}, score={score}"

    def _summarize_batch_llm_input(self, kwargs: dict) -> str:
        candidates = kwargs.get("candidates")
        base_diffs = kwargs.get("base_diffs")
        candidate_count = kwargs.get("candidate_count", len(candidates) if isinstance(candidates, list) else 0)
        eligible_count = kwargs.get("eligible_count", len(base_diffs) if isinstance(base_diffs, list) else 0)
        analysis_mode = kwargs.get("analysis_mode") or "mixed"
        source_doc_type = kwargs.get("source_doc_type") or "unknown"
        return (
            f"candidate_count={candidate_count}, "
            f"eligible_count={eligible_count}, "
            f"analysis_mode={analysis_mode}, "
            f"source_doc_type={source_doc_type}"
        )

    def _summarize_validation_output(self, result) -> str:
        target_locator, target_exists, validation_message = result
        if target_locator is None:
            return "locator=없음, target_exists=unknown"
        if target_exists is True:
            return f"locator={target_locator}, target_exists=yes"
        if target_exists is False:
            return f"locator={target_locator}, target_exists=no, message={validation_message}"
        return f"locator={target_locator}, target_exists=unknown"
