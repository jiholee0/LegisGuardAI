from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx

from app.core.config import get_settings


@dataclass
class MolegLawSummary:
    law_code: str
    law_name: str
    law_type: str
    detail_path: str
    detail_params: dict[str, str]


class MolegApiError(RuntimeError):
    pass


class MolegApiClient:
    RETRY_DELAYS_SECONDS = (0.5, 1.0)

    def __init__(self) -> None:
        self.settings = get_settings()

    def search_law(self, law_name: str) -> list[MolegLawSummary]:
        if not self.settings.moleg_api_key:
            raise MolegApiError("MOLEG_API_KEY is not configured.")

        params = {
            "OC": self.settings.moleg_api_key,
            "target": "law",
            "type": "XML",
            "query": law_name,
        }
        root = self._request_xml(
            url=self.settings.moleg_search_url,
            params=params,
            timeout=httpx.Timeout(30.0, connect=10.0),
            cache_filename=f"search_{law_name}.xml",
            request_label=f"law search for '{law_name}'",
        )
        law_nodes = self._select_law_nodes(root, law_name)
        if not law_nodes:
            raise MolegApiError(f"Law not found for query: {law_name}")
        summaries: list[MolegLawSummary] = []
        for law_node in law_nodes:
            law_code = self._find_text(law_node, ["법령일련번호", "법령ID", "법령id", "ID"])
            found_name = self._find_text(law_node, ["법령명한글", "법령명", "법령명_한글", "법령명한글full"]) or law_name
            law_type = self._find_text(law_node, ["법종구분", "법령구분명", "법령종류"]) or "LAW"
            detail_link = self._find_text(law_node, ["법령상세링크", "상세링크", "detailLink"])
            if not law_code or not detail_link:
                continue

            detail_path, detail_params = self._parse_detail_link(detail_link)
            summaries.append(
                MolegLawSummary(
                    law_code=law_code,
                    law_name=found_name,
                    law_type=law_type,
                    detail_path=detail_path,
                    detail_params=detail_params,
                )
            )

        if not summaries:
            raise MolegApiError(f"Missing detail link in search response for: {law_name}")
        return summaries

    def fetch_law_detail(self, summary: MolegLawSummary) -> ET.Element:
        if not self.settings.moleg_api_key:
            raise MolegApiError("MOLEG_API_KEY is not configured.")

        params = dict(summary.detail_params)
        params["OC"] = self.settings.moleg_api_key
        params["type"] = "XML"
        params.setdefault("target", "law")
        detail_url = urljoin(self.settings.moleg_detail_url, summary.detail_path)
        return self._request_xml(
            url=detail_url,
            params=params,
            timeout=httpx.Timeout(60.0, connect=10.0),
            cache_filename=f"detail_{summary.law_code}.xml",
            request_label=f"law detail for '{summary.law_name}'",
        )

    def _find_text(self, node: ET.Element, candidates: list[str]) -> str | None:
        for candidate in candidates:
            found = node.findtext(candidate)
            if found:
                return found.strip()
        return None

    def _request_xml(
        self,
        url: str,
        params: dict[str, str],
        timeout: httpx.Timeout,
        cache_filename: str,
        request_label: str,
    ) -> ET.Element:
        response: httpx.Response | None = None
        last_error: Exception | None = None

        for attempt in range(1, len(self.RETRY_DELAYS_SECONDS) + 2):
            try:
                response = httpx.get(url, params=params, timeout=timeout)
                break
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                last_error = exc
                if attempt > len(self.RETRY_DELAYS_SECONDS):
                    break
                time.sleep(self.RETRY_DELAYS_SECONDS[attempt - 1])
            except httpx.HTTPError as exc:
                raise MolegApiError(f"MOLEG API request failed during {request_label}: {exc}") from exc

        if response is None:
            if isinstance(last_error, httpx.ConnectTimeout):
                raise MolegApiError(f"Connection timed out during {request_label}.") from last_error
            if isinstance(last_error, httpx.ReadTimeout):
                raise MolegApiError(f"Response timed out during {request_label}.") from last_error
            if isinstance(last_error, httpx.ConnectError):
                raise MolegApiError(f"Connection failed during {request_label}: {last_error}") from last_error
            raise MolegApiError(f"MOLEG API request failed during {request_label}.") from last_error

        self._cache_response(cache_filename, response.text)
        api_error_message = self._extract_api_error_message(response.text, request_label)
        if api_error_message:
            raise MolegApiError(api_error_message)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MolegApiError(
                f"MOLEG API returned HTTP {exc.response.status_code} during {request_label}: {response.text.strip()}"
            ) from exc

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise MolegApiError(f"Invalid XML received during {request_label}.") from exc

        return root

    def _select_law_nodes(self, root: ET.Element, requested_law_name: str) -> list[ET.Element]:
        law_nodes = root.findall(".//law")
        if not law_nodes:
            return []
        return law_nodes

    def _parse_detail_link(self, detail_link: str) -> tuple[str, dict[str, str]]:
        parsed = urlparse(detail_link)
        params = {key: value for key, value in parse_qsl(parsed.query, keep_blank_values=True)}
        return parsed.path or "/DRF/lawService.do", params

    def _normalize_law_name(self, law_name: str) -> str:
        return re.sub(r"\s+", " ", law_name).strip()

    def _extract_api_error_message(self, xml_text: str, request_label: str) -> str | None:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None

        result_text = self._find_text(root, ["result", "RESULT", "Result"])
        message_text = self._find_text(root, ["msg", "MSG", "message", "Message"])

        normalized_result = (result_text or "").strip()
        normalized_message = (message_text or "").strip()

        if not normalized_result and not normalized_message:
            return None

        if normalized_result == "success":
            return None

        if normalized_result or normalized_message:
            return (
                f"MOLEG API error during {request_label}: "
                f"result='{normalized_result}', msg='{normalized_message}'"
            )

        return None

    def _cache_response(self, filename: str, body: str) -> None:
        cache_dir = Path(self.settings.raw_cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w\-.가-힣 ]+", "_", filename)
        (cache_dir / safe_name).write_text(body, encoding="utf-8")
