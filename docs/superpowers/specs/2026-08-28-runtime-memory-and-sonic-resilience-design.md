# Runtime Memory And Sonic Resilience Design

## Problem

The QA host has 7.44 GiB of memory. Sonic currently uses about 1.7 GiB, while
`midscene-task.service` normally uses about 422 MiB but was twice killed after
reaching 4.63-5.04 GiB RSS. The backend currently accepts 300 MiB JSON bodies,
parses them through multiple in-memory copies, and creates an unbounded request
thread per connection. Sonic Eureka also uses Docker restart policy `no`, so a
single exit leaves the Sonic backend unavailable until manual intervention.

## Decisions

1. Keep the platform deployment boundary: `update-main-server.sh` observes Sonic
   containers but does not start, stop, or recreate them.
2. Limit normal JSON bodies to 64 MiB. Keep the legacy raw report endpoint at
   300 MiB, but stream it atomically to disk instead of buffering it in memory.
3. Bound HTTP request threads to 64 and large in-flight requests to 2. When the
   server is saturated, return a Chinese `503` response instead of allocating
   more memory.
4. Replace per-request AI generation threads with a persisted queue. Two fixed
   workers execute jobs and at most eight job IDs wait in memory. Figma parsing,
   repair, generation, mind-map generation, regeneration, and retry all use the
   same queue. Legacy synchronous Figma/case/YAML generation shares the same two
   heavy-work slots, so it cannot bypass the queue's process-wide execution
   capacity. The API-testing module retains its own authentication-first body
   contract and is not intercepted by this outer limit.
5. Bound and prune process-local caches. Expired Sonic result entries must not
   remain for the lifetime of the Python process.
6. Add lightweight process RSS, peak RSS, thread, request-slot, and background
   queue data to the health response so a future increase is observable before
   OOM.
7. Install a systemd resource guard with `MemoryHigh=2G`, `MemoryMax=3G`, and
   `TasksMax=256`. Values remain overridable at installation for other hosts.
8. Provide a separate, explicit Sonic operations script that changes existing
   Sonic containers to `restart=unless-stopped`. It may start stopped containers
   only when the operator passes `--start-stopped`.

## Failure Handling

- A normal request larger than 64 MiB receives HTTP 413 with the active limit.
- A third concurrent large request receives HTTP 503 and can be retried.
- A ninth waiting background task receives HTTP 503, and the task history shows
  a clear queue-full failure instead of an indefinitely running record.
- On service restart, interrupted running jobs are marked failed with a retry
  instruction; valid pending jobs are restored to the bounded queue. Startup
  scans all persisted active jobs rather than applying the history display limit.
- Cancellation remains authoritative before execution and before a Figma,
  repair, YAML, or mind-map worker writes a terminal result.
- A platform process exceeding 3 GiB is contained and restarted by systemd;
  it cannot consume all host memory and destabilize Sonic.
- A Sonic process exit is recovered by Docker after its restart policy has been
  configured. Platform deployment continues to report, but not mutate, Sonic.

## Verification

- Focused tests cover body limits, streaming writes, bounded request handling,
  cache eviction, deployment resource limits, and Sonic restart policy setup.
- Run backend static checks, Python compilation, shell syntax checks, and
  `git diff --check`.
