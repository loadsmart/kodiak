"""
1. Remove a repo event from queue
2. calculate mergeability and update GitHub check status
3. merge immediately if configured or add to merge queue if required.
"""

# TODO(chdsbd): Can we move this logic into the webhook_worker? I think we're going to lose out on the dedupe from the sorted set no matter what we do.
