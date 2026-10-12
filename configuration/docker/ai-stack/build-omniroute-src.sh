#!/usr/bin/env bash
# Build the OmniRoute base image from upstream SOURCE with the ACP executor patch.
#
# Why: the published image ships compiled Next.js chunks, which cannot take a new
# executor. So the gateway is built from the pinned upstream tag with
# configuration/omniroute/acp-executor-<tag>.patch applied (generic ACP executor +
# the qoder-acp provider, `qodercli --acp`), using upstream's own Dockerfile and its
# runner-base target - the same stage the published image comes from. The result is
# the local image autoos/omniroute-src:<tag>-acp<n>, which omniroute.Dockerfile
# builds FROM (qodercli, bcryptjs and the vertex guard layer on top).
#
# Idempotent: an image whose label org.autoos.omniroute-src.source matches
# (tag + commit + patch + target + this recipe) is kept; a second run prints
# "skipped". The upstream commit is verified, so a moved tag cannot change the gateway.
#   build-omniroute-src.sh [--dry-run]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER="${AUTOOS_DOCKER:-docker}"
REPO_URL="${AUTOOS_OMNIROUTE_REPO:-https://github.com/diegosouzapw/OmniRoute.git}"
TAG="v3.8.50"
COMMIT="5458026c216f77a3da68ea49152dc33470cfe2cb"
REV="acp1"
IMAGE="autoos/omniroute-src:${TAG#v}-${REV}"
PATCH="$HERE/../../omniroute/acp-executor-${TAG}.patch"
TARGET="runner-base"
LABEL="org.autoos.omniroute-src.source"

DRY=0
case "${1:-}" in
    "") ;;
    --dry-run) DRY=1 ;;
    *) echo "usage: ${0##*/} [--dry-run]" >&2; exit 2 ;;
esac

[[ -f "$PATCH" ]] || { echo "  ! patch missing: $PATCH" >&2; exit 1; }

want="$({ printf 'tag=%s\ncommit=%s\ntarget=%s\nrev=%s\n' "$TAG" "$COMMIT" "$TARGET" "$REV"; cat "$PATCH"; } | sha256sum | cut -d' ' -f1)"
if "$DOCKER" image inspect "$IMAGE" >/dev/null 2>&1; then
    have="$("$DOCKER" image inspect -f "{{index .Config.Labels \"$LABEL\"}}" "$IMAGE" 2>/dev/null || true)"
    if [[ "$have" == "$want" ]]; then echo "  - $IMAGE skipped (up to date)"; exit 0; fi
    what="rebuild $IMAGE (patch or pin changed)"
else
    what="build $IMAGE"
fi
if (( DRY )); then echo "  - would $what"; exit 0; fi

work="$(mktemp -d "${TMPDIR:-/tmp}/autoos-omniroute-src.XXXXXX")"
trap 'rm -rf "$work"' EXIT
echo "  + $what (clone $TAG, apply patch, upstream Dockerfile target $TARGET)"
git clone --quiet --depth 1 --branch "$TAG" "$REPO_URL" "$work/src"
got="$(git -C "$work/src" rev-parse HEAD)"
[[ "$got" == "$COMMIT" ]] || { echo "  ! $TAG is $got, expected $COMMIT - refusing to build" >&2; exit 1; }
# -C1: the registry index lists providers that differ between upstream patch releases.
git -C "$work/src" apply -C1 "$PATCH"
[[ -f "$work/src/open-sse/executors/acp.ts" ]] || { echo "  ! patch did not add the ACP executor" >&2; exit 1; }
"$DOCKER" build --target "$TARGET" --label "$LABEL=$want" -t "$IMAGE" "$work/src" \
    || { echo "  ! building $IMAGE failed" >&2; exit 1; }
