"""HTML-to-text conversion and JSON-LD extraction, using only the standard library.

The input is untrusted page content. These helpers only turn it into plain data:
nothing in it is executed or followed.
"""

import json
import re
from html.parser import HTMLParser
from typing import Any

_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "tr", "ul",
    }
)  # fmt: skip
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")
            if tag == "li":
                self._parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        lines = (
            re.sub(r"[ \t\r\f\v\xa0]+", " ", line).strip()
            for line in "".join(self._parts).split("\n")
        )
        joined = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", joined).strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


class _JsonLdExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.blocks: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and any(
            name == "type" and (value or "").strip().lower() == "application/ld+json"
            for name, value in attrs
        ):
            self._current = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._current is not None:
            self.blocks.append("".join(self._current))
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)


def find_job_postings(html: str) -> list[dict[str, Any]]:
    """Return every schema.org JobPosting object embedded as JSON-LD in `html`."""
    parser = _JsonLdExtractor()
    parser.feed(html)
    parser.close()
    postings: list[dict[str, Any]] = []
    for block in parser.blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        postings.extend(_iter_job_postings(data))
    return postings


def _iter_job_postings(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, list):
        return [posting for item in node for posting in _iter_job_postings(item)]
    if not isinstance(node, dict):
        return []
    types = node.get("@type")
    type_list = types if isinstance(types, list) else [types]
    if "JobPosting" in type_list:
        return [node]
    return _iter_job_postings(node.get("@graph", []))


class _TitleExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self._parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title" and self.title is None:
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._parts is not None:
            self.title = " ".join("".join(self._parts).split()) or None
            self._parts = None

    def handle_data(self, data: str) -> None:
        if self._parts is not None:
            self._parts.append(data)


def page_title(html: str) -> str | None:
    """The text of the page's first <title>, whitespace-collapsed, or None."""
    parser = _TitleExtractor()
    parser.feed(html)
    parser.close()
    return parser.title
