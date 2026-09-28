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

diagnostic_request() (
    endpoint="$1"
    shift
    umask 077
    body=$(mktemp) || exit 1
    trap 'rm -f "$body"' EXIT

    rc=0
    http=$(curl --fail-with-body -s -o "$body" \
        -w '%{http_code}' "$@" 2>/dev/null) || rc=$?

    case "$rc" in
        0)  error=none ;;
        3)  error=malformed_url ;;
        5)  error=proxy_resolution_failed ;;
        6)  error=host_resolution_failed ;;
        7)  error=connection_failed ;;
        22) error=http_error ;;
        28) error=timeout ;;
        35) error=tls_handshake_failed ;;
        52) error=empty_reply ;;
        60) error=tls_certificate_verification_failed ;;
        *)  error=other_curl_error ;;
    esac
    printf 'endpoint=%s http_status=%s curl_exit=%s curl_error=%s\n' \
        "$endpoint" "$http" "$rc" "$error" >&2

    if [ "$rc" -ne 0 ]; then
        printf 'body_safe=' >&2
        jq -cs '
            def safe:
                if . == "Unauthorized" or . == "Forbidden"
                   or . == "Not Found" or . == "Internal Server Error"
                   or . == "pending" or . == "running" or . == "failed"
                then . else "[omitted]" end;
            if length == 1 and (.[0] | type) == "object" then
                .[0] | {
                    state: (.state | safe),
                    error: (.error | safe),
                    message: (.message | safe)
                }
            else {body: "[omitted: unexpected response format]"} end
        ' "$body" >&2 2>/dev/null ||
            printf '%s\n' '[omitted: non-JSON response]' >&2
        exit "$rc"
    fi

    cat "$body"
)

wait_for_build() {
    local build_id="$1"
    local token="$2"

    [ -z "$build_id" ] && { echo "Error: build_id is empty" >&2; return 1; }

    for i in $(seq 1 "$MAX_WAIT_ATTEMPTS"); do
        response=$(diagnostic_request build_status \
            -H "Authorization: Bearer $token" \
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
    diagnostic_request trigger_rebuild -X POST \
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
    case "$BUILD_ID" in
        '')         id_shape=empty ;;
        *[!0-9]*)   id_shape=non_numeric ;;
        *)          id_shape=digits_only ;;
    esac
    printf 'trigger_response_shape=%s\n' "$id_shape" >&2
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
