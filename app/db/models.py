from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Law(Base):
    __tablename__ = "laws"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    law_code: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    law_name: Mapped[str] = mapped_column(String(255), index=True)
    law_type: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64), default="MOLEG_OPEN_API")
    promulgation_no: Mapped[str | None] = mapped_column(String(128), nullable=True)
    promulgation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    articles: Mapped[list["Article"]] = relationship(
        back_populates="law",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("article_key", name="uq_article_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    law_id: Mapped[int] = mapped_column(ForeignKey("laws.id", ondelete="CASCADE"), index=True)
    article_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    article_no: Mapped[str] = mapped_column(String(64), index=True)
    article_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    article_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    article_order: Mapped[int] = mapped_column(Integer, nullable=False)
    paragraph_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    law: Mapped[Law] = relationship(back_populates="articles")


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    target_scope: Mapped[str] = mapped_column(Text, nullable=False)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
