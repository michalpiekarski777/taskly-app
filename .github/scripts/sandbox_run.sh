#!/usr/bin/env bash
# Run a command against a checked-out repository inside a throwaway container
# with no network, no environment (so no secrets or runner tokens), no access
# to the runner's file-command files, a read-only root filesystem, no Linux
# capabilities and a time limit.
#
# The repository is mounted read-write at the same absolute path, so virtualenvs
# and node_modules installed on the host keep working, and the runner's tool
# cache (Python and Node from actions/setup-*) is mounted read-only.
#
# Usage: sandbox_run.sh <repo dir> <working dir relative to repo> <command...>
# Optional env: SANDBOX_IMAGE (default: test-agent-sandbox, built from
# .github/sandbox/Dockerfile), SANDBOX_TIMEOUT (seconds, default 600).
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <repo dir> <working dir> <command...>" >&2
  exit 64
fi

repo="$(cd "$1" && pwd -P)"
workdir="$2"
shift 2

case "/${workdir}/" in
  */../* | //*) echo "sandbox_run: working dir must stay inside the repo: ${workdir}" >&2; exit 64 ;;
esac

image="${SANDBOX_IMAGE:-test-agent-sandbox}"
tool_cache="${RUNNER_TOOL_CACHE:-/opt/hostedtoolcache}"
mounts=(--volume "${repo}:${repo}")
if [[ -d "${tool_cache}" ]]; then
  mounts+=(--volume "${tool_cache}:${tool_cache}:ro")
fi

exec docker run --rm --init \
  --network none \
  --user "$(id -u):$(id -g)" \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --read-only \
  --tmpfs /tmp:rw,exec,size=1g \
  --pids-limit 1024 \
  --memory 4g \
  "${mounts[@]}" \
  --workdir "${repo}/${workdir}" \
  --env CI=true \
  --env HOME=/tmp \
  --env TZ=UTC \
  --env LANG=C.UTF-8 \
  --env PATH="${PATH}" \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --env npm_config_cache=/tmp/.npm \
  "${image}" \
  timeout --kill-after=10 "${SANDBOX_TIMEOUT:-600}" "$@"
