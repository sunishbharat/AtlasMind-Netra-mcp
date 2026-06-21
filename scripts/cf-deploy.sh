#!/bin/bash
# Build a multi-platform image and deploy to Cloud Foundry.
# Usage: REGISTRY=ghcr.io/sunishbharat ./scripts/cf-deploy.sh
#
# One-time buildx setup required before first run:
#   docker buildx create --name multiarch --driver docker-container --use
#   docker buildx inspect --bootstrap
set -e

REGISTRY="${REGISTRY:?Set REGISTRY env var, e.g. export REGISTRY=ghcr.io/sunishbharat}"
GIT_SHA=$(git rev-parse --short HEAD)
IMAGE="${REGISTRY}/atlasmind-netra-mcp:${GIT_SHA}"

echo "==> Building linux/amd64 + linux/arm64: ${IMAGE}"
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    --tag "${IMAGE}" \
    --tag "${REGISTRY}/atlasmind-netra-mcp:latest" \
    --file docker/Dockerfile \
    --push \
    .
# --push is mandatory for multi-platform builds; buildx cannot --load a
# multi-arch image into the local daemon. Use scripts/docker-build-local.sh
# with --load for single-arch local testing.

echo "==> Generating manifest from template"
TMP_MANIFEST=$(mktemp)
trap 'rm -f "$TMP_MANIFEST"' EXIT
sed "s|DOCKER_IMAGE_TAG|${IMAGE}|g" manifest.yml.template > "$TMP_MANIFEST"

echo "==> Pushing to Cloud Foundry"
cf push atlasmind-netra-mcp -f "$TMP_MANIFEST"
# NOTE: if deploying to OCI only (not CF), stop after the docker buildx build step above.
# cf push requires an active CF session; it will exit non-zero (and abort the script
# via set -e) if you are not logged in. It is not a silent no-op.

echo "==> Done. Image: ${IMAGE}"
