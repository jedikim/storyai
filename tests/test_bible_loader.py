from __future__ import annotations

from pathlib import Path

from server.load_bible import BibleLoader


def test_frontmatter_accepts_crlf() -> None:
    metadata, body, body_start = BibleLoader._frontmatter(
        "---\r\ntitle: 테스트\r\n---\r\n\r\n# 본문\r\n",
        Path("test.md"),
    )
    assert metadata == {"title": "테스트"}
    assert body == "# 본문\r\n"
    assert body_start > 0
