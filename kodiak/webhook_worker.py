"""
Webhook worker

1. removes webhook payloads from a Redis queue
2. processes the payloads to extract the PRs
3. sends PR information to the repo-specific queue and alerts repo worker supervisor of activity for a specific repo

horizontally scalable
"""

import asyncio
import time

import asyncio_redis

from kodiak import settings
from kodiak.web import WebhookRequest
from kodiak.github.events import event_registry
from kodiak.github import events
import structlog
from kodiak.queue import WebhookEvent

log = structlog.get_logger()


async def get_redis() -> asyncio_redis.Connection:
    try:
        redis_db = int(settings.REDIS_URL.database)
    except ValueError:
        redis_db = 0
    return await asyncio_redis.Connection.create(
        host=settings.REDIS_URL.hostname or "localhost",
        port=settings.REDIS_URL.port or 6379,
        password=settings.REDIS_URL.password or None,
        db=redis_db,
        poolsize=settings.REDIS_POOL_SIZE,
    )


class GitHubAPI:
    @staticmethod
    async def create() -> "GitHubAPI":
        pass

    async def get_pull_requests_for_sha(self, sha: str) -> List[events.BasePullRequest]:
        raise NotImplementedError


def get_repo_queue_key(install_id: str, org: str, repo: str):
    return f"repo_merge_queue:{install_id}.{org}/{repo}"

async def get_github(owner: str, repo: str, installation_id: str) -> GitHubAPI:
    return await GitHubAPI.create()


async def get_webhook_event(
    *, event: str, payload: dict
) -> Optional[Union[WebhookEvent, List[WebhookEvent]]]:
    if event == "pull_request":
        pr = events.PullRequestEvent.parse_obj(payload)
        assert pr.installation is not None
        return WebhookEvent(
            repo_owner=pr.repository.owner.login,
            repo_name=pr.repository.name,
            pull_request_number=pr.number,
            installation_id=str(pr.installation.id),
        )
    if event == "check_run":
        check_run_event = events.CheckRunEvent.parse_obj(payload)
        assert check_run_event.installation
        # Prevent an infinite loop when we update our check run
        if check_run_event.check_run.name == settings.CHECK_RUN_NAME:
            return
        results = []
        for pr in check_run_event.check_run.pull_requests:
            results.append(
                WebhookEvent(
                    repo_owner=check_run_event.repository.owner.login,
                    repo_name=check_run_event.repository.name,
                    pull_request_number=pr.number,
                    installation_id=str(check_run_event.installation.id),
                )
            )
        return results
    if event == "status_event":
        status_event = events.StatusEvent.parse_obj(payload)
        assert status_event.installation
        sha = status_event.commit.sha
        owner = status_event.repository.owner.login
        repo = status_event.repository.name
        installation_id = str(status_event.installation.id)
        github_api = await get_github(
            owner=owner, repo=repo, installation_id=installation_id
        )
        prs = await github_api.get_pull_requests_for_sha(sha=sha)
        return [
            WebhookEvent(
                repo_owner=owner,
                repo_name=repo,
                pull_request_number=pr.number,
                installation_id=str(installation_id),
            )
            for pr in prs
        ]
    if event == "pull_request_review":
        review = events.PullRequestReviewEvent.parse_obj(payload)
        assert review.installation
        return WebhookEvent(
            repo_owner=review.repository.owner.login,
            repo_name=review.repository.name,
            pull_request_number=review.pull_request.number,
            installation_id=str(review.installation.id),
        )
    log.warning("unhandled event", event=event)
    return None


async def process_webhook_payload(msg: str) -> None:
    request = WebhookRequest.parse_raw(msg)
    maybe_webhook_event = await get_webhook_event(
        event=request.headers["x-github-event"], payload=request.payload
    )
    if maybe_webhook_event is None:
        return None
    if isinstance(maybe_webhook_event, list):
        webhook_events = maybe_webhook_event
    else:
        webhook_events = [maybe_webhook_event]
    if len(webhook_events) == 0:
        return
    redis = await get_redis()

    key = get_repo_queue_key(
        install_id=webhook_events[0].installation_id,
        org=webhook_events[0].repo_owner,
        repo=webhook_events[0].repo_name,
    )

    payload = dict()
    for event in webhook_events:
        payload[event.json()] = time.time()

    # TODO(chdsbd): send message (PUBLISH) to supervisor about repo. Should also update SET storing known repos. The supervisor can use this SET for starting workers on boot.

    # add our events to the specific per-repo sorted set.
    await redis.zadd(key, payload, only_if_not_exists=True)


async def process_webhook_payloads() -> None:
    """
    1. pop webhook event off queue
    2. spin off task to process event

    Since each webhook payload is generally unique it is okay to create a task
    for each payload. Processing the queue slowly will not dedupe requests like
    it would on the per-repo queues.
    """
    redis = await get_redis()
    while True:
        webhook_event_json: asyncio_redis.BlockingZPopReply = await redis.bzpopmin(
            [settings.GITHUB_WEBHOOK_QUEUE]
        )
        asyncio.create_task(process_webhook_payload(webhook_event_json.value))

