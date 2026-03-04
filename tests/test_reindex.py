from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from pathlib import Path

import chromadb
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.models import Article, Base, Law
from app.services.embedding_index import EmbeddingIndexService


def build_session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reindex.db'}", future=True)
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


def test_reindex_creates_chroma_entries(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    chroma_dir = tmp_path / "chroma"
    settings.chroma_persist_dir = str(chroma_dir)
    settings.chroma_collection_name = "test_articles"

    service = EmbeddingIndexService(session_factory=session_factory)
    result = service.reindex(recreate=True)

    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_collection("test_articles")

    assert result.status == "SUCCESS"
    assert result.chunks_indexed == 1
    assert collection.count() == 1
