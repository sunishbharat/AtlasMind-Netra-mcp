#!/bin/bash
# Build for local architecture only (fast, no push, for dev/testing).
# For a multi-platform release image, push a git tag to trigger CI.
set -e
REGISTRY="${REGISTRY:-ghcr.io/sunishbharat}"
VERSION="${VERSION:-$(git describe --tags --abbrev=0 2>/dev/null || echo dev)}"
docker buildx build \
    --platform "linux/$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')" \
    --tag "${REGISTRY}/atlasmind-netra-mcp:${VERSION}" \
    --file docker/Dockerfile \
    --load \
    .
echo "Built local image: ${REGISTRY}/atlasmind-netra-mcp:${VERSION}"
echo "Run with: docker run -p 8765:8765 --env-file .env ${REGISTRY}/atlasmind-netra-mcp:${VERSION}"
