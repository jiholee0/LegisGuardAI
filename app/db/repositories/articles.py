from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Article, Law


class ArticleRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_many(self, records: list[dict]) -> int:
        count = 0
        for record in records:
            existing = self.session.scalar(select(Article).where(Article.article_key == record["article_key"]))
            if existing is None:
                existing = Article(**record)
                self.session.add(existing)
            else:
                for key, value in record.items():
                    setattr(existing, key, value)
            count += 1
        return count

    def list_all(self) -> list[Article]:
        return list(self.session.scalars(select(Article).order_by(Article.article_order, Article.id)))

    def get_by_id(self, article_id: int) -> Article | None:
        stmt = select(Article).options(joinedload(Article.law)).where(Article.id == article_id)
        return self.session.scalar(stmt)

    def list_by_law_name_and_article_no(self, law_name: str, article_no: str) -> list[Article]:
        stmt = (
            select(Article)
            .join(Law, Article.law_id == Law.id)
            .options(joinedload(Article.law))
            .where(Law.law_name == law_name, Article.article_no == article_no)
            .order_by(Article.article_order, Article.id)
        )
        return list(self.session.scalars(stmt))
