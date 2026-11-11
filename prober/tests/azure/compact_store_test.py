"""BlobCompactStore (ADR 0025), tested against the real azure-storage-blob
ContainerClient. Only the HTTP transport is a stub: it answers with the wire shape of the Blob
service REST API, so the SDK builds the requests and parses the responses as in production
(a fake must match the real thing): List Blobs returns XML with a NextMarker, Get Blob returns the
bytes, Put Blob returns 201. The test checks the requests the SDK sent."""

import io
import urllib.parse
from datetime import UTC, datetime
from email.utils import format_datetime

import pytest

from ftg_prober.azure.compact_store import BlobCompactStore

EXP = "experiment=ftg-20261027-x"
RAW = f"{EXP}/provider=azure/probe_type=spot_price/date=2026-10-26/"
DAILY = f"{EXP}/daily/provider=azure/probe_type=spot_price/date=2026-10-26/"
HTTP_DATE = format_datetime(datetime(2026, 10, 26, 12, 0, tzinfo=UTC), usegmt=True)


def _xml_list(names_sizes, next_marker=""):
    blobs = "".join(
        f"<Blob><Name>{name}</Name><Properties><Creation-Time>{HTTP_DATE}</Creation-Time>"
        f"<Last-Modified>{HTTP_DATE}</Last-Modified><Etag>&quot;0x8DC{name[-7]}&quot;</Etag>"
        f"<Content-Length>{size}</Content-Length><Content-Type>application/json</Content-Type>"
        "<BlobType>BlockBlob</BlobType><AccessTier>Hot</AccessTier>"
        "<ServerEncrypted>true</ServerEncrypted></Properties><OrMetadata /></Blob>"
        for name, size in names_sizes
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<EnumerationResults ServiceEndpoint="https://acct.blob.core.windows.net/" '
        'ContainerName="data"><Prefix></Prefix><Marker /><Blobs>'
        f"{blobs}</Blobs><NextMarker>{next_marker}</NextMarker></EnumerationResults>"
    ).encode()


def _response(request, status, body=b"", headers=None):
    import requests
    import urllib3
    from azure.core.rest._requests_basic import RestRequestsTransportResponse

    response = requests.Response()
    response.status_code = status
    response.raw = urllib3.response.HTTPResponse(
        body=io.BytesIO(body), preload_content=False, status=status
    )
    response.headers.update(
        {
            "x-ms-request-id": "r",
            "x-ms-version": "2025-01-05",
            "Date": HTTP_DATE,
            "Content-Length": str(len(body)),
            **(headers or {}),
        }
    )
    response.reason = "stub"
    return RestRequestsTransportResponse(request=request, internal_response=response)


class StubTransport:
    def __new__(cls, pages=(), blobs=None):
        from azure.core.pipeline.transport import HttpTransport

        class Transport(HttpTransport):
            def __init__(self):
                self.sent = []  # (method, url path, query dict, headers, body)
                self.pages = list(pages)
                self.blobs = dict(blobs or {})

            def open(self):
                pass

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def send(self, request, **kw):
                url = urllib.parse.urlsplit(request.url)
                query = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
                body = request.data
                if hasattr(body, "read"):
                    body = body.read()
                self.sent.append((request.method, url.path, query, dict(request.headers), body))
                if request.method == "GET" and query.get("comp") == "list":
                    # The real service echoes the request prefix in <Prefix>, and the SDK sends
                    # that echoed value with the next-page request.
                    xml = self.pages.pop(0).replace(
                        b"<Prefix></Prefix>", f"<Prefix>{query.get('prefix', '')}</Prefix>".encode()
                    )
                    return _response(request, 200, xml, {"Content-Type": "application/xml"})
                if request.method == "GET":
                    name = urllib.parse.unquote(url.path.split("/", 2)[2])
                    data = self.blobs[name]
                    # The SDK asks for a range, and the service answers 206 with Content-Range.
                    return _response(
                        request,
                        206,
                        data,
                        {
                            "Content-Range": f"bytes 0-{len(data) - 1}/{len(data)}",
                            "Content-Type": "application/octet-stream",
                            "ETag": '"0x8DC"',
                            "Last-Modified": HTTP_DATE,
                            "x-ms-blob-type": "BlockBlob",
                            "Accept-Ranges": "bytes",
                        },
                    )
                if request.method == "PUT":
                    return _response(
                        request,
                        201,
                        b"",
                        {
                            "ETag": '"0x8DD"',
                            "Last-Modified": HTTP_DATE,
                            "x-ms-request-server-encrypted": "true",
                            "Content-MD5": "1B2M2Y8AsgTpgAmY7PhCfg==",
                        },
                    )
                raise AssertionError(f"unexpected request {request.method} {request.url}")

        return Transport()


def _store(transport, prefix=""):
    from azure.storage.blob import ContainerClient

    client = ContainerClient(
        account_url="https://acct.blob.core.windows.net",
        container_name="data",
        credential=None,
        transport=transport,
    )
    return BlobCompactStore("acct", "data", prefix, client=client)


def test_list_follows_every_page_and_sends_the_prefix():
    transport = StubTransport(
        pages=[
            _xml_list([(RAW + "a.jsonl", 10), (RAW + "b.jsonl", 20)], next_marker="m2"),
            _xml_list([(RAW + "c.jsonl", 30)]),
        ]
    )
    got = list(_store(transport).list(RAW))
    assert [(k, n) for k, n in got] == [
        (RAW + "a.jsonl", 10),
        (RAW + "b.jsonl", 20),
        (RAW + "c.jsonl", 30),
    ]
    assert [(m, q.get("prefix"), q.get("marker")) for m, _, q, _, _ in transport.sent] == [
        ("GET", RAW, None),
        ("GET", RAW, "m2"),
    ]
    # A flat list: every page sends the prefix.
    assert all("delimiter" not in q for _, _, q, _, _ in transport.sent)


def test_list_strips_the_store_prefix():
    transport = StubTransport(pages=[_xml_list([("pre/" + RAW + "a.jsonl", 10)])])
    assert [(k, n) for k, n in _store(transport, "pre").list(RAW)] == [(RAW + "a.jsonl", 10)]
    assert transport.sent[0][2]["prefix"] == "pre/" + RAW


def test_list_of_nothing_is_empty():
    assert list(_store(StubTransport(pages=[_xml_list([])])).list(RAW)) == []


def test_get_returns_the_bytes():
    key = DAILY + "manifest.json"
    transport = StubTransport(blobs={key: b'{"row_count": 3}'})
    got = _store(transport).get(key)
    assert got == b'{"row_count": 3}'
    assert isinstance(got, bytes)
    assert transport.sent[0][0] == "GET"
    assert urllib.parse.unquote(transport.sent[0][1]) == f"/data/{key}"


def test_put_replaces_a_daily_key_and_sets_the_content_type():
    transport = StubTransport()
    key = "experiment=x/daily/v1/date=2026-10-26/spot_2026-10-26.jsonl.zst"
    _store(transport).put(key, b"zzz", "application/zstd")
    ((method, path, _, headers, body),) = transport.sent
    assert method == "PUT"
    assert urllib.parse.unquote(path) == f"/data/{key}"
    assert body == b"zzz"
    assert headers["x-ms-blob-content-type"] == "application/zstd"
    assert headers["x-ms-blob-type"] == "BlockBlob"
    # overwrite=True: no If-None-Match, so a rebuild replaces the file.
    assert "If-None-Match" not in headers


def test_put_refuses_a_key_outside_the_daily_prefix():
    transport = StubTransport()
    for key in (
        f"{RAW}x.jsonl",
        "experiment=x/daily/v2/date=2026-10-26/a.jsonl.zst",
        "experiment=x/daily/provider=azure/a.jsonl",
        "daily/v1/a",
        "experiment=/daily/v1/a",
    ):
        with pytest.raises(ValueError):
            _store(transport).put(key, b"z", "application/zstd")
    assert transport.sent == []


def test_list_with_many_small_pages_returns_everything():
    names = [(f"{RAW}{i:04d}.jsonl", i) for i in range(7)]
    pages = [
        _xml_list(names[k : k + 2], next_marker=f"m{k}" if k + 2 < 7 else "")
        for k in range(0, 7, 2)
    ]
    transport = StubTransport(pages=pages)
    assert [(k, n) for k, n in _store(transport).list(RAW)] == names
    assert len(transport.sent) == 4


def test_the_class_has_no_delete_and_no_other_write():
    public = {n for n in dir(BlobCompactStore) if not n.startswith("_")}
    assert {"get", "list", "put"} <= public
    assert not {n for n in public if any(w in n for w in ("delete", "remove", "upload", "write"))}


def test_from_url():
    store = BlobCompactStore.from_url("az://acct/data/")
    assert (store.account, store.container, store.prefix) == ("acct", "data", "")
    assert BlobCompactStore.from_url("az://acct/data/x/y").prefix == "x/y"
    for bad in ("s3://b", "az://acct", "az:///data", "file:///x"):
        with pytest.raises(ValueError):
            BlobCompactStore.from_url(bad)


def test_store_url_forms():
    assert BlobCompactStore("acct", "data").url == "az://acct/data"
    assert BlobCompactStore("acct", "data", "/x/").url == "az://acct/data/x"


@pytest.mark.parametrize("name", ["list", "get", "put"])
def test_the_fake_calls_exist_on_the_real_container_client(name):
    from azure.storage.blob import ContainerClient

    real = {"list": "list_blobs", "get": "download_blob", "put": "upload_blob"}[name]
    assert hasattr(ContainerClient, real)


def test_open_compact_store_opens_an_az_url():
    from ftg_prober.compact_stores import open_compact_store

    store = open_compact_store("az://acct/data/")
    assert isinstance(store, BlobCompactStore)
    assert store.url == "az://acct/data"
