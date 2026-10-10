# Sandbox for running tests from pull requests (see .github/scripts/sandbox_run.sh).
# Same base as the ubuntu-24.04 runner, so Python and Node from the runner's
# tool cache (mounted read-only) run unchanged; only their shared libraries are added.
FROM ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 libsqlite3-0 libreadline8t64 \
    && rm -rf /var/lib/apt/lists/*
