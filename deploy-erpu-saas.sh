#!/bin/sh

# Deploy script for ERPU SaaS
# Usage: ./deploy-erpu-saas.sh <production|staging1>

set -e

# Configuration
readonly API_BASE_URL="${ERPUSAAS_API_URL}"
readonly MAX_WAIT_ATTEMPTS=60
readonly POLL_INTERVAL=5

# Check dependencies
for cmd in curl jq; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "Error: Required command '$cmd' not found" >&2
        exit 1
    fi
done

wait_for_build() {
    local build_id="$1"
    local token="$2"

    [ -z "$build_id" ] && { echo "Error: build_id is empty" >&2; return 1; }

    for i in $(seq 1 "$MAX_WAIT_ATTEMPTS"); do
        response=$(curl --fail -s -H "Authorization: Bearer $token" \
            "$API_BASE_URL/erpusaas/build/${build_id}/status") || {
            echo "✗ Error: HTTP request failed" >&2
            return 3
        }

        state=$(echo "$response" | jq -r '.state')
        display_name=$(echo "$response" | jq -r '.display_name')

        echo "[$i/$MAX_WAIT_ATTEMPTS] $display_name - $state"

        case "$state" in
            running)
                echo "✓ Build is running!"
                return 0
                ;;
            failed)
                echo "✗ Build failed!" >&2
                return 1
                ;;
        esac

        sleep "$POLL_INTERVAL"
    done

    echo "⏱ Timeout: Build did not complete within $((MAX_WAIT_ATTEMPTS * POLL_INTERVAL)) seconds" >&2
    return 2
}

trigger_rebuild() {
    local env="$1"
    curl --fail -s -X POST \
        -H "Authorization: Bearer ${ERPUSAAS_DEPLOY_SECRET}" \
    -F "commit=$GITHUB_SHA" \
    -F "build=$GITHUB_RUN_NUMBER" \
        "$API_BASE_URL/erpusaas/project/${ERPUSAAS_DEPLOY_PROJECT}/${env}/rebuild"
}

# Validate input
if [ $# -ne 1 ]; then
    echo "Usage: $0 <production|staging1>" >&2
    exit 1
fi

environment="$1"
case "$environment" in
    production|staging1) ;;
    *)
        echo "Error: Invalid environment '$environment'. Must be 'production' or 'staging1'" >&2
        exit 1
        ;;
esac

if [ -n "${ERPUSAAS_DEPLOY_SECRET}" ]; then
    echo "ERPU SaaS Deploy to $environment"

    BUILD_ID=$(trigger_rebuild "$environment")
    if [ -z "$BUILD_ID" ]; then
        echo "Error: Failed to trigger rebuild" >&2
        exit 1
    fi

    wait_for_build "$BUILD_ID" "${ERPUSAAS_DEPLOY_SECRET}"
    exit $?
else
    echo "Deploy secret not set. Skipping ERPU SaaS deployment." >&2
    exit 1
fi
