"""
Repo worker supervisor

- listens for events from Redis and creats repo workers if necessary
- supervises repo workers for failure and restarts them

vertically scalable


Event Loop

- started by supervisor per core. Jobs distributed amongst event loops.


Repo worker task

- started by repo worker supervisor
- blocks on redis queue waiting for event
- marks current PR in redis for safe restart (loads this on start too)
- updates and merges PRs from queue serially
"""