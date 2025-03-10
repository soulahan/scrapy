"""
This module implements a class which returns the appropriate Response class
based on different criteria.
"""

from __future__ import annotations

from io import StringIO
from mimetypes import MimeTypes
from pkgutil import get_data
from typing import TYPE_CHECKING

from scrapy.http import Response
from scrapy.utils.misc import load_object
from scrapy.utils.python import binary_is_text, to_bytes, to_unicode

if TYPE_CHECKING:
    from collections.abc import Mapping


class ResponseTypes:
    CLASSES = {
        "text/html": "scrapy.http.HtmlResponse",
        "application/atom+xml": "scrapy.http.XmlResponse",
        "application/rdf+xml": "scrapy.http.XmlResponse",
        "application/rss+xml": "scrapy.http.XmlResponse",
        "application/xhtml+xml": "scrapy.http.HtmlResponse",
        "application/vnd.wap.xhtml+xml": "scrapy.http.HtmlResponse",
        "application/xml": "scrapy.http.XmlResponse",
        "application/json": "scrapy.http.JsonResponse",
        "application/x-json": "scrapy.http.JsonResponse",
        "application/json-amazonui-streaming": "scrapy.http.JsonResponse",
        "application/javascript": "scrapy.http.TextResponse",
        "application/x-javascript": "scrapy.http.TextResponse",
        "text/xml": "scrapy.http.XmlResponse",
        "text/*": "scrapy.http.TextResponse",
    }

    def __init__(self) -> None:
        self.classes: dict[str, type[Response]] = {}
        self.mimetypes: MimeTypes = MimeTypes()
        mimedata = get_data("scrapy", "mime.types")
        if not mimedata:
            raise ValueError(
                "The mime.types file is not found in the Scrapy installation"
            )
        self.mimetypes.readfp(StringIO(mimedata.decode("utf8")))
        for mimetype, cls in self.CLASSES.items():
            self.classes[mimetype] = load_object(cls)

    def from_mimetype(self, mimetype: str) -> type[Response]:
        """Return the most appropriate Response class for the given mimetype"""
        if mimetype is None:
            return Response
        if mimetype in self.classes:
            return self.classes[mimetype]
        basetype = f"{mimetype.split('/')[0]}/*"
        return self.classes.get(basetype, Response)

    def from_content_type(
        self, content_type: str | bytes, content_encoding: bytes | None = None
    ) -> type[Response]:
        """Return the most appropriate Response class from an HTTP Content-Type
        header"""
        if content_encoding:
            return Response
        mimetype = (
            to_unicode(content_type, encoding="latin-1").split(";")[0].strip().lower()
        )
        return self.from_mimetype(mimetype)

    def from_content_disposition(
        self, content_disposition: str | bytes
    ) -> type[Response]:
        try:
            filename = (
                to_unicode(content_disposition, encoding="latin-1", errors="replace")
                .split(";")[1]
                .split("=")[1]
                .strip("\"'")
            )
            return self.from_filename(filename)
        except IndexError:
            return Response

    def from_headers(self, headers: Mapping[bytes, bytes]) -> type[Response]:
        """Return the most appropriate Response class by looking at the HTTP
        headers"""
        cls = Response
        if b"Content-Type" in headers:
            cls = self.from_content_type(
                content_type=headers[b"Content-Type"],
                content_encoding=headers.get(b"Content-Encoding"),
            )
        if cls is Response and b"Content-Disposition" in headers:
            cls = self.from_content_disposition(headers[b"Content-Disposition"])
        return cls

    def from_filename(self, filename: str) -> type[Response]:
        """Return the most appropriate Response class from a file name"""
        mimetype, encoding = self.mimetypes.guess_type(filename)
        if mimetype and not encoding:
            return self.from_mimetype(mimetype)
        return Response

    def from_body(self, body: bytes) -> type[Response]:
        """Try to guess the appropriate response based on the body content.
        This method is a bit magic and could be improved in the future, but
        it's not meant to be used except for special cases where response types
        cannot be guess using more straightforward methods."""
        chunk = body[:5000]
        chunk = to_bytes(chunk)
        if not binary_is_text(chunk):
            return self.from_mimetype("application/octet-stream")
        lowercase_chunk = chunk.lower()
        if b"<html>" in lowercase_chunk:
            return self.from_mimetype("text/html")
        if b"<?xml" in lowercase_chunk:
            return self.from_mimetype("text/xml")
        if b"<!doctype html>" in lowercase_chunk:
            return self.from_mimetype("text/html")
        return self.from_mimetype("text")

    def from_args(
        self,
        headers: Mapping[bytes, bytes] | None = None,
        url: str | None = None,
        filename: str | None = None,
        body: bytes | None = None,
    ) -> type[Response]:
        """Guess the most appropriate Response class based on
        the given arguments."""
        cls = Response
        if headers is not None:
            cls = self.from_headers(headers)
        if cls is Response and url is not None:
            cls = self.from_filename(url)
        if cls is Response and filename is not None:
            cls = self.from_filename(filename)
        if cls is Response and body is not None:
            cls = self.from_body(body)
        return cls


responsetypes = ResponseTypes()
