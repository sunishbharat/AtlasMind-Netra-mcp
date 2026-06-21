#!/bin/bash
# Copy user-defined env vars from SOURCE_APP to TARGET_APP.
# Usage: SOURCE_APP=atlasmind-netra-mcp TARGET_APP=atlasmind-netra-mcp-green \
#          ./scripts/cf-copy-env.sh
set -e

SOURCE_APP="${SOURCE_APP:?Set SOURCE_APP}"
TARGET_APP="${TARGET_APP:?Set TARGET_APP}"

echo "==> Copying env vars: ${SOURCE_APP} -> ${TARGET_APP}"

cf env "${SOURCE_APP}" \
    | awk '/^User-Provided:/{found=1; next} found && /^[[:space:]]*$/{exit} found{print}' \
    | while IFS= read -r line; do
        key="${line%%:*}"
        value="${line#*: }"
        key=$(echo "$key" | xargs)
        [ -z "$key" ] && continue
        echo "  ${key}"
        cf set-env "${TARGET_APP}" "${key}" "${value}"
      done

echo "==> Done. Run 'cf restage ${TARGET_APP}' or 'cf start ${TARGET_APP}' to apply."
