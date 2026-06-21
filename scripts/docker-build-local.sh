#!/bin/bash
# Build for local architecture only (fast, no push, for dev/testing).
# For the full multi-platform push, use scripts/cf-deploy.sh or push to main.
set -e
REGISTRY="${REGISTRY:-ghcr.io/sunishbharat}"
docker buildx build \
    --platform "linux/$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')" \
    --tag "${REGISTRY}/atlasmind-netra-mcp:local" \
    --file docker/Dockerfile \
    --load \
    .
echo "Built local image: ${REGISTRY}/atlasmind-netra-mcp:local"
echo "Run with: docker run -p 8765:8765 --env-file .env ${REGISTRY}/atlasmind-netra-mcp:local"
