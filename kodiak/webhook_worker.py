"""
Webhook worker

1. removes webhook payloads from a Redis queue
2. processes the payloads to extract the PRs
3. sends PR information to the repo-specific queue and alerts repo worker supervisor of activity for a specific repo

horizontally scalable
"""

import asyncio
import time
from typing import List

import asyncio_redis

from kodiak import settings
from kodiak.web import WebhookRequest
from kodiak.github.events import event_registry
from kodiak.github import events
import structlog
from kodiak.queue import WebhookEvent

log = structlog.get_logger()


async def get_redis() -> asyncio_redis.Connection:
    # TODO: A connection pool would be nice. The one included with asyncio_redis
    # sucks because it is fixed and doesn't grow.
    try:
        redis_db = int(settings.REDIS_URL.database)
    except ValueError:
        redis_db = 0
    return await asyncio_redis.Connection.create(
        host=settings.REDIS_URL.hostname or "localhost",
        port=settings.REDIS_URL.port or 6379,
        password=settings.REDIS_URL.password or None,
        db=redis_db,
    )


class GitHubAPI:
    @staticmethod
    async def create() -> "GitHubAPI":
        pass

    async def get_pull_requests_for_sha(self, sha: str) -> List[events.BasePullRequest]:
        raise NotImplementedError


def get_repo_queue_key(install_id: str, org: str, repo: str) -> str:
    return f"repo_merge_queue:{install_id}.{org}/{repo}"


async def get_github(owner: str, repo: str, installation_id: str) -> GitHubAPI:
    return await GitHubAPI.create()


async def get_webhook_event(
    *, event: str, payload: dict
) -> List[WebhookEvent]:
    """
    Convert a payload from the queue to our WebhookEvent for processing.

    For some GitHub events we can have updates for multiple pull requests, in
    which case we'll return multiple WebhookEvent's.
    """
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
            log.debug("skipping event trigger by kodiak", event=event)
            return []
        return [
            WebhookEvent(
                repo_owner=check_run_event.repository.owner.login,
                repo_name=check_run_event.repository.name,
                pull_request_number=pr.number,
                installation_id=str(check_run_event.installation.id),
            )
            for pr in check_run_event.check_run.pull_requests
        ]
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
        return [
            WebhookEvent(
                repo_owner=review.repository.owner.login,
                repo_name=review.repository.name,
                pull_request_number=review.pull_request.number,
                installation_id=str(review.installation.id),
            )
        ]
    log.warning("unhandled event", event=event)
    return None


async def process_webhook_payload(msg: str) -> None:
    request = WebhookRequest.parse_raw(msg)
    webhook_events = await get_webhook_event(
        event=request.headers["x-github-event"], payload=request.payload
    )
    if not webhook_events:
        log.debug("no webhook events found for msg", msg=msg)
        return None
    redis = await get_redis()

    repo_merge_queue = get_repo_queue_key(
        # this information for an event will be the same for all results in webhook_events.
        install_id=webhook_events[0].installation_id,
        org=webhook_events[0].repo_owner,
        repo=webhook_events[0].repo_name,
    )

    payload = dict()
    for event in webhook_events:
        payload[event.json()] = time.time()

    # add our events to the specific per-repo sorted set.
    merge_queue_add_fut = redis.zadd(repo_merge_queue, payload, only_if_not_exists=True)
    # update our set of known repos. The supervisor can use this SET for starting workers on boot.
    # TODO(chdsbd): I was thinking we might not need this because we could just
    # wait until we receive a message in the supervisor, but this ignores the
    # fact that we could have active merges going on.
    known_repos_add_fut = redis.sadd(settings.KNOWN_GITHUB_REPOS, repo_merge_queue)
    # send message to supervisor about activity on repo.
    # TODO(chdsbd): We should support multiple kinds of messages, so make this richer.
    supervisor_publish_fut = redis.publish(settings.SUPERVISOR_CHANNEL, repo_merge_queue)
    await asyncio.gather(merge_queue_add_fut, known_repos_add_fut,supervisor_publish_fut)
    # TODO(chdsbd): Add error checks to redis results

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
        # TODO(chdsbd): Test to see what the max amount of in process tasks are
        # and limit our fetches by that amount. We can have some counter that we
        # decrement when we create a task and then we can have a callback on the
        # task to increment it when finished. We ma want to use an
        # asyncio.sempahore so we can simple block until the sempahore is upped.
        webhook_event_json: asyncio_redis.BlockingZPopReply = await redis.bzpopmin(
            [settings.GITHUB_WEBHOOK_QUEUE]
        )
        asyncio.create_task(process_webhook_payload(webhook_event_json.value))
