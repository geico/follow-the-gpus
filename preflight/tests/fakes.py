"""Fakes that replay recorded responses. No network, no credentials."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from botocore.exceptions import ClientError

from ftg_preflight.azure import AzError

FIXTURES = Path(__file__).parent / "fixtures"


def recorded(cloud: str, key: str) -> Any:
    data = json.loads((FIXTURES / f"{cloud}.json").read_text(encoding="utf-8"))
    return copy.deepcopy(data[key])


def aws_reply(key: str) -> Any:
    """A recorded AWS response, or a ClientError for an entry with an Error part."""
    body = recorded("aws", key)
    if "Error" in body:
        return ClientError(body, key)
    return body


class FakeClient:
    """A boto3-style client. `replies` maps a method name to a reply or to a callable."""

    def __init__(self, replies: dict[str, Any]):
        self.replies = replies
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, method: str) -> Any:
        if method not in self.replies:
            raise AttributeError(method)

        def call(**kwargs: Any) -> Any:
            self.calls.append((method, kwargs))
            reply = self.replies[method]
            if callable(reply) and not isinstance(reply, Exception):
                reply = reply(**kwargs)
            if isinstance(reply, Exception):
                raise reply
            return reply

        return call

    def get_paginator(self, method: str) -> Any:
        client = self

        class Paginator:
            def paginate(self, **kwargs: Any) -> list[Any]:
                return [getattr(client, method)(**kwargs)]

        return Paginator()


class FakeAz:
    """Replays az CLI output. `replies` maps a key to the parsed JSON, or to an AzError."""

    def __init__(self, replies: dict[str, Any]):
        self.replies = replies
        self.calls: list[tuple[str, ...]] = []

    @staticmethod
    def key(args: tuple[str, ...]) -> str:
        if args[0] == "rest":
            url = unquote(args[args.index("--url") + 1])
            if "/Microsoft.Compute/skus" in url:
                region = re.search(r"location eq '([^']+)'", url).group(1)
                return f"rest skus {region}"
            region = url.split("/locations/")[1].split("/")[0]
            return f"rest quota {region}" + (" page2" if "page=2" in url else "")
        if "--location" in args:
            return f"{args[0]} {args[1]} {args[args.index('--location') + 1]}"
        return " ".join(args[:3]) if args[0] in ("role", "network") else " ".join(args[:2])

    def __call__(self, *args: str) -> Any:
        self.calls.append(args)
        reply = self.replies[self.key(args)]
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)


def az_error(message: str, code: str = "") -> AzError:
    return AzError(message, code)
