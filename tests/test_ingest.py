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
  <별표>
    <별표단위 별표키="000100E">
      <별표번호>0001</별표번호>
      <별표가지번호>00</별표가지번호>
      <별표구분>별표</별표구분>
      <별표제목>법의 일부를 적용하지 않는 사업 또는 사업장</별표제목>
      <별표내용><![CDATA[별표 본문 1줄
별표 본문 2줄]]></별표내용>
    </별표단위>
    <별표단위 별표키="000100F">
      <별표번호>0001</별표번호>
      <별표가지번호>00</별표가지번호>
      <별표구분>서식</별표구분>
      <별표제목>통합 산업재해 현황 조사표</별표제목>
      <별표내용><![CDATA[별지 본문 1줄
별지 본문 2줄]]></별표내용>
    </별표단위>
  </별표>
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
    assert result.articles_upserted == 4


def test_ingest_is_idempotent_for_article_key(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    service = LawIngestService(client=FakeMolegApiClient(), parser=LawXmlParser(), session_factory=session_factory)

    service.ingest(["산업안전보건법"])
    service.ingest(["산업안전보건법"])

    with session_factory() as session:
        assert session.query(Law).count() == 1
        assert session.query(Article).count() == 4


def test_parser_supports_article_numbers_with_suffix():
    parsed = LawXmlParser().parse_law(ET.fromstring(LAW_XML), "산업안전보건법", "LAW001", "LAW")
    article_nos = [article["article_no"] for article in parsed.articles]
    assert "제23조의2" in article_nos


def test_parser_includes_appendix_as_article():
    parsed = LawXmlParser().parse_law(ET.fromstring(LAW_XML), "산업안전보건법", "LAW001", "LAW")

    appendix = next(article for article in parsed.articles if article["article_no"] == "별표 1")
    assert appendix["article_title"] == "법의 일부를 적용하지 않는 사업 또는 사업장"
    assert "별표 본문 1줄\n별표 본문 2줄" in appendix["article_text"]

    form = next(article for article in parsed.articles if article["article_no"] == "별지 1")
    assert form["article_title"] == "통합 산업재해 현황 조사표"
    assert "별지 본문 1줄\n별지 본문 2줄" in form["article_text"]
    assert appendix["article_key"] != form["article_key"]
