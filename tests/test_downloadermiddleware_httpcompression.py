from gzip import GzipFile
from io import BytesIO
from logging import WARNING
from pathlib import Path

import pytest
from testfixtures import LogCapture
from w3lib.encoding import resolve_encoding

from scrapy.downloadermiddlewares.httpcompression import (
    ACCEPTED_ENCODINGS,
    HttpCompressionMiddleware,
)
from scrapy.exceptions import IgnoreRequest, NotConfigured
from scrapy.http import HtmlResponse, Request, Response
from scrapy.responsetypes import responsetypes
from scrapy.spiders import Spider
from scrapy.utils.gz import gunzip
from scrapy.utils.test import get_crawler
from tests import tests_datadir

SAMPLEDIR = Path(tests_datadir, "compressed")

FORMAT = {
    "gzip": ("html-gzip.bin", "gzip"),
    "x-gzip": ("html-gzip.bin", "x-gzip"),
    "rawdeflate": ("html-rawdeflate.bin", "deflate"),
    "zlibdeflate": ("html-zlibdeflate.bin", "deflate"),
    "gzip-deflate": ("html-gzip-deflate.bin", "gzip, deflate"),
    "gzip-deflate-gzip": ("html-gzip-deflate-gzip.bin", "gzip, deflate, gzip"),
    "br": ("html-br.bin", "br"),
    # $ zstd raw.html --content-size -o html-zstd-static-content-size.bin
    "zstd-static-content-size": ("html-zstd-static-content-size.bin", "zstd"),
    # $ zstd raw.html --no-content-size -o html-zstd-static-no-content-size.bin
    "zstd-static-no-content-size": ("html-zstd-static-no-content-size.bin", "zstd"),
    # $ cat raw.html | zstd -o html-zstd-streaming-no-content-size.bin
    "zstd-streaming-no-content-size": (
        "html-zstd-streaming-no-content-size.bin",
        "zstd",
    ),
    **{
        f"bomb-{format_id}": (f"bomb-{format_id}.bin", format_id)
        for format_id in (
            "br",  # 34 → 11 511 612
            "deflate",  # 27 968 → 11 511 612
            "gzip",  # 27 988 → 11 511 612
            "zstd",  # 1 096 → 11 511 612
        )
    },
}


def _skip_if_no_br() -> None:
    try:
        try:
            import brotli  # noqa: F401,PLC0415
        except ImportError:
            import brotlicffi  # noqa: F401,PLC0415
    except ImportError:
        pytest.skip("no brotli support")


def _skip_if_no_zstd() -> None:
    try:
        import zstandard  # noqa: F401,PLC0415
    except ImportError:
        pytest.skip("no zstd support (zstandard)")


class TestHttpCompression:
    def setup_method(self):
        self.crawler = get_crawler(Spider)
        self.spider = self.crawler._create_spider("scrapytest.org")
        self.mw = HttpCompressionMiddleware.from_crawler(self.crawler)
        self.crawler.stats.open_spider(self.spider)

    def _getresponse(self, coding):
        if coding not in FORMAT:
            raise ValueError

        samplefile, contentencoding = FORMAT[coding]

        body = (SAMPLEDIR / samplefile).read_bytes()

        headers = {
            "Server": "Yaws/1.49 Yet Another Web Server",
            "Date": "Sun, 08 Mar 2009 00:41:03 GMT",
            "Content-Length": len(body),
            "Content-Type": "text/html",
            "Content-Encoding": contentencoding,
        }

        response = Response("http://scrapytest.org/", body=body, headers=headers)
        response.request = Request(
            "http://scrapytest.org", headers={"Accept-Encoding": "gzip, deflate"}
        )
        return response

    def assertStatsEqual(self, key, value):
        assert self.crawler.stats.get_value(key, spider=self.spider) == value, str(
            self.crawler.stats.get_stats(self.spider)
        )

    def test_setting_false_compression_enabled(self):
        with pytest.raises(NotConfigured):
            HttpCompressionMiddleware.from_crawler(
                get_crawler(settings_dict={"COMPRESSION_ENABLED": False})
            )

    def test_setting_default_compression_enabled(self):
        assert isinstance(
            HttpCompressionMiddleware.from_crawler(get_crawler()),
            HttpCompressionMiddleware,
        )

    def test_setting_true_compression_enabled(self):
        assert isinstance(
            HttpCompressionMiddleware.from_crawler(
                get_crawler(settings_dict={"COMPRESSION_ENABLED": True})
            ),
            HttpCompressionMiddleware,
        )

    def test_process_request(self):
        request = Request("http://scrapytest.org")
        assert "Accept-Encoding" not in request.headers
        self.mw.process_request(request, self.spider)
        assert request.headers.get("Accept-Encoding") == b", ".join(ACCEPTED_ENCODINGS)

    def test_process_response_gzip(self):
        response = self._getresponse("gzip")
        request = response.request

        assert response.headers["Content-Encoding"] == b"gzip"
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert newresponse.body.startswith(b"<!DOCTYPE")
        assert "Content-Encoding" not in newresponse.headers
        self.assertStatsEqual("httpcompression/response_count", 1)
        self.assertStatsEqual("httpcompression/response_bytes", 74837)

    def test_process_response_br(self):
        _skip_if_no_br()

        response = self._getresponse("br")
        request = response.request
        assert response.headers["Content-Encoding"] == b"br"
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert newresponse.body.startswith(b"<!DOCTYPE")
        assert "Content-Encoding" not in newresponse.headers
        self.assertStatsEqual("httpcompression/response_count", 1)
        self.assertStatsEqual("httpcompression/response_bytes", 74837)

    def test_process_response_br_unsupported(self):
        try:
            try:
                import brotli  # noqa: F401,PLC0415

                pytest.skip("Requires not having brotli support")
            except ImportError:
                import brotlicffi  # noqa: F401,PLC0415

                pytest.skip("Requires not having brotli support")
        except ImportError:
            pass
        response = self._getresponse("br")
        request = response.request
        assert response.headers["Content-Encoding"] == b"br"
        with LogCapture(
            "scrapy.downloadermiddlewares.httpcompression",
            propagate=False,
            level=WARNING,
        ) as log:
            newresponse = self.mw.process_response(request, response, self.spider)
        log.check(
            (
                "scrapy.downloadermiddlewares.httpcompression",
                "WARNING",
                (
                    "HttpCompressionMiddleware cannot decode the response for"
                    " http://scrapytest.org/ from unsupported encoding(s) 'br'."
                    " You need to install brotli or brotlicffi to decode 'br'."
                ),
            ),
        )
        assert newresponse is not response
        assert newresponse.headers.getlist("Content-Encoding") == [b"br"]

    def test_process_response_zstd(self):
        _skip_if_no_zstd()

        raw_content = None
        for check_key in FORMAT:
            if not check_key.startswith("zstd-"):
                continue
            response = self._getresponse(check_key)
            request = response.request
            assert response.headers["Content-Encoding"] == b"zstd"
            newresponse = self.mw.process_response(request, response, self.spider)
            if raw_content is None:
                raw_content = newresponse.body
            else:
                assert raw_content == newresponse.body
            assert newresponse is not response
            assert newresponse.body.startswith(b"<!DOCTYPE")
            assert "Content-Encoding" not in newresponse.headers

    def test_process_response_zstd_unsupported(self):
        try:
            import zstandard  # noqa: F401,PLC0415

            pytest.skip("Requires not having zstandard support")
        except ImportError:
            pass
        response = self._getresponse("zstd-static-content-size")
        request = response.request
        assert response.headers["Content-Encoding"] == b"zstd"
        with LogCapture(
            "scrapy.downloadermiddlewares.httpcompression",
            propagate=False,
            level=WARNING,
        ) as log:
            newresponse = self.mw.process_response(request, response, self.spider)
        log.check(
            (
                "scrapy.downloadermiddlewares.httpcompression",
                "WARNING",
                (
                    "HttpCompressionMiddleware cannot decode the response for"
                    " http://scrapytest.org/ from unsupported encoding(s) 'zstd'."
                    " You need to install zstandard to decode 'zstd'."
                ),
            ),
        )
        assert newresponse is not response
        assert newresponse.headers.getlist("Content-Encoding") == [b"zstd"]

    def test_process_response_rawdeflate(self):
        response = self._getresponse("rawdeflate")
        request = response.request

        assert response.headers["Content-Encoding"] == b"deflate"
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert newresponse.body.startswith(b"<!DOCTYPE")
        assert "Content-Encoding" not in newresponse.headers
        self.assertStatsEqual("httpcompression/response_count", 1)
        self.assertStatsEqual("httpcompression/response_bytes", 74840)

    def test_process_response_zlibdelate(self):
        response = self._getresponse("zlibdeflate")
        request = response.request

        assert response.headers["Content-Encoding"] == b"deflate"
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert newresponse.body.startswith(b"<!DOCTYPE")
        assert "Content-Encoding" not in newresponse.headers
        self.assertStatsEqual("httpcompression/response_count", 1)
        self.assertStatsEqual("httpcompression/response_bytes", 74840)

    def test_process_response_plain(self):
        response = Response("http://scrapytest.org", body=b"<!DOCTYPE...")
        request = Request("http://scrapytest.org")

        assert not response.headers.get("Content-Encoding")
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is response
        assert newresponse.body.startswith(b"<!DOCTYPE")
        self.assertStatsEqual("httpcompression/response_count", None)
        self.assertStatsEqual("httpcompression/response_bytes", None)

    def test_multipleencodings(self):
        response = self._getresponse("gzip")
        response.headers["Content-Encoding"] = ["uuencode", "gzip"]
        request = response.request
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert newresponse.headers.getlist("Content-Encoding") == [b"uuencode"]

    def test_multi_compression_single_header(self):
        response = self._getresponse("gzip-deflate")
        request = response.request
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert "Content-Encoding" not in newresponse.headers
        assert newresponse.body.startswith(b"<!DOCTYPE")

    def test_multi_compression_single_header_invalid_compression(self):
        response = self._getresponse("gzip-deflate")
        response.headers["Content-Encoding"] = [b"gzip, foo, deflate"]
        request = response.request
        with LogCapture(
            "scrapy.downloadermiddlewares.httpcompression",
            propagate=False,
            level=WARNING,
        ) as log:
            newresponse = self.mw.process_response(request, response, self.spider)
        log.check(
            (
                "scrapy.downloadermiddlewares.httpcompression",
                "WARNING",
                (
                    "HttpCompressionMiddleware cannot decode the response for"
                    " http://scrapytest.org/ from unsupported encoding(s) 'gzip,foo'."
                ),
            ),
        )
        assert newresponse is not response
        assert newresponse.headers.getlist("Content-Encoding") == [b"gzip", b"foo"]

    def test_multi_compression_multiple_header(self):
        response = self._getresponse("gzip-deflate")
        response.headers["Content-Encoding"] = ["gzip", "deflate"]
        request = response.request
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert "Content-Encoding" not in newresponse.headers
        assert newresponse.body.startswith(b"<!DOCTYPE")

    def test_multi_compression_multiple_header_invalid_compression(self):
        response = self._getresponse("gzip-deflate")
        response.headers["Content-Encoding"] = ["gzip", "foo", "deflate"]
        request = response.request
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert newresponse.headers.getlist("Content-Encoding") == [b"gzip", b"foo"]

    def test_multi_compression_single_and_multiple_header(self):
        response = self._getresponse("gzip-deflate-gzip")
        response.headers["Content-Encoding"] = ["gzip", "deflate, gzip"]
        request = response.request
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert "Content-Encoding" not in newresponse.headers
        assert newresponse.body.startswith(b"<!DOCTYPE")

    def test_multi_compression_single_and_multiple_header_invalid_compression(self):
        response = self._getresponse("gzip-deflate")
        response.headers["Content-Encoding"] = ["gzip", "foo,deflate"]
        request = response.request
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert newresponse.headers.getlist("Content-Encoding") == [b"gzip", b"foo"]

    def test_process_response_encoding_inside_body(self):
        headers = {
            "Content-Type": "text/html",
            "Content-Encoding": "gzip",
        }
        f = BytesIO()
        plainbody = (
            b"<html><head><title>Some page</title>"
            b'<meta http-equiv="Content-Type" content="text/html; charset=gb2312">'
        )
        zf = GzipFile(fileobj=f, mode="wb")
        zf.write(plainbody)
        zf.close()
        response = Response(
            "http;//www.example.com/", headers=headers, body=f.getvalue()
        )
        request = Request("http://www.example.com/")

        newresponse = self.mw.process_response(request, response, self.spider)
        assert isinstance(newresponse, HtmlResponse)
        assert newresponse.body == plainbody
        assert newresponse.encoding == resolve_encoding("gb2312")
        self.assertStatsEqual("httpcompression/response_count", 1)
        self.assertStatsEqual("httpcompression/response_bytes", len(plainbody))

    def test_process_response_force_recalculate_encoding(self):
        headers = {
            "Content-Type": "text/html",
            "Content-Encoding": "gzip",
        }
        f = BytesIO()
        plainbody = (
            b"<html><head><title>Some page</title>"
            b'<meta http-equiv="Content-Type" content="text/html; charset=gb2312">'
        )
        zf = GzipFile(fileobj=f, mode="wb")
        zf.write(plainbody)
        zf.close()
        response = HtmlResponse(
            "http;//www.example.com/page.html", headers=headers, body=f.getvalue()
        )
        request = Request("http://www.example.com/")

        newresponse = self.mw.process_response(request, response, self.spider)
        assert isinstance(newresponse, HtmlResponse)
        assert newresponse.body == plainbody
        assert newresponse.encoding == resolve_encoding("gb2312")
        self.assertStatsEqual("httpcompression/response_count", 1)
        self.assertStatsEqual("httpcompression/response_bytes", len(plainbody))

    def test_process_response_no_content_type_header(self):
        headers = {
            "Content-Encoding": "identity",
        }
        plainbody = (
            b"<html><head><title>Some page</title>"
            b'<meta http-equiv="Content-Type" content="text/html; charset=gb2312">'
        )
        respcls = responsetypes.from_args(
            url="http://www.example.com/index", headers=headers, body=plainbody
        )
        response = respcls(
            "http://www.example.com/index", headers=headers, body=plainbody
        )
        request = Request("http://www.example.com/index")

        newresponse = self.mw.process_response(request, response, self.spider)
        assert isinstance(newresponse, respcls)
        assert newresponse.body == plainbody
        assert newresponse.encoding == resolve_encoding("gb2312")
        self.assertStatsEqual("httpcompression/response_count", 1)
        self.assertStatsEqual("httpcompression/response_bytes", len(plainbody))

    def test_process_response_gzipped_contenttype(self):
        response = self._getresponse("gzip")
        response.headers["Content-Type"] = "application/gzip"
        request = response.request

        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert newresponse.body.startswith(b"<!DOCTYPE")
        assert "Content-Encoding" not in newresponse.headers
        self.assertStatsEqual("httpcompression/response_count", 1)
        self.assertStatsEqual("httpcompression/response_bytes", 74837)

    def test_process_response_gzip_app_octetstream_contenttype(self):
        response = self._getresponse("gzip")
        response.headers["Content-Type"] = "application/octet-stream"
        request = response.request

        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert newresponse.body.startswith(b"<!DOCTYPE")
        assert "Content-Encoding" not in newresponse.headers
        self.assertStatsEqual("httpcompression/response_count", 1)
        self.assertStatsEqual("httpcompression/response_bytes", 74837)

    def test_process_response_gzip_binary_octetstream_contenttype(self):
        response = self._getresponse("x-gzip")
        response.headers["Content-Type"] = "binary/octet-stream"
        request = response.request

        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is not response
        assert newresponse.body.startswith(b"<!DOCTYPE")
        assert "Content-Encoding" not in newresponse.headers
        self.assertStatsEqual("httpcompression/response_count", 1)
        self.assertStatsEqual("httpcompression/response_bytes", 74837)

    def test_process_response_gzipped_gzip_file(self):
        """Test that a gzip Content-Encoded .gz file is gunzipped
        only once by the middleware, leaving gunzipping of the file
        to upper layers.
        """
        headers = {
            "Content-Type": "application/gzip",
            "Content-Encoding": "gzip",
        }
        # build a gzipped file (here, a sitemap)
        f = BytesIO()
        plainbody = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.google.com/schemas/sitemap/0.84">
  <url>
    <loc>http://www.example.com/</loc>
    <lastmod>2009-08-16</lastmod>
    <changefreq>daily</changefreq>
    <priority>1</priority>
  </url>
  <url>
    <loc>http://www.example.com/Special-Offers.html</loc>
    <lastmod>2009-08-16</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
</urlset>"""
        gz_file = GzipFile(fileobj=f, mode="wb")
        gz_file.write(plainbody)
        gz_file.close()

        # build a gzipped response body containing this gzipped file
        r = BytesIO()
        gz_resp = GzipFile(fileobj=r, mode="wb")
        gz_resp.write(f.getvalue())
        gz_resp.close()

        response = Response(
            "http;//www.example.com/", headers=headers, body=r.getvalue()
        )
        request = Request("http://www.example.com/")

        newresponse = self.mw.process_response(request, response, self.spider)
        assert gunzip(newresponse.body) == plainbody
        self.assertStatsEqual("httpcompression/response_count", 1)
        self.assertStatsEqual("httpcompression/response_bytes", 230)

    def test_process_response_head_request_no_decode_required(self):
        response = self._getresponse("gzip")
        response.headers["Content-Type"] = "application/gzip"
        request = response.request
        request.method = "HEAD"
        response = response.replace(body=None)
        newresponse = self.mw.process_response(request, response, self.spider)
        assert newresponse is response
        assert response.body == b""
        self.assertStatsEqual("httpcompression/response_count", None)
        self.assertStatsEqual("httpcompression/response_bytes", None)

    def _test_compression_bomb_setting(self, compression_id):
        settings = {"DOWNLOAD_MAXSIZE": 10_000_000}
        crawler = get_crawler(Spider, settings_dict=settings)
        spider = crawler._create_spider("scrapytest.org")
        mw = HttpCompressionMiddleware.from_crawler(crawler)
        mw.open_spider(spider)

        response = self._getresponse(f"bomb-{compression_id}")
        with pytest.raises(IgnoreRequest):
            mw.process_response(response.request, response, spider)

    def test_compression_bomb_setting_br(self):
        _skip_if_no_br()

        self._test_compression_bomb_setting("br")

    def test_compression_bomb_setting_deflate(self):
        self._test_compression_bomb_setting("deflate")

    def test_compression_bomb_setting_gzip(self):
        self._test_compression_bomb_setting("gzip")

    def test_compression_bomb_setting_zstd(self):
        _skip_if_no_zstd()

        self._test_compression_bomb_setting("zstd")

    def _test_compression_bomb_spider_attr(self, compression_id):
        class DownloadMaxSizeSpider(Spider):
            download_maxsize = 10_000_000

        crawler = get_crawler(DownloadMaxSizeSpider)
        spider = crawler._create_spider("scrapytest.org")
        mw = HttpCompressionMiddleware.from_crawler(crawler)
        mw.open_spider(spider)

        response = self._getresponse(f"bomb-{compression_id}")
        with pytest.raises(IgnoreRequest):
            mw.process_response(response.request, response, spider)

    def test_compression_bomb_spider_attr_br(self):
        _skip_if_no_br()

        self._test_compression_bomb_spider_attr("br")

    def test_compression_bomb_spider_attr_deflate(self):
        self._test_compression_bomb_spider_attr("deflate")

    def test_compression_bomb_spider_attr_gzip(self):
        self._test_compression_bomb_spider_attr("gzip")

    def test_compression_bomb_spider_attr_zstd(self):
        _skip_if_no_zstd()

        self._test_compression_bomb_spider_attr("zstd")

    def _test_compression_bomb_request_meta(self, compression_id):
        crawler = get_crawler(Spider)
        spider = crawler._create_spider("scrapytest.org")
        mw = HttpCompressionMiddleware.from_crawler(crawler)
        mw.open_spider(spider)

        response = self._getresponse(f"bomb-{compression_id}")
        response.meta["download_maxsize"] = 10_000_000
        with pytest.raises(IgnoreRequest):
            mw.process_response(response.request, response, spider)

    def test_compression_bomb_request_meta_br(self):
        _skip_if_no_br()

        self._test_compression_bomb_request_meta("br")

    def test_compression_bomb_request_meta_deflate(self):
        self._test_compression_bomb_request_meta("deflate")

    def test_compression_bomb_request_meta_gzip(self):
        self._test_compression_bomb_request_meta("gzip")

    def test_compression_bomb_request_meta_zstd(self):
        _skip_if_no_zstd()

        self._test_compression_bomb_request_meta("zstd")

    def _test_download_warnsize_setting(self, compression_id):
        settings = {"DOWNLOAD_WARNSIZE": 10_000_000}
        crawler = get_crawler(Spider, settings_dict=settings)
        spider = crawler._create_spider("scrapytest.org")
        mw = HttpCompressionMiddleware.from_crawler(crawler)
        mw.open_spider(spider)
        response = self._getresponse(f"bomb-{compression_id}")

        with LogCapture(
            "scrapy.downloadermiddlewares.httpcompression",
            propagate=False,
            level=WARNING,
        ) as log:
            mw.process_response(response.request, response, spider)
        log.check(
            (
                "scrapy.downloadermiddlewares.httpcompression",
                "WARNING",
                (
                    "<200 http://scrapytest.org/> body size after "
                    "decompression (11511612 B) is larger than the download "
                    "warning size (10000000 B)."
                ),
            ),
        )

    def test_download_warnsize_setting_br(self):
        _skip_if_no_br()

        self._test_download_warnsize_setting("br")

    def test_download_warnsize_setting_deflate(self):
        self._test_download_warnsize_setting("deflate")

    def test_download_warnsize_setting_gzip(self):
        self._test_download_warnsize_setting("gzip")

    def test_download_warnsize_setting_zstd(self):
        _skip_if_no_zstd()

        self._test_download_warnsize_setting("zstd")

    def _test_download_warnsize_spider_attr(self, compression_id):
        class DownloadWarnSizeSpider(Spider):
            download_warnsize = 10_000_000

        crawler = get_crawler(DownloadWarnSizeSpider)
        spider = crawler._create_spider("scrapytest.org")
        mw = HttpCompressionMiddleware.from_crawler(crawler)
        mw.open_spider(spider)
        response = self._getresponse(f"bomb-{compression_id}")

        with LogCapture(
            "scrapy.downloadermiddlewares.httpcompression",
            propagate=False,
            level=WARNING,
        ) as log:
            mw.process_response(response.request, response, spider)
        log.check(
            (
                "scrapy.downloadermiddlewares.httpcompression",
                "WARNING",
                (
                    "<200 http://scrapytest.org/> body size after "
                    "decompression (11511612 B) is larger than the download "
                    "warning size (10000000 B)."
                ),
            ),
        )

    def test_download_warnsize_spider_attr_br(self):
        _skip_if_no_br()

        self._test_download_warnsize_spider_attr("br")

    def test_download_warnsize_spider_attr_deflate(self):
        self._test_download_warnsize_spider_attr("deflate")

    def test_download_warnsize_spider_attr_gzip(self):
        self._test_download_warnsize_spider_attr("gzip")

    def test_download_warnsize_spider_attr_zstd(self):
        _skip_if_no_zstd()

        self._test_download_warnsize_spider_attr("zstd")

    def _test_download_warnsize_request_meta(self, compression_id):
        crawler = get_crawler(Spider)
        spider = crawler._create_spider("scrapytest.org")
        mw = HttpCompressionMiddleware.from_crawler(crawler)
        mw.open_spider(spider)
        response = self._getresponse(f"bomb-{compression_id}")
        response.meta["download_warnsize"] = 10_000_000

        with LogCapture(
            "scrapy.downloadermiddlewares.httpcompression",
            propagate=False,
            level=WARNING,
        ) as log:
            mw.process_response(response.request, response, spider)
        log.check(
            (
                "scrapy.downloadermiddlewares.httpcompression",
                "WARNING",
                (
                    "<200 http://scrapytest.org/> body size after "
                    "decompression (11511612 B) is larger than the download "
                    "warning size (10000000 B)."
                ),
            ),
        )

    def test_download_warnsize_request_meta_br(self):
        _skip_if_no_br()

        self._test_download_warnsize_request_meta("br")

    def test_download_warnsize_request_meta_deflate(self):
        self._test_download_warnsize_request_meta("deflate")

    def test_download_warnsize_request_meta_gzip(self):
        self._test_download_warnsize_request_meta("gzip")

    def test_download_warnsize_request_meta_zstd(self):
        _skip_if_no_zstd()

        self._test_download_warnsize_request_meta("zstd")
