"""
Public HTTP reader — clean web pages, RSS/Atom feeds, public text.

Stdlib HTML-to-text extraction (no headless browser): scripts/styles are
dropped, block elements become line breaks, the <title> is captured.  Pages
that genuinely need JavaScript belong to the (future) browser worker, not
this adapter.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from helios.web.adapters.base import BaseSourceAdapter
from helios.web.types import SourceCapabilities, SourceDocument, WebAccessRequest

_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
               "section", "article", "blockquote", "pre"}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:  # <title> lives inside <head>, which is skipped
            self.title_parts.append(data)
        elif not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str | None]:
    """Return (text, title) extracted from an HTML document."""
    extractor = _TextExtractor()
    extractor.feed(html or "")
    text = re.sub(r"[ \t]+", " ", "".join(extractor.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    title = "".join(extractor.title_parts).strip() or None
    return text, title


def parse_feed(xml_text: str) -> list[dict[str, str | None]]:
    """Minimal RSS/Atom item extraction."""
    items: list[dict[str, str | None]] = []
    root = ET.fromstring(xml_text)
    ns_atom = "{http://www.w3.org/2005/Atom}"

    for item in root.iter("item"):  # RSS
        items.append({
            "title": (item.findtext("title") or "").strip() or None,
            "url": (item.findtext("link") or "").strip() or None,
            "published_at": (item.findtext("pubDate") or "").strip() or None,
            "content": (item.findtext("description") or "").strip(),
        })
    for entry in root.iter(f"{ns_atom}entry"):  # Atom
        link = entry.find(f"{ns_atom}link")
        items.append({
            "title": (entry.findtext(f"{ns_atom}title") or "").strip() or None,
            "url": link.get("href") if link is not None else None,
            "published_at": (entry.findtext(f"{ns_atom}updated") or "").strip() or None,
            "content": (entry.findtext(f"{ns_atom}summary") or "").strip(),
        })
    return items


class HttpReaderAdapter(BaseSourceAdapter):
    name = "web"
    version = "0.1.0"
    trust_level = "builtin"
    capabilities = SourceCapabilities(read=True)

    def read(self, request: WebAccessRequest) -> SourceDocument:
        raw = self._get_text(request.url)
        stripped = raw.lstrip()

        if stripped.startswith("<?xml") or "<rss" in stripped[:200] or "<feed" in stripped[:200]:
            items = parse_feed(raw)
            content = "\n\n".join(
                f"- {i['title'] or '(untitled)'} — {i['url'] or ''}\n  {i['content']}"
                for i in items[: request.max_results]
            )
            return SourceDocument(
                source=self.name,
                operation="read",
                url=request.url,
                title="Feed",
                content=content,
                content_type="feed",
                source_adapter=self.name,
                adapter_version=self.version,
                citations=[{"url": i["url"]} for i in items[: request.max_results] if i["url"]],
            )

        text, title = html_to_text(raw)
        return SourceDocument(
            source=self.name,
            operation="read",
            url=request.url,
            title=title,
            content=text,
            content_type="page",
            source_adapter=self.name,
            adapter_version=self.version,
            citations=[{"url": request.url}],
        )
