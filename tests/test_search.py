from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.models import Article, Base, Law
from app.schemas.search import NoticeBodyJson, NoticeSearchRequest
from app.services.embedding_index import EmbeddingIndexService
from app.services.notice_search import NoticeSearchService


def build_session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'search.db'}", future=True)
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
        session.add_all(
            [
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
                ),
                Article(
                    law_id=law.id,
                    article_key="산업안전보건법:제15조",
                    article_no="제15조",
                    article_title="안전보건관리책임자",
                    article_text="사업장은 안전보건관리책임자를 두어야 한다.",
                    normalized_text="사업장은 안전보건관리책임자를 두어야 한다.",
                    article_order=2,
                    paragraph_json=None,
                    effective_date=date(2024, 2, 1),
                    hash="hash2",
                ),
            ]
        )
        session.commit()


def test_search_returns_matching_articles(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma")
    settings.chroma_collection_name = "search_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = NoticeSearchService(session_factory=session_factory)

    response = service.search_notice(
        NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 일부개정안",
            body="매년 1회 이상 점검을 6개월마다 1회 이상 점검으로 변경한다.",
            top_k=3,
        )
    )

    assert response.query_units
    assert response.query_units[0].matches
    assert response.query_units[0].matches[0].article_key == "산업안전보건법:제23조"


def test_search_prefers_law_name_from_json_payload(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_json")
    settings.chroma_collection_name = "json_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = NoticeSearchService(session_factory=session_factory)

    response = service.search_notice(
        NoticeSearchRequest(
            input_type="json",
            title="개정안",
            body_json=NoticeBodyJson(law_name="산업안전보건법", content="안전보건관리책임자"),
            top_k=3,
        )
    )

    assert response.query_units[0].matches[0].law_name == "산업안전보건법"
