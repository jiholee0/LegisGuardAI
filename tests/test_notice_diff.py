from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.models import Article, Base, Law
from app.schemas.search import NoticeBodyJson, NoticeSearchRequest
from app.services.change_analyst import ChangeAnalystService
from app.services.embedding_index import EmbeddingIndexService
from app.services.llm_change_analysis import LlmChangeAnalysisTool
from app.services.notice_parser import NoticeParserService


def build_session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'notice_diff.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    @contextmanager
    def factory():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    return factory


def seed_articles(session_factory):
    with session_factory() as session:
        law = Law(
            law_code="LAW001",
            law_name="산업안전보건법",
            law_type="LAW",
            source="MOLEG_OPEN_API",
            effective_date=date(2024, 2, 1),
        )
        session.add(law)
        session.flush()
        session.add(
            Article(
                law_id=law.id,
                article_key="산업안전보건법:제23조",
                article_no="제23조",
                article_title="안전조치",
                article_text="사업주는 매년 1회 이상 점검하여야 한다.",
                normalized_text="사업주는 매년 1회 이상 점검하여야 한다.",
                article_order=1,
                paragraph_json=None,
                effective_date=date(2024, 2, 1),
                hash="hash1",
            )
        )
        session.commit()


def test_notice_parser_extracts_change_metadata():
    parser = NoticeParserService()

    parsed = parser.parse(
        NoticeSearchRequest(
            input_type="json",
            title="산업안전보건법 일부개정안",
            body_json=NoticeBodyJson(
                law_name="산업안전보건법",
                content='제23조 중 "매년 1회 이상"을 "6개월마다 1회 이상"으로 한다.',
            ),
        )
    )

    assert parsed.law_name == "산업안전보건법"
    assert parsed.change_type == "일부개정"
    assert parsed.article_candidates[0].article_no == "제23조"
    assert parsed.article_candidates[0].source_text == '제23조 중 "매년 1회 이상"을 "6개월마다 1회 이상"으로 한다.'


def test_notice_diff_returns_exact_match_and_numeric_change(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_diff")
    settings.chroma_collection_name = "notice_diff_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = ChangeAnalystService(session_factory=session_factory)

    response = service.analyze_notice(
        NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 일부개정안",
            body='제23조 중 "매년 1회 이상 점검하여야 한다."을 "6개월마다 1회 이상 점검하여야 한다."으로 한다.',
        )
    )

    assert response.parsed_notice.change_type == "일부개정"
    assert len(response.article_diffs) == 1

    article_diff = response.article_diffs[0]
    assert article_diff.match_method == "exact_article_no"
    assert article_diff.analysis_method == "rule_based"
    assert article_diff.matched_article_key == "산업안전보건법:제23조"
    assert article_diff.before_text == "사업주는 매년 1회 이상 점검하여야 한다."
    assert article_diff.after_text == "6개월마다 1회 이상 점검하여야 한다."
    assert article_diff.numeric_changes[0].before == "1회"
    assert article_diff.numeric_changes[0].after == "6개월"
    assert any(segment.op == "insert" and "6개월" in segment.text for segment in article_diff.diff_segments)


class FakeLlmClient:
    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        return {
            "before_text": "사업주는 매년 1회 이상 점검하여야 한다.",
            "after_text": "사업주는 6개월마다 1회 이상 점검하여야 한다.",
            "diff_summary": "점검 주기를 매년에서 반기마다로 변경",
            "labels": ["빈도변경", "조문개정"],
            "highlights": [
                {"type": "replace", "before": "매년 1회 이상", "after": "6개월마다 1회 이상"}
            ],
            "numeric_changes": [
                {"field": "period", "before": "1년", "after": "6개월"}
            ],
            "diff_segments": [
                {"op": "delete", "text": "매년 1회 이상"},
                {"op": "insert", "text": "6개월마다 1회 이상"},
                {"op": "equal", "text": "점검하여야 한다 ."},
            ],
        }


def test_notice_diff_can_use_llm_change_tool(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_llm_diff")
    settings.chroma_collection_name = "notice_llm_diff_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = ChangeAnalystService(
        session_factory=session_factory,
        llm_change_tool=LlmChangeAnalysisTool(llm_client=FakeLlmClient()),
    )

    response = service.analyze_notice(
        NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 일부개정안",
            body='제23조 중 "매년 1회 이상 점검하여야 한다."을 "6개월마다 1회 이상 점검하여야 한다."으로 한다.',
        )
    )

    article_diff = response.article_diffs[0]
    assert article_diff.analysis_method == "llm"
    assert article_diff.before_text == "사업주는 매년 1회 이상 점검하여야 한다."
    assert article_diff.after_text == "사업주는 6개월마다 1회 이상 점검하여야 한다."
    assert article_diff.diff_summary == "점검 주기를 매년에서 반기마다로 변경"
    assert article_diff.labels == ["빈도변경", "조문개정"]
    assert article_diff.highlights[0].type == "replace"
    assert article_diff.numeric_changes[0].field == "period"
    assert article_diff.numeric_changes[0].before == "1년"
    assert article_diff.numeric_changes[0].after == "6개월"
