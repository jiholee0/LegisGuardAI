from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
import xml.etree.ElementTree as ET

from app.services.text_normalizer import normalize_text, text_hash


ARTICLE_TAG_CANDIDATES = ["조문", "article", "Article"]
PARAGRAPH_TAG_CANDIDATES = ["항", "paragraph", "Paragraph"]


@dataclass
class ParsedLaw:
    law: dict
    articles: list[dict]


class LawXmlParser:
    def parse_law(self, root: ET.Element, fallback_law_name: str, fallback_law_code: str, fallback_law_type: str) -> ParsedLaw:
        base_info = root.find("./기본정보") or root
        law_name = self._find_text(base_info, ["법령명한글", "법령명", "법령명_한글"]) or fallback_law_name
        law_type = self._classify_law_type(
            law_name,
            self._find_text(base_info, ["법종구분", "법령구분명", "법령종류"]) or fallback_law_type,
        )
        promulgation_no = self._find_text(base_info, ["공포번호"])
        promulgation_date = self._parse_date(self._find_text(base_info, ["공포일자"]))
        effective_date = self._parse_date(self._find_text(base_info, ["시행일자"]))

        articles: list[dict] = []
        for index, article_node in enumerate(self._iter_article_nodes(root), start=1):
            article_no = self._build_article_no(article_node, index)
            article_title = self._find_text(article_node, ["조문제목", "조제목", "title"])
            article_text = self._build_article_text(article_node)
            normalized_text = normalize_text(article_text)
            if not normalized_text:
                continue

            paragraphs = self._extract_paragraphs(article_node)
            articles.append(
                {
                    "article_key": f"{law_name}:{article_no}",
                    "article_no": article_no,
                    "article_title": article_title,
                    "article_text": article_text,
                    "normalized_text": normalized_text,
                    "article_order": index,
                    "paragraph_json": paragraphs if paragraphs else None,
                    "effective_date": effective_date,
                    "hash": text_hash(normalized_text),
                }
            )

        for appendix_index, appendix_node in enumerate(self._iter_appendix_nodes(root), start=1):
            article_no = self._build_appendix_no(appendix_node, appendix_index)
            article_title = self._find_text(appendix_node, ["별표제목", "title"])
            article_text = self._build_appendix_text(appendix_node)
            appendix_key = self._find_appendix_key(appendix_node, appendix_index)
            normalized_text = normalize_text(article_text)
            if not normalized_text:
                continue

            articles.append(
                {
                    "article_key": f"{law_name}:{article_no}:{appendix_key}",
                    "article_no": article_no,
                    "article_title": article_title,
                    "article_text": article_text,
                    "normalized_text": normalized_text,
                    "article_order": len(articles) + 1,
                    "paragraph_json": None,
                    "effective_date": effective_date,
                    "hash": text_hash(normalized_text),
                }
            )

        return ParsedLaw(
            law={
                "law_code": fallback_law_code,
                "law_name": law_name,
                "law_type": law_type,
                "source": "MOLEG_OPEN_API",
                "promulgation_no": promulgation_no,
                "promulgation_date": promulgation_date,
                "effective_date": effective_date,
                "is_current": True,
            },
            articles=articles,
        )

    def _iter_article_nodes(self, root: ET.Element) -> list[ET.Element]:
        article_units = root.findall(".//조문단위")
        if article_units:
            return [node for node in article_units if self._find_text(node, ["조문여부"]) == "조문"]

        for tag in ARTICLE_TAG_CANDIDATES:
            nodes = root.findall(f".//{tag}")
            if nodes:
                return nodes

        results: list[ET.Element] = []
        for node in root.iter():
            has_number = self._find_text(node, ["조문번호", "조번호", "articleNo", "num"])
            if has_number:
                results.append(node)
        return results

    def _build_article_text(self, article_node: ET.Element) -> str:
        sections: list[str] = []
        main_text = self._find_text(article_node, ["조문내용", "내용", "text"])
        if main_text:
            sections.append(main_text)

        for paragraph in self._extract_paragraph_texts(article_node):
            if paragraph not in sections:
                sections.append(paragraph)

        return self._join_sections(sections)

    def _build_appendix_text(self, appendix_node: ET.Element) -> str:
        sections: list[str] = []
        appendix_text = self._find_multiline_text(appendix_node, ["별표내용", "내용", "text"])
        if appendix_text:
            sections.append(appendix_text)
        return self._join_sections(sections)

    def _extract_paragraph_texts(self, article_node: ET.Element) -> list[str]:
        collected: list[str] = []
        for tag in PARAGRAPH_TAG_CANDIDATES:
            for paragraph in article_node.findall(f"./{tag}"):
                parts: list[str] = []
                paragraph_text = self._find_text(paragraph, ["항내용", "내용", "text"])
                if paragraph_text:
                    parts.append(paragraph_text)

                for ho in paragraph.findall("./호"):
                    ho_text = self._find_text(ho, ["호내용"])
                    if ho_text:
                        parts.append(ho_text)

                    for mok in ho.findall("./목"):
                        mok_text = self._find_text(mok, ["목내용"])
                        if mok_text:
                            parts.append(mok_text)

                text = self._join_lines(parts)
                if text:
                    collected.append(text)
        return collected

    def _extract_paragraphs(self, article_node: ET.Element) -> str | None:
        paragraphs = self._extract_paragraph_texts(article_node)
        if not paragraphs:
            return None
        import json

        return json.dumps([{"order": index + 1, "text": text} for index, text in enumerate(paragraphs)], ensure_ascii=False)

    def _iter_appendix_nodes(self, root: ET.Element) -> list[ET.Element]:
        appendix_units = root.findall(".//별표단위")
        if appendix_units:
            return appendix_units
        return []

    def _find_text(self, node: ET.Element, candidates: list[str]) -> str | None:
        for candidate in candidates:
            found = node.findtext(candidate)
            if found:
                return normalize_text(found)
        return None

    def _find_multiline_text(self, node: ET.Element, candidates: list[str]) -> str | None:
        for candidate in candidates:
            found = node.find(candidate)
            if found is None:
                continue
            raw_text = "".join(found.itertext())
            if not raw_text:
                continue
            lines = [normalize_text(line) for line in raw_text.splitlines()]
            normalized_lines = [line for line in lines if line]
            if normalized_lines:
                return "\n".join(normalized_lines)
        return None

    def _parse_date(self, value: str | None) -> date | None:
        if not value:
            return None
        digits = re.sub(r"[^0-9]", "", value)
        if len(digits) != 8:
            return None
        return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))

    def _classify_law_type(self, law_name: str, fallback: str) -> str:
        if "시행규칙" in law_name:
            return "ENFORCEMENT_RULE"
        if "시행령" in law_name:
            return "ENFORCEMENT_DECREE"
        if fallback:
            return fallback
        return "LAW"

    def _build_article_no(self, article_node: ET.Element, index: int) -> str:
        article_number = self._find_text(article_node, ["조문번호", "조번호", "articleNo", "num"]) or str(index)
        branch_number = self._find_text(article_node, ["조문가지번호"])
        normalized_article_number = article_number.replace(" ", "")
        if normalized_article_number.startswith("제") and "조" in normalized_article_number:
            return normalized_article_number
        if branch_number and branch_number != "0":
            return f"제{article_number}조의{branch_number}"
        return f"제{article_number}조"

    def _build_appendix_no(self, appendix_node: ET.Element, index: int) -> str:
        appendix_number = self._find_text(appendix_node, ["별표번호"]) or str(index)
        appendix_branch_number = self._find_text(appendix_node, ["별표가지번호"])
        appendix_type = self._find_text(appendix_node, ["별표구분"])
        normalized_number = appendix_number.lstrip("0") or "0"
        normalized_branch = (appendix_branch_number or "").lstrip("0")
        label = self._resolve_appendix_label(appendix_type)

        if normalized_branch:
            return f"{label} {normalized_number}의{normalized_branch}"
        return f"{label} {normalized_number}"

    def _find_appendix_key(self, appendix_node: ET.Element, index: int) -> str:
        appendix_key = appendix_node.attrib.get("별표키")
        if appendix_key:
            return appendix_key
        fallback = self._find_text(appendix_node, ["별표번호"]) or str(index)
        return f"NO{fallback}"

    def _resolve_appendix_label(self, appendix_type: str | None) -> str:
        if appendix_type == "서식":
            return "별지"
        if appendix_type == "별표":
            return "별표"
        if appendix_type:
            return appendix_type
        return "별표"

    def _join_lines(self, parts: list[str]) -> str:
        normalized_lines = [normalize_text(part) for part in parts if normalize_text(part)]
        return "\n".join(normalized_lines)

    def _join_sections(self, parts: list[str]) -> str:
        normalized_sections = [part.strip() for part in parts if part and part.strip()]
        return "\n\n".join(normalized_sections)
