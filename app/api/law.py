from fastapi import APIRouter, Depends
from fastapi import HTTPException
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.db.models import Article, Law
from app.db.session import get_session
from app.schemas.admin import LawArticleItem, LawArticleListResponse, LawDbLawItem, LawDbSummaryResponse


router = APIRouter(prefix="/law", tags=["law"])


def _is_counted_article() -> object:
    return and_(
        ~Article.article_no.like("별표%"),
        ~Article.article_no.like("별지%"),
        ~Article.article_no.like("%조의%"),
    )


@router.get("/db", response_model=LawDbSummaryResponse, description="법령 데이터베이스 요약 정보 조회")
def get_law_db_summary(session: Session = Depends(get_session)) -> LawDbSummaryResponse:
    counted_article_condition = _is_counted_article()
    rows = session.execute(
        select(
            Law.id,
            Law.law_name,
            Law.law_type,
            Law.promulgation_no,
            Law.effective_date,
            Law.is_current,
            func.coalesce(func.sum(case((counted_article_condition, 1), else_=0)), 0).label("article_count"),
        )
        .outerjoin(Article, Article.law_id == Law.id)
        .group_by(Law.id)
        .order_by(Law.law_name)
    ).all()

    total_laws = session.scalar(select(func.count(Law.id))) or 0
    total_articles = session.scalar(
        select(func.count(Article.id)).where(counted_article_condition)
    ) or 0

    return LawDbSummaryResponse(
        total_laws=total_laws,
        total_articles=total_articles,
        laws=[
            LawDbLawItem(
                law_id=row.id,
                law_name=row.law_name,
                law_type=row.law_type,
                promulgation_no=row.promulgation_no,
                effective_date=row.effective_date.isoformat() if row.effective_date else None,
                article_count=row.article_count,
                is_current=row.is_current,
            )
            for row in rows
        ],
    )


@router.get("/{law_id}/articles", response_model=LawArticleListResponse, description="법령의 조문 목록 조회")
def get_law_articles(law_id: int, session: Session = Depends(get_session)) -> LawArticleListResponse:
    law = session.get(Law, law_id)
    if law is None:
        raise HTTPException(status_code=404, detail="Law not found")

    articles = list(
        session.scalars(
            select(Article)
            .where(Article.law_id == law_id)
            .order_by(Article.article_order, Article.id)
        )
    )
    article_count = sum(
        1
        for article in articles
        if not article.article_no.startswith(("별표", "별지")) and "조의" not in article.article_no
    )

    return LawArticleListResponse(
        law_id=law.id,
        law_name=law.law_name,
        article_count=article_count,
        articles=[
            LawArticleItem(
                article_no=article.article_no,
                article_title=article.article_title,
                article_text=article.article_text,
            )
            for article in articles
        ],
    )
