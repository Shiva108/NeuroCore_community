"""Shared HTML sanitization helpers for article ingestion."""

from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import re

IGNORED_HTML_TAGS = frozenset(
    {
        "aside",
        "button",
        "figure",
        "footer",
        "form",
        "head",
        "iframe",
        "img",
        "input",
        "link",
        "meta",
        "nav",
        "noscript",
        "script",
        "style",
        "svg",
    }
)
VOID_HTML_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
NAVIGATION_HINTS = (
    "cookie",
    "privacy policy",
    "subscribe",
    "sign in",
    "menu",
    "newsletter",
)
TARGET_SPECIFIC_PATTERNS = (
    r"\bclient-specific\b",
    r"\bcustomer vpn\b",
    r"\bprovided credentials\b",
    r"\binternal dashboard\b",
    r"\binternal portal\b",
    r"\bclient portal\b",
    r"\bthis engagement\b",
    r"\bthe engagement\b",
    r"\binvoice\s+#?\d+\b",
    r"https?://[^\s]+?\.(?:internal|corp|local)\b",
)


class ArticleHTMLMarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._parts: list[str] = []
        self._ignore_depth = 0
        self._list_depth = 0
        self._in_pre = False
        self._in_inline_code = False
        self._capturing_title = False
        self._title_parts: list[str] = []
        self._current_heading_text: list[str] | None = None
        self.first_heading: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if lowered in IGNORED_HTML_TAGS:
            if lowered not in VOID_HTML_TAGS:
                self._ignore_depth += 1
            return
        if self._ignore_depth:
            return
        if lowered == "title":
            self._capturing_title = True
            return
        if lowered in {"article", "main", "section", "div", "p", "header"}:
            self._ensure_newlines(2)
            return
        if lowered in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._ensure_newlines(2)
            level = max(1, min(int(lowered[1]), 6))
            self._append_raw(f"{'#' * level} ")
            self._current_heading_text = []
            return
        if lowered in {"ul", "ol"}:
            self._list_depth += 1
            self._ensure_newlines(1)
            return
        if lowered == "li":
            self._ensure_newlines(1)
            indent = "  " * max(0, self._list_depth - 1)
            self._append_raw(f"{indent}- ")
            return
        if lowered == "br":
            self._ensure_newlines(1)
            return
        if lowered == "pre":
            self._ensure_newlines(2)
            self._append_raw("```text\n")
            self._in_pre = True
            return
        if lowered == "code" and not self._in_pre:
            self._append_raw("`")
            self._in_inline_code = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in IGNORED_HTML_TAGS:
            if self._ignore_depth:
                self._ignore_depth -= 1
            return
        if self._ignore_depth:
            return
        if lowered == "title":
            self._capturing_title = False
            return
        if lowered in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            heading = " ".join(
                part for part in self._current_heading_text or [] if part
            )
            if heading and self.first_heading is None:
                self.first_heading = heading
            self._current_heading_text = None
            self._ensure_newlines(2)
            return
        if lowered in {"ul", "ol"}:
            self._list_depth = max(0, self._list_depth - 1)
            self._ensure_newlines(1)
            return
        if lowered == "pre":
            if not self._endswith("\n"):
                self._append_raw("\n")
            self._append_raw("```\n\n")
            self._in_pre = False
            return
        if lowered == "code" and self._in_inline_code:
            self._append_raw("`")
            self._in_inline_code = False
            return
        if lowered in {"article", "main", "section", "div", "p", "header", "li"}:
            self._ensure_newlines(2 if lowered != "li" else 1)

    def handle_data(self, data: str) -> None:
        if self._ignore_depth:
            return
        if self._capturing_title:
            text = " ".join(unescape(data).split())
            if text:
                self._title_parts.append(text)
            return
        if self._in_pre:
            self._append_raw(unescape(data).replace("\r\n", "\n"))
            return
        text = " ".join(unescape(data).split())
        if not text:
            return
        if self._current_heading_text is not None:
            self._current_heading_text.append(text)
        self._append_text(text)

    @property
    def extracted_title(self) -> str | None:
        title = " ".join(self._title_parts).strip()
        return self.first_heading or title

    def render_markdown(self) -> str:
        rendered = "".join(self._parts)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered)
        rendered = "\n".join(line.rstrip() for line in rendered.splitlines())
        return rendered.strip()

    def _append_text(self, text: str) -> None:
        if not self._parts:
            self._parts.append(text)
            return
        last = self._parts[-1]
        if last.endswith((" ", "\n", "`")):
            self._parts.append(text)
            return
        self._parts.append(f" {text}")

    def _append_raw(self, text: str) -> None:
        if text:
            self._parts.append(text)

    def _endswith(self, suffix: str) -> bool:
        return bool(self._parts and self._parts[-1].endswith(suffix))

    def _ensure_newlines(self, count: int) -> None:
        if count <= 0 or not self._parts:
            return
        joined = "".join(self._parts)
        trailing = len(joined) - len(joined.rstrip("\n"))
        needed = max(0, count - trailing)
        if needed:
            self._parts.append("\n" * needed)


def preferred_article_html_fragment(content: str) -> str:
    for tag in ("article", "main"):
        match = re.search(
            rf"<{tag}\b[^>]*>.*?</{tag}>",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match is not None:
            return match.group(0)
    return content


def strip_html_to_text(content: str) -> str:
    without_scripts = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        " ",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    without_tags = re.sub(r"<[^>]+>", " ", without_scripts)
    return " ".join(unescape(without_tags).split())


def sanitize_article_html_to_markdown(
    content: str,
    *,
    fallback_title: str | None,
) -> tuple[str, str | None]:
    content = preferred_article_html_fragment(content)
    parser = ArticleHTMLMarkdownParser()
    parser.feed(content)
    parser.close()
    body = parser.render_markdown()
    title = str(parser.extracted_title or fallback_title or "").strip() or None
    if not body:
        body = strip_html_to_text(content)
    if title and not body.startswith("# "):
        body = f"# {title}\n\n{body}".strip()
    return body.strip(), title


def looks_like_markup_or_navigation(
    *,
    raw_content: str,
    content_format: str,
    evaluation_text: str,
) -> bool:
    if content_format.strip().lower() != "html":
        return False
    tag_count = len(re.findall(r"<[^>]+>", raw_content))
    nav_hits = sum(
        1 for phrase in NAVIGATION_HINTS if phrase in evaluation_text.lower()
    )
    word_count = len(evaluation_text.split()) if evaluation_text else 0
    return bool(
        (tag_count >= 25 and word_count < 40) or (nav_hits >= 3 and word_count < 80)
    )


def looks_like_target_specific_article(evaluation_text: str) -> bool:
    lowered = evaluation_text.lower()
    hits = 0
    for pattern in TARGET_SPECIFIC_PATTERNS:
        if re.search(pattern, lowered):
            hits += 1
    return hits >= 2
