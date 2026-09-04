"""网盘链接 / 磁力链接归档测试"""

from pathlib import Path

import pytest

from plugins.media_archiver.listener import (
    _archive_forward_links,
    _archive_plain_magnets,
    _extract_codes,
    _extract_links,
    _extract_magnets,
)


HASH = "0123456789abcdef0123456789abcdef01234567"
MAGNET = f"magnet:?xt=urn:btih:{HASH}"
MAGNET_DN = f"magnet:?xt=urn:btih:{HASH}&dn=foo.mp4&tr=udp://tracker.example:80"


def test_extract_magnets_basic():
    assert _extract_magnets(f"资源\n{MAGNET}") == [MAGNET]


def test_extract_magnets_with_params():
    assert _extract_magnets(MAGNET_DN) == [MAGNET_DN]


def test_extract_magnets_html_escaped():
    raw = f"magnet:?xt=urn:btih:{HASH}&amp;dn=bar"
    assert _extract_magnets(raw) == [f"magnet:?xt=urn:btih:{HASH}&dn=bar"]


def test_extract_magnets_trailing_punct():
    assert _extract_magnets(f"{MAGNET}。") == [MAGNET]
    assert _extract_magnets(f"{MAGNET}.") == [MAGNET]


def test_extract_magnets_case_insensitive_and_dedup():
    upper = MAGNET.upper()
    text = f"{MAGNET}\n{upper}"
    got = _extract_magnets(text)
    assert len(got) == 1
    assert got[0].lower() == MAGNET.lower()


def test_extract_magnets_ignores_http_and_empty():
    assert _extract_magnets("https://pan.baidu.com/s/xxx") == []
    assert _extract_magnets("") == []
    assert _extract_magnets("magnet:?foo=bar") == []


def test_extract_magnets_wrapped_and_multiple():
    other = "magnet:?xt=urn:btih:" + "a" * 40
    text = f"[{MAGNET}] 还有 {other}"
    assert _extract_magnets(text) == [MAGNET, other]


def test_extract_links_still_http_only():
    text = f"{MAGNET}\nhttps://pan.xunlei.com/s/abc?pwd=1234"
    links = _extract_links(text)
    assert MAGNET not in links
    assert links == ["https://pan.xunlei.com/s/abc?pwd=1234"]
    assert _extract_codes("提取码: 1234") == ["1234"]


@pytest.mark.asyncio
async def test_archive_forward_writes_separate_files(tmp_path: Path, monkeypatch):
    class DummyCfg:
        def get_archive_path(self):
            return tmp_path

    monkeypatch.setattr(
        "plugins.media_archiver.listener.get_config", lambda: DummyCfg(),
    )

    content = [
        {
            "user_id": 1001,
            "message": [{"type": "text", "data": {"text": f"网盘 https://pan.baidu.com/s/xyz 提取码: abcd"}}],
        },
        {
            "user_id": 1002,
            "message": [{"type": "text", "data": {"text": f"种子 {MAGNET_DN}"}}],
        },
    ]
    await _archive_forward_links(
        content=content, group_id=123, source_message_id=999, user_id=1,
    )

    month_dirs = list((tmp_path / "123" / "links").iterdir())
    assert len(month_dirs) == 1
    links_dir = month_dirs[0]
    cloud = links_dir / "forward_999.md"
    magnet = links_dir / "magnet_999.md"
    assert cloud.is_file()
    assert magnet.is_file()

    cloud_text = cloud.read_text(encoding="utf-8")
    magnet_text = magnet.read_text(encoding="utf-8")
    assert "https://pan.baidu.com/s/xyz" in cloud_text
    assert "abcd" in cloud_text
    assert "磁力链接:" not in cloud_text
    assert MAGNET_DN in magnet_text
    assert "https://pan.baidu.com/s/xyz" not in magnet_text
    assert magnet_text.startswith("# 转发消息磁力链接存档")


@pytest.mark.asyncio
async def test_archive_forward_magnet_only(tmp_path: Path, monkeypatch):
    class DummyCfg:
        def get_archive_path(self):
            return tmp_path

    monkeypatch.setattr(
        "plugins.media_archiver.listener.get_config", lambda: DummyCfg(),
    )

    content = [
        {
            "user_id": 7,
            "message": [{"type": "text", "data": {"text": MAGNET}}],
        },
    ]
    await _archive_forward_links(
        content=content, group_id=55, source_message_id=42, user_id=7,
    )
    links_dir = next((tmp_path / "55" / "links").iterdir())
    assert (links_dir / "magnet_42.md").is_file()
    assert not (links_dir / "forward_42.md").exists()


@pytest.mark.asyncio
async def test_archive_plain_magnets(tmp_path: Path, monkeypatch):
    class DummyCfg:
        def get_archive_path(self):
            return tmp_path

    monkeypatch.setattr(
        "plugins.media_archiver.listener.get_config", lambda: DummyCfg(),
    )

    segs = [{"type": "text", "data": {"text": f"看这个 {MAGNET}"}}]
    await _archive_plain_magnets(segs, group_id=66, message_id=8, user_id=9)
    links_dir = next((tmp_path / "66" / "links").iterdir())
    fpath = links_dir / "magnet_8.md"
    assert fpath.is_file()
    text = fpath.read_text(encoding="utf-8")
    assert MAGNET in text
    assert text.startswith("# 消息磁力链接存档")
