import typing
from enum import Enum, auto
from dataclasses import dataclass

from kodiak import config
from kodiak.queries import PullRequest, PullRequestState, MergeStateStatus, RepoInfo
import structlog

log = structlog.get_logger()


class MergeErrors(str, Enum):
    MISSING_WHITELIST_LABEL = auto()
    MISSING_BLACKLIST_LABEL = auto()
    PR_MERGED = auto()
    PR_CLOSED = auto()
    # there are unsuccessful checks
    UNSTABLE_MERGE = auto()
    DRAFT = auto()
    DIRTY = auto()
    BLOCKED = auto()
    UNEXPECTED_VALUE = auto()


async def valid_merge_methods(cfg: config.V1, repo: RepoInfo) -> bool:
    if cfg.merge.method == config.MergeMethod.merge:
        return repo.merge_commit_allowed
    if cfg.merge.method == config.MergeMethod.squash:
        return repo.squash_merge_allowed
    if cfg.merge.method == config.MergeMethod.rebase:
        return repo.rebase_merge_allowed
    raise TypeError("Unknown value")


class MergabilityException(Exception):
    pass


@dataclass
class NotMergable(MergabilityException):
    reasons: typing.Optional[typing.List[MergeErrors]]


class NeedsUpdate(MergabilityException):
    pass


class CheckMergability(MergabilityException):
    pass


# TOOD: We can probably extend that to display a status check on the PR (is
# there a risk for a loop there?)
def evaluate_mergability(config: config.V1, pull_request: PullRequest) -> None:
    """
    Process a PR to potentially be merged

    A PR is able to be merged if:

    1. Labeled with `AUTOMERGE_LABEL`
    2. Rrequired statuses and check runs are successful
    3. PR has required approvals
    4. PR is up-to-date with target. If this last case fails, we will place the
       PR on the queue for serial integration into target.
    """
    problems: typing.List[MergeErrors] = []
    behind_target = False
    unknown_mergability = False

    # TODO: Evaluate merge method viability
    pr_log = log.bind(
        labels=pull_request.labels,
        state=pull_request.state,
        merge_state_status=pull_request.mergeStateStatus,
    )

    # If we don't have a whitelist, we continue.
    if config.merge.whitelist:
        has_label = any(
            True
            for label in config.merge.whitelist
            if label in set(pull_request.labels)
        )
        # if we don't have a label, ensure the PR is not enqueued and return
        if not has_label:
            problems.append(MergeErrors.MISSING_WHITELIST_LABEL)
    # If we have any blacklist label, we should stop
    if config.merge.blacklist:
        has_label = any(
            True
            for label in config.merge.whitelist
            if label in set(pull_request.labels)
        )
        if has_label:
            problems.append(MergeErrors.MISSING_BLACKLIST_LABEL)

    if pull_request.state == PullRequestState.MERGED:
        problems.append(MergeErrors.PR_MERGED)
    if pull_request.state == PullRequestState.CLOSED:
        problems.append(MergeErrors.PR_CLOSED)

    # If unknown, we should probably retry
    if (
        pull_request.mergeStateStatus == MergeStateStatus.UNKNOWN
        and pull_request.state != PullRequestState.MERGED
    ):
        # we need to trigger a test commit to fix this. We do that by calling
        # GET on the pull request endpoint.
        unknown_mergability = True
    if pull_request.mergeStateStatus == MergeStateStatus.BEHIND:
        # mark PR was needing merge
        behind_target = True
    if pull_request.mergeStateStatus == MergeStateStatus.UNSTABLE:
        # unstable means we have in-progress/failing statuses
        problems.append(MergeErrors.UNSTABLE_MERGE)
    if pull_request.mergeStateStatus == MergeStateStatus.DRAFT:
        problems.append(MergeErrors.DRAFT)
    if pull_request.mergeStateStatus in (
        MergeStateStatus.DIRTY,
        MergeStateStatus.BLOCKED,
    ):
        # TODO: Add comment to PR explaining that PR cannot be merged. Remove automerge label.
        problems = problems + [MergeErrors.DIRTY, MergeErrors.BLOCKED]
    if behind_target:
        raise NeedsUpdate()
    if unknown_mergability:
        raise CheckMergability()

    if problems:
        raise NotMergable(reasons=problems)

    assert pull_request.mergeStateStatus in (
        MergeStateStatus.CLEAN,
        MergeStateStatus.HAS_HOOKS,
    ), "sanity check for if we update MergeStateStatus or Github does. This indicates a programming error."
    return None