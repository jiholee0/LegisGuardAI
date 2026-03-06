from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from app.schemas.search import ToolAuditItem


SummaryFn = Callable[[dict[str, Any]], str | None]
OutputSummaryFn = Callable[[Any], str | None]
logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    name: str
    handler: Callable[..., Any]
    summarize_input: SummaryFn | None = None
    summarize_output: OutputSummaryFn | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._audit: list[ToolAuditItem] = []

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec
        handler_name = self._handler_name(spec.handler)
        logger.info(
            "Tool registered: %s -> %s",
            spec.name,
            handler_name,
            extra={
                "tool_name": spec.name,
                "handler": handler_name,
            },
        )

    @property
    def audit(self) -> list[ToolAuditItem]:
        return list(self._audit)

    def execute(self, tool_name: str, **kwargs):
        spec = self._tools[tool_name]
        handler_name = self._handler_name(spec.handler)
        input_summary = spec.summarize_input(kwargs) if spec.summarize_input else None
        logger.info(
            "Tool execution started: %s (handler=%s)",
            tool_name,
            handler_name,
            extra={
                "tool_name": tool_name,
                "handler": handler_name,
                "input_summary": input_summary,
            },
        )
        try:
            result = spec.handler(**kwargs)
        except Exception as exc:
            output_summary = self._summarize_exception(exc)
            self._audit.append(
                ToolAuditItem(
                    tool_name=tool_name,
                    status="error",
                    input_summary=input_summary,
                    output_summary=output_summary,
                )
            )
            logger.exception(
                "Tool execution failed: %s (handler=%s)",
                tool_name,
                handler_name,
                extra={
                    "tool_name": tool_name,
                    "handler": handler_name,
                    "input_summary": input_summary,
                    "output_summary": output_summary,
                },
            )
            raise

        output_summary = spec.summarize_output(result) if spec.summarize_output else None
        self._audit.append(
            ToolAuditItem(
                tool_name=tool_name,
                status="success",
                input_summary=input_summary,
                output_summary=output_summary,
            )
        )
        logger.info(
            "Tool execution completed: %s (handler=%s)",
            tool_name,
            handler_name,
            extra={
                "tool_name": tool_name,
                "handler": handler_name,
                "output_summary": output_summary,
            },
        )
        return result

    def record_skip(self, tool_name: str, *, input_summary: str | None = None, output_summary: str | None = None) -> None:
        self._audit.append(
            ToolAuditItem(
                tool_name=tool_name,
                status="skipped",
                input_summary=input_summary,
                output_summary=output_summary,
            )
        )
        logger.info(
            "Tool execution skipped: %s",
            tool_name,
            extra={
                "tool_name": tool_name,
                "input_summary": input_summary,
                "output_summary": output_summary,
            },
        )

    def _summarize_exception(self, exc: Exception) -> str:
        message = " ".join(str(exc).split()).strip()
        if not message:
            return exc.__class__.__name__
        return f"{exc.__class__.__name__}: {message}"

    def _handler_name(self, handler: Callable[..., Any]) -> str:
        module = getattr(handler, "__module__", "")
        qualname = getattr(handler, "__qualname__", getattr(handler, "__name__", str(handler)))
        return f"{module}.{qualname}" if module else qualname
