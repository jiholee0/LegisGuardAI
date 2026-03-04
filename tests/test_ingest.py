from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import xml.etree.ElementTree as ET

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Article, Law
from app.services.law.law_ingest import LawIngestService
from app.services.law.law_parser import LawXmlParser


LAW_XML = """
<root>
  <법령명한글>산업안전보건법</법령명한글>
  <공포번호>12345</공포번호>
  <공포일자>20240101</공포일자>
  <시행일자>20240201</시행일자>
  <조문>
    <조문번호>제23조</조문번호>
    <조문제목>안전조치</조문제목>
    <조문내용>사업주는 매년 1회 이상 점검하여야 한다.</조문내용>
    <항><항내용>매년 1회 이상 점검</항내용></항>
  </조문>
  <조문>
    <조문번호>제23조의2</조문번호>
    <조문내용>사업주는 기록을 보관하여야 한다.</조문내용>
  </조문>
</root>
"""


class FakeMolegApiClient:
    def search_law(self, law_name: str):
        return type(
            "Summary",
            (),
            {"law_code": "LAW001", "law_name": law_name, "law_type": "LAW"},
        )()

    def fetch_law_detail(self, law_code: str):
        return ET.fromstring(LAW_XML)


def build_session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", future=True)
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


def test_ingest_upserts_laws_and_articles(tmp_path: Path):
    service = LawIngestService(
        client=FakeMolegApiClient(),
        parser=LawXmlParser(),
        session_factory=build_session_factory(tmp_path),
    )

    result = service.ingest(["산업안전보건법"])

    assert result.status == "SUCCESS"
    assert result.laws_upserted == 1
    assert result.articles_upserted == 2


def test_ingest_is_idempotent_for_article_key(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    service = LawIngestService(client=FakeMolegApiClient(), parser=LawXmlParser(), session_factory=session_factory)

    service.ingest(["산업안전보건법"])
    service.ingest(["산업안전보건법"])

    with session_factory() as session:
        assert session.query(Law).count() == 1
        assert session.query(Article).count() == 2


def test_parser_supports_article_numbers_with_suffix():
    parsed = LawXmlParser().parse_law(ET.fromstring(LAW_XML), "산업안전보건법", "LAW001", "LAW")
    article_nos = [article["article_no"] for article in parsed.articles]
    assert "제23조의2" in article_nos
