"""Delivery channels: markdown file writing and the factory."""

from pathlib import Path

from briefings.delivery import (
    MarkdownFileDeliveryChannel,
    build_delivery_channel,
)
from config.settings import DeliverySettings


async def test_writes_markdown_file_and_returns_path(tmp_path: Path) -> None:
    channel = MarkdownFileDeliveryChannel(tmp_path / "reports")
    location = await channel.deliver(report_id="20260612_x", content="# Report\n")
    path = Path(location)
    assert path.name == "20260612_x.md"
    assert path.read_text(encoding="utf-8") == "# Report\n"


async def test_creates_nested_output_dir(tmp_path: Path) -> None:
    channel = MarkdownFileDeliveryChannel(tmp_path / "a" / "b" / "reports")
    location = await channel.deliver(report_id="r1", content="x")
    assert Path(location).is_file()


def test_factory_builds_markdown_channel(tmp_path: Path) -> None:
    settings = DeliverySettings(output_dir=tmp_path)
    channel = build_delivery_channel(settings)
    assert isinstance(channel, MarkdownFileDeliveryChannel)
