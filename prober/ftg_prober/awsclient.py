"""The one place that builds a boto3 client. Spec 0008 section 2.5."""

from typing import Any

# A hung call must not block the single scheduler thread for minutes.
# https://botocore.amazonaws.com/v1/documentation/api/latest/reference/config.html
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 30
DEFAULT_ATTEMPTS = 3


def boto3_client(
    service: str, region: str | None = None, max_attempts: int = DEFAULT_ATTEMPTS
) -> Any:
    import boto3.session
    from botocore.config import Config

    config = Config(
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=READ_TIMEOUT_SECONDS,
        retries={"mode": "standard", "max_attempts": max_attempts},
    )
    # Each client comes from its own Session, never from the shared default session that
    # `boto3.client()` uses: boto3 documents that creating clients from the default session on
    # several threads is not thread-safe. The flush thread of the store log handler and the
    # scheduler thread both create clients.
    # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/clients.html#multithreading-or-multiprocessing-with-clients
    return boto3.session.Session().client(service, region_name=region, config=config)
