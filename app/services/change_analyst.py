from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Callable

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.schemas.search import NoticeDiffResponse, NoticeSearchRequest
from app.services.change_analyst_tools import ArticleDiffTool, LawArticleMatchTool
from app.services.llm_change_analysis import LlmChangeAnalysisTool
from app.services.notice_parser import NoticeParserService


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
        parsed_notice = self.parser.parse(payload)
        tool_audit = ["parse_notice_structure"]

        try:
            collection = self.article_match_tool.get_collection()
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Vector index is not initialized.") from exc

        if collection.count() == 0:
            raise HTTPException(status_code=409, detail="Vector index is empty. Run /admin/reindex first.")

        article_diffs = []
        for candidate in parsed_notice.article_candidates:
            matched_article, match_score, match_method = self.article_match_tool.match(
                collection=collection,
                law_name=parsed_notice.law_name,
                candidate=candidate,
            )
            tool_audit.append("lawdb_get_article" if match_method == "exact_article_no" else "vector_search_law")

            article_diff = self.article_diff_tool.build_base_diff(
                candidate=candidate,
                matched_article=matched_article,
                match_score=match_score,
                match_method=match_method,
            )
            tool_audit.append("diff_generate_article")

            if self.llm_change_tool and article_diff.current_text and article_diff.after_text:
                article_diff = self.llm_change_tool.analyze(
                    current_text=article_diff.current_text,
                    source_text=article_diff.source_text,
                    base_diff=article_diff,
                )
                tool_audit.append("classify_change_labels")

            article_diffs.append(article_diff)

        return NoticeDiffResponse(
            parsed_notice=parsed_notice,
            article_diffs=article_diffs,
            tool_audit=tool_audit,
        )
