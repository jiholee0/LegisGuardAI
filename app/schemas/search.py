from typing import Literal

from pydantic import BaseModel, Field, model_validator


class NoticeBodyJson(BaseModel):
    law_name: str | None = None
    content: str


class NoticeSearchRequest(BaseModel):
    input_type: Literal["text", "json"]
    title: str | None = None
    body: str | None = None
    body_json: NoticeBodyJson | None = None
    top_k: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def validate_payload(self) -> "NoticeSearchRequest":
        if self.input_type == "text" and not self.body:
            raise ValueError("body is required when input_type is 'text'")
        if self.input_type == "json" and not self.body_json:
            raise ValueError("body_json is required when input_type is 'json'")
        return self


class NoticeSearchMatch(BaseModel):
    law_name: str
    article_no: str
    article_title: str | None = None
    article_text: str
    score: float
    article_key: str


class NoticeSearchUnit(BaseModel):
    query_text: str
    matches: list[NoticeSearchMatch]


class NoticeSearchResponse(BaseModel):
    query_units: list[NoticeSearchUnit]
