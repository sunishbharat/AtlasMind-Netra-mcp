"""Report delivery channels (design doc: Delivery).

Strategy pattern: `BaseDeliveryChannel` is the swappable seam for Teams / Slack / email.
The current implementation writes a markdown file on disk so every query output can be
reviewed by a human with zero infrastructure. Future targets (Confluence, Teams webhook,
...) are new subclasses registered in the factory - call sites never change.
"""

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

import structlog

from config.settings import DeliverySettings
from core.exceptions import ConfigError

logger = structlog.get_logger(__name__)


class BaseDeliveryChannel(ABC):
    """Interface for delivering a finished report to its destination."""

    @abstractmethod
    async def deliver(self, *, report_id: str, content: str) -> str:
        """Deliver markdown content; returns the destination (file path or URL)."""


class MarkdownFileDeliveryChannel(BaseDeliveryChannel):
    """Writes the report as `<output_dir>/<report_id>.md` (viewable in any editor)."""

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    async def deliver(self, *, report_id: str, content: str) -> str:
        path = self._output_dir / f"{report_id}.md"
        logger.debug("report_writing", report_id=report_id, path=str(path))
        try:
            await asyncio.to_thread(self._write, path, content)
        except OSError as exc:
            logger.error("report_write_failed", report_id=report_id, path=str(path), error=str(exc))
            raise
        logger.debug("report_written", report_id=report_id, path=str(path))
        return str(path)

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def build_delivery_channel(settings: DeliverySettings) -> BaseDeliveryChannel:
    """Factory (Factory pattern): constructs the configured channel from settings."""
    if settings.channel == "markdown_file":
        return MarkdownFileDeliveryChannel(settings.output_dir)
    raise ConfigError(f"unknown delivery channel: {settings.channel}")
