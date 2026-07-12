#!/bin/sh

# Deploy script for ERPU SaaS
# Usage: ./deploy-erpu-saas.sh <production|staging1>

set -e

# Configuration
readonly API_BASE_URL="${ERPUSAAS_API_URL}"
readonly MAX_WAIT_ATTEMPTS=60
readonly POLL_INTERVAL=5
readonly FAILURE_LOG_LIMIT=12000

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
                echo "$response" | jq . >&2
                print_failure_details "$build_id" "$token" "$response"
                return 1
                ;;
        esac

        sleep "$POLL_INTERVAL"
    done

    echo "⏱ Timeout: Build did not complete within $((MAX_WAIT_ATTEMPTS * POLL_INTERVAL)) seconds" >&2
    return 2
}

print_failure_details() {
    local build_id="$1"
    local token="$2"
    local response="$3"
    local external_build_id
    local path
    local url
    local tmp_file

    external_build_id=$(echo "$response" | jq -r '.external_build_id // empty')
    tmp_file=$(mktemp)

    echo "Failure diagnostics:" >&2
    for path in \
        "erpusaas/build/${build_id}" \
        "erpusaas/build/${build_id}/log" \
        "erpusaas/build/${build_id}/logs" \
        "erpusaas/build/${build_id}/traceback" \
        "erpusaas/build/${build_id}/result" \
        "erpusaas/build/${build_id}/steps" \
        "erpusaas/build/${build_id}/status?include=logs"
    do
        url="$API_BASE_URL/$path"
        echo "--- GET /$path ---" >&2
        curl -sS -L \
            -H "Authorization: Bearer $token" \
            -w "\nHTTP_STATUS:%{http_code}\n" \
            "$url" > "$tmp_file" || true
        head -c "$FAILURE_LOG_LIMIT" "$tmp_file" >&2 || true
        echo >&2
    done

    if [ -n "$external_build_id" ]; then
        for path in \
            "erpusaas/external_build/${external_build_id}" \
            "erpusaas/external_build/${external_build_id}/log" \
            "erpusaas/external_build/${external_build_id}/logs" \
            "erpusaas/external-build/${external_build_id}" \
            "erpusaas/external-build/${external_build_id}/log" \
            "erpusaas/external-build/${external_build_id}/logs"
        do
            url="$API_BASE_URL/$path"
            echo "--- GET /$path ---" >&2
            curl -sS -L \
                -H "Authorization: Bearer $token" \
                -w "\nHTTP_STATUS:%{http_code}\n" \
                "$url" > "$tmp_file" || true
            head -c "$FAILURE_LOG_LIMIT" "$tmp_file" >&2 || true
            echo >&2
        done
    fi

    rm -f "$tmp_file"
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
