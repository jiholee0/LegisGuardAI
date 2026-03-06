from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Article, Base, Law
from app.db.session import get_session
from app.main import app


def build_session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'law_api.db'}", future=True)
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


def seed_law_and_articles(session_factory):
    with session_factory() as session:
        law = Law(
            law_code="LAW001",
            law_name="산업안전보건법 시행규칙",
            law_type="ENFORCEMENT_RULE",
            source="MOLEG_OPEN_API",
            effective_date=date(2024, 2, 1),
        )
        session.add(law)
        session.flush()

        session.add_all(
            [
                Article(
                    law_id=law.id,
                    article_key="산업안전보건법 시행규칙:제1조",
                    article_no="제1조",
                    article_title="목적",
                    article_text="이 규칙은 ...",
                    normalized_text="이 규칙은 ...",
                    article_order=1,
                    paragraph_json=None,
                    effective_date=date(2024, 2, 1),
                    hash="hash1",
                ),
                Article(
                    law_id=law.id,
                    article_key="산업안전보건법 시행규칙:별표 1:000100E",
                    article_no="별표 1",
                    article_title="별표 제목",
                    article_text="별표 내용",
                    normalized_text="별표 내용",
                    article_order=2,
                    paragraph_json=None,
                    effective_date=date(2024, 2, 1),
                    hash="hash2",
                ),
                Article(
                    law_id=law.id,
                    article_key="산업안전보건법 시행규칙:제1조의2",
                    article_no="제1조의2",
                    article_title="가지 조문",
                    article_text="가지 조문 내용",
                    normalized_text="가지 조문 내용",
                    article_order=3,
                    paragraph_json=None,
                    effective_date=date(2024, 2, 1),
                    hash="hash2_1",
                ),
                Article(
                    law_id=law.id,
                    article_key="산업안전보건법 시행규칙:별지 1:000100F",
                    article_no="별지 1",
                    article_title="별지 제목",
                    article_text="별지 내용",
                    normalized_text="별지 내용",
                    article_order=4,
                    paragraph_json=None,
                    effective_date=date(2024, 2, 1),
                    hash="hash3",
                ),
            ]
        )
        session.commit()


def test_law_db_summary_excludes_appendix_and_form_from_counts(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_law_and_articles(session_factory)

    def override_get_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/law/db")
    assert response.status_code == 200
    payload = response.json()

    assert payload["total_laws"] == 1
    assert payload["total_articles"] == 1
    assert payload["laws"][0]["article_count"] == 1

    app.dependency_overrides.clear()


def test_law_articles_count_excludes_appendix_and_form(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_law_and_articles(session_factory)

    def override_get_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/law/1/articles")
    assert response.status_code == 200
    payload = response.json()

    assert payload["article_count"] == 1
    assert len(payload["articles"]) == 4

    app.dependency_overrides.clear()
