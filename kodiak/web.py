"""
An http server to accept webhook events from GitHub and add them to a Redis Queue.

This server allows us to shutdown the Kodiak workers without losing events. This
should be deployed separately from Kodiak web workers.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import sys
from typing import Dict, Optional
import time

import sentry_sdk
import structlog
import asyncio
from fastapi import Body, FastAPI, Header, Depends
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.integrations.logging import LoggingIntegration
from starlette import status
from starlette.requests import Request
from starlette.responses import Response

from kodiak import settings
from kodiak.logging import SentryProcessor
from kodiak.errors import HTTPBadRequest
from pydantic import BaseModel
import json
import redis

# for info on logging formats see: https://docs.python.org/3/library/logging.html#logrecord-attributes
logging.basicConfig(
    stream=sys.stdout,
    level=settings.LOGGING_LEVEL,
    format="%(levelname)s %(name)s:%(filename)s:%(lineno)d %(message)s",
)
sentry_sdk.init(
    send_default_pii=True,
    integrations=[LoggingIntegration(level=None, event_level=None)],
)
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        SentryProcessor(level=logging.WARNING),
        structlog.processors.KeyValueRenderer(key_order=["event"], sort_keys=True),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
app = FastAPI()
app.add_middleware(SentryAsgiMiddleware)


_redis_connection_pool: Optional[redis.ConnectionPool] = None


def get_redis() -> redis.Redis:
    """
    Get a connection to redis
    """
    global _redis_connection_pool
    if _redis_connection_pool is None:
        _redis_connection_pool = redis.ConnectionPool.from_url(str(settings.REDIS_URL))
    return redis.Redis(connection_pool=_redis_connection_pool)


class WebhookRequest(BaseModel):
    payload: dict
    headers: dict


@app.get("/")
async def root() -> Response:
    return Response("OK", media_type="text/plain")


@app.get("/github/webhook_event")
def github_webhook_event(
    *,
    request: Request,
    x_github_event: Optional[str] = Header(None),
    x_hub_signature: Optional[str] = Header(None),
    redis: redis.Redis = Depends(get_redis)
) -> Response:
    """
    Entrypoint for GitHub webhooks into Kodiak.

    Valid payloads are added to the webhook event queue.
    """
    body = asyncio.run(request.body())

    if x_github_event is None:
        raise HTTPBadRequest("Missing required header: X-Github-Event")
    if x_hub_signature is None:
        raise HTTPBadRequest("Missing required header: X-Hub-Signature")
    expected_sha = hmac.new(
        key=settings.SECRET_KEY.encode(), msg=body, digestmod=hashlib.sha1
    ).hexdigest()
    if not hmac.compare_digest(x_hub_signature, expected_sha):
        raise HTTPBadRequest("Invalid signature: X-Hub-Signature")

    redis.zadd(
        settings.GITHUB_WEBHOOK_QUEUE,
        {
            WebhookRequest(
                payload=json.loads(body), headers=dict(request.headers)
            ).json(): time.time()
        },
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)
