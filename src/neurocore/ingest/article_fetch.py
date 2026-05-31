"""URL normalization and retrieval helpers for article ingestion."""

from __future__ import annotations

import gzip
from pathlib import Path
import zlib
from urllib import request as urllib_request
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from neurocore.ingest.article_html import sanitize_article_html_to_markdown

TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
ARTICLE_FETCH_PRIMARY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "text/markdown;q=0.8,text/plain;q=0.7,*/*;q=0.5"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
ARTICLE_FETCH_FALLBACK_HEADERS = {
    **ARTICLE_FETCH_PRIMARY_HEADERS,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/json;q=0.8,text/plain;q=0.7,*/*;q=0.5"
    ),
    "Connection": "close",
    "Referer": "https://www.google.com/",
    "Upgrade-Insecure-Requests": "1",
}


def canonicalize_article_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Article URL must be an absolute http or https URL")
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    canonical = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        query=urlencode(query_pairs),
        fragment="",
        path=path,
    )
    return urlunparse(canonical)


def content_format_from_url(url: str, content_type: str) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".json":
        return "json"
    if suffix in {".html", ".htm"}:
        return "html"
    if "markdown" in content_type:
        return "markdown"
    if "json" in content_type:
        return "json"
    if "html" in content_type:
        return "html"
    return "text"


def title_from_url(url: str) -> str:
    parsed = urlparse(url)
    stem = Path(unquote(parsed.path)).stem.replace("-", " ").replace("_", " ").strip()
    return stem or parsed.netloc or "Imported URL"


def fetch_article_source(url: str) -> dict[str, object]:
    canonical_url = canonicalize_article_url(url)
    body: bytes | None = None
    content_type = ""
    last_error: Exception | None = None
    for request_headers, redirect_friendly in (
        (ARTICLE_FETCH_PRIMARY_HEADERS, False),
        (ARTICLE_FETCH_FALLBACK_HEADERS, True),
    ):
        request = _build_article_request(canonical_url, request_headers)
        try:
            with _open_article_request(
                request,
                timeout=20.0,
                redirect_friendly=redirect_friendly,
            ) as response:
                body = response.read()
                content_type = str(response.headers.get("Content-Type") or "").lower()
                content_encoding = str(
                    response.headers.get("Content-Encoding") or ""
                ).lower()
            break
        except Exception as exc:
            last_error = exc
    else:
        raise ValueError(
            f"Could not fetch article URL: {canonical_url}"
        ) from last_error

    if body is None:
        raise ValueError(f"Could not fetch article URL: {canonical_url}")
    content = _decode_article_body(
        body,
        content_type=content_type,
        content_encoding=content_encoding,
    )
    content_format = content_format_from_url(canonical_url, content_type)
    source: dict[str, object] = {
        "url": canonical_url,
        "canonical_url": canonical_url,
        "content": content,
        "content_format": content_format,
        "title": title_from_url(canonical_url),
    }
    if content_format == "html":
        sanitized_content, extracted_title = sanitize_article_html_to_markdown(
            content,
            fallback_title=source["title"],
        )
        source["content"] = sanitized_content
        source["content_format"] = "markdown"
        source["original_content_format"] = "html"
        source["sanitized_from_html"] = True
        if extracted_title:
            source["title"] = extracted_title
    return source


def _build_article_request(url: str, headers: dict[str, str]) -> urllib_request.Request:
    return urllib_request.Request(url=url, headers=headers, method="GET")


def _open_article_request(
    request: urllib_request.Request,
    *,
    timeout: float,
    redirect_friendly: bool,
):
    if not redirect_friendly:
        return urllib_request.urlopen(request, timeout=timeout)
    opener = urllib_request.build_opener(urllib_request.HTTPRedirectHandler())
    return opener.open(request, timeout=timeout)


def _decode_article_body(
    body: bytes, *, content_type: str, content_encoding: str
) -> str:
    decoded_body = _decode_content_encoding(body, content_encoding)
    candidate_encodings: list[str] = []
    charset = _charset_from_content_type(content_type)
    if charset:
        candidate_encodings.append(charset)
    candidate_encodings.extend(("utf-8", "utf-8-sig", "latin-1"))
    seen: set[str] = set()
    for encoding in candidate_encodings:
        normalized = encoding.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        try:
            return decoded_body.decode(normalized)
        except (LookupError, UnicodeDecodeError):
            continue
    return decoded_body.decode("utf-8", errors="replace")


def _decode_content_encoding(body: bytes, content_encoding: str) -> bytes:
    lowered = content_encoding.strip().lower()
    if lowered in {"", "identity"}:
        return body
    if lowered in {"gzip", "x-gzip"}:
        return gzip.decompress(body)
    if lowered == "deflate":
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    return body


def _charset_from_content_type(content_type: str) -> str | None:
    for part in content_type.split(";"):
        key, _, value = part.partition("=")
        if key.strip().lower() != "charset":
            continue
        charset = value.strip().strip('"').strip("'")
        return charset or None
    return None
