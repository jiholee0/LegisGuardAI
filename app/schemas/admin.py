from pydantic import BaseModel, Field


class IngestLawsRequest(BaseModel):
    targets: list[str] | None = Field(
        default=None,
        examples=[["산업안전보건법", "산업안전보건법 시행령", "산업안전보건법 시행규칙"]],
    )

class IngestLawsResponse(BaseModel):
    run_id: int
    status: str
    laws_upserted: int
    articles_upserted: int
    failed_targets: list[str] = Field(default_factory=list)


class ReindexRequest(BaseModel):
    recreate: bool = True


class ReindexResponse(BaseModel):
    run_id: int
    status: str
    chunks_indexed: int


class LawDbLawItem(BaseModel):
    law_id: int
    law_name: str
    law_type: str
    promulgation_no: str | None = None
    effective_date: str | None = None
    article_count: int
    is_current: bool


class LawDbSummaryResponse(BaseModel):
    total_laws: int
    total_articles: int
    laws: list[LawDbLawItem]


class LawArticleItem(BaseModel):
    article_no: str
    article_title: str | None = None
    article_text: str


class LawArticleListResponse(BaseModel):
    law_id: int
    law_name: str
    article_count: int
    articles: list[LawArticleItem]
