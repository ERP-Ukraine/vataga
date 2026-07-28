#!/bin/sh

# Deploy script for ERPU SaaS
# Usage: ./deploy-erpu-saas.sh <production|staging1>

set -u

readonly MAX_WAIT_ATTEMPTS=60
readonly POLL_INTERVAL=5

DIAG_TMP_DIR=''
LAST_HTTP_STATUS=''
LAST_CURL_EXIT=0
LAST_STATE=''
LAST_DISPLAY_NAME=''

cleanup() {
    if [ -n "$DIAG_TMP_DIR" ] && [ -d "$DIAG_TMP_DIR" ]; then
        rm -rf "$DIAG_TMP_DIR"
    fi
}

trap cleanup EXIT
trap 'cleanup; exit 1' HUP INT TERM

print_response_headers() {
    response_headers_file="$1"

    if [ ! -s "$response_headers_file" ]; then
        printf '%s\n' '(no response headers)'
        return
    fi

    awk '
        {
            line = $0
            sub(/\r$/, "", line)
            separator = index(line, ":")
            if (separator > 0) {
                name = tolower(substr(line, 1, separator - 1))
                if (
                    name ~ /authorization/
                    || name ~ /cookie/
                    || name ~ /credential/
                    || name ~ /password/
                    || name ~ /secret/
                    || name ~ /token/
                    || name ~ /api[-_]key/
                ) {
                    print substr(line, 1, separator) " [REDACTED]"
                } else {
                    print line
                }
            } else {
                print line
            }
        }
    ' "$response_headers_file"
}

print_response_body() {
    response_body_file="$1"

    if [ ! -s "$response_body_file" ]; then
        printf '%s\n' '(empty response body)'
        return
    fi

    if jq -e . "$response_body_file" >/dev/null 2>&1; then
        jq '
            def redact:
                if type == "object" then
                    with_entries(
                        if (
                            .key
                            | test(
                                "(authorization|cookie|credential|password|secret|token|api[-_]?key)";
                                "i"
                            )
                        ) then
                            .value = "[REDACTED]"
                        else
                            .value |= redact
                        end
                    )
                elif type == "array" then
                    map(redact)
                elif type == "string" then
                    gsub(
                        "Bearer[[:space:]]+[^[:space:]]+";
                        "Bearer [REDACTED]"
                    )
                else
                    .
                end;
            redact
        ' "$response_body_file" | jq .
    else
        sed -E \
            -e 's/(Bearer[[:space:]]+)[^[:space:]]+/\1[REDACTED]/g' \
            -e 's/((authorization|credential|password|secret|token|api[-_]?key)[=:][[:space:]]*)[^[:space:],}]+/\1[REDACTED]/Ig' \
            "$response_body_file"
    fi
}

print_top_level_keys() {
    response_body_file="$1"

    printf '%s\n' 'Top-level JSON keys:'
    if jq -e 'type == "object"' "$response_body_file" >/dev/null 2>&1; then
        jq -r 'keys[]' "$response_body_file"
    elif jq -e . "$response_body_file" >/dev/null 2>&1; then
        response_json_type=$(jq -r 'type' "$response_body_file")
        printf '(none; JSON type is %s)\n' "$response_json_type"
    else
        printf '%s\n' '(none; response is not valid JSON)'
    fi
}

print_request_diagnostics() {
    diagnostic_label="$1"
    diagnostic_timestamp="$2"
    diagnostic_http_status="$3"
    diagnostic_headers_file="$4"
    diagnostic_body_file="$5"

    printf '%s\n' "=== $diagnostic_label ==="
    printf 'UTC timestamp: %s\n' "$diagnostic_timestamp"
    printf 'HTTP status: %s\n' "$diagnostic_http_status"
    printf '%s\n' 'Response headers:'
    print_response_headers "$diagnostic_headers_file"
    printf '%s\n' 'Full response body:'
    print_response_body "$diagnostic_body_file"
    print_top_level_keys "$diagnostic_body_file"
}

fetch_status() {
    status_label="$1"
    status_body_file="$2"
    status_headers_file="$3"
    status_timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

    : > "$status_body_file"
    : > "$status_headers_file"

    LAST_CURL_EXIT=0
    LAST_HTTP_STATUS=$(
        curl \
            --silent \
            --show-error \
            --output "$status_body_file" \
            --dump-header "$status_headers_file" \
            --write-out '%{http_code}' \
            -H "Authorization: Bearer ${ERPUSAAS_DEPLOY_SECRET}" \
            "${API_BASE_URL}/erpusaas/build/${BUILD_ID}/status"
    ) || LAST_CURL_EXIT=$?

    print_request_diagnostics \
        "$status_label" \
        "$status_timestamp" \
        "${LAST_HTTP_STATUS:-000}" \
        "$status_headers_file" \
        "$status_body_file"

    LAST_STATE=''
    LAST_DISPLAY_NAME=''
    if jq -e 'type == "object"' "$status_body_file" >/dev/null 2>&1; then
        LAST_STATE=$(
            jq -r '
                (.state // "")
                | if type == "string" then . else tostring end
            ' "$status_body_file"
        )
        LAST_DISPLAY_NAME=$(
            jq -r '
                (.display_name // "")
                | if type == "string" then . else tostring end
            ' "$status_body_file"
        )
    fi

    printf 'state: %s\n' "${LAST_STATE:-<missing>}"
    printf 'display_name: %s\n' "${LAST_DISPLAY_NAME:-<missing>}"

    if [ "$LAST_CURL_EXIT" -ne 0 ]; then
        printf 'HTTP request failed; curl exit code: %s\n' \
            "$LAST_CURL_EXIT" >&2
        return 3
    fi

    case "$LAST_HTTP_STATUS" in
        2??)
            return 0
            ;;
        *)
            printf 'HTTP request returned status %s.\n' \
                "${LAST_HTTP_STATUS:-000}" >&2
            return 3
            ;;
    esac
}

run_failed_rechecks() {
    failed_recheck=1

    printf '%s\n' \
        'Build reported failed; waiting 5 seconds before three rechecks.'
    sleep 5

    while [ "$failed_recheck" -le 3 ]; do
        failed_body_file="${DIAG_TMP_DIR}/failed-recheck-${failed_recheck}.body"
        failed_headers_file="${DIAG_TMP_DIR}/failed-recheck-${failed_recheck}.headers"

        fetch_status \
            "Failed status recheck ${failed_recheck}/3" \
            "$failed_body_file" \
            "$failed_headers_file"
        failed_recheck_result=$?
        if [ "$failed_recheck_result" -ne 0 ]; then
            printf 'Failed status recheck %s returned HTTP diagnostic code %s.\n' \
                "$failed_recheck" \
                "$failed_recheck_result" >&2
        fi

        failed_recheck=$((failed_recheck + 1))
    done
}

wait_for_build() {
    attempt=1

    while [ "$attempt" -le "$MAX_WAIT_ATTEMPTS" ]; do
        status_body_file="${DIAG_TMP_DIR}/status-${attempt}.body"
        status_headers_file="${DIAG_TMP_DIR}/status-${attempt}.headers"

        fetch_status \
            "Status poll ${attempt}/${MAX_WAIT_ATTEMPTS}" \
            "$status_body_file" \
            "$status_headers_file"
        status_result=$?
        if [ "$status_result" -ne 0 ]; then
            return 3
        fi

        case "$LAST_STATE" in
            running)
                printf '%s\n' 'Build is running.'
                return 0
                ;;
            failed)
                printf '%s\n' '=== First failed response repeated ==='
                printf '%s\n' 'Response headers:'
                print_response_headers "$status_headers_file"
                printf '%s\n' 'Full failed response body:'
                print_response_body "$status_body_file"
                print_top_level_keys "$status_body_file"
                run_failed_rechecks
                printf '%s\n' 'Build failed.' >&2
                return 1
                ;;
        esac

        sleep "$POLL_INTERVAL"
        attempt=$((attempt + 1))
    done

    waited_seconds=$((MAX_WAIT_ATTEMPTS * POLL_INTERVAL))
    printf 'Timeout after %s seconds waiting for build %s.\n' \
        "$waited_seconds" \
        "$BUILD_ID" >&2
    return 2
}

for required_command in curl jq mktemp date awk sed printenv; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        printf 'Error: Required command %s was not found.\n' \
            "$required_command" >&2
        exit 1
    fi
done

if [ "$#" -ne 1 ]; then
    printf 'Usage: %s <production|staging1>\n' "$0" >&2
    exit 1
fi

environment="$1"
case "$environment" in
    production|staging1)
        ;;
    *)
        printf 'Error: Invalid environment %s.\n' "$environment" >&2
        exit 1
        ;;
esac

for required_variable in \
    ERPUSAAS_DEPLOY_SECRET \
    ERPUSAAS_DEPLOY_PROJECT \
    ERPUSAAS_API_URL \
    GITHUB_SHA \
    GITHUB_RUN_NUMBER
do
    required_value=$(printenv "$required_variable" 2>/dev/null || :)
    if [ -z "$required_value" ]; then
        printf 'Error: Required environment variable %s is not set.\n' \
            "$required_variable" >&2
        exit 1
    fi
done

readonly API_BASE_URL="${ERPUSAAS_API_URL%/}"

DIAG_TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/erpu-deploy-diag.XXXXXX")
if [ ! -d "$DIAG_TMP_DIR" ]; then
    printf '%s\n' 'Error: Failed to create diagnostic temporary directory.' >&2
    exit 1
fi

trigger_body_file="${DIAG_TMP_DIR}/trigger.body"
trigger_headers_file="${DIAG_TMP_DIR}/trigger.headers"
trigger_timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
: > "$trigger_body_file"
: > "$trigger_headers_file"

printf 'ERPU SaaS deployment to %s\n' "$environment"

trigger_curl_exit=0
trigger_http_status=$(
    curl \
        --silent \
        --show-error \
        --request POST \
        --output "$trigger_body_file" \
        --dump-header "$trigger_headers_file" \
        --write-out '%{http_code}' \
        -H "Authorization: Bearer ${ERPUSAAS_DEPLOY_SECRET}" \
        -F "commit=${GITHUB_SHA}" \
        -F "build=${GITHUB_RUN_NUMBER}" \
        "${API_BASE_URL}/erpusaas/project/${ERPUSAAS_DEPLOY_PROJECT}/${environment}/rebuild"
) || trigger_curl_exit=$?

print_request_diagnostics \
    'Trigger rebuild response' \
    "$trigger_timestamp" \
    "${trigger_http_status:-000}" \
    "$trigger_headers_file" \
    "$trigger_body_file"

if [ "$trigger_curl_exit" -ne 0 ]; then
    printf 'Trigger request failed; curl exit code: %s\n' \
        "$trigger_curl_exit" >&2
    exit 3
fi

case "$trigger_http_status" in
    2??)
        ;;
    *)
        printf 'Trigger request returned HTTP status %s.\n' \
            "${trigger_http_status:-000}" >&2
        exit 3
        ;;
esac

if jq -e 'type == "number" or type == "string"' \
    "$trigger_body_file" >/dev/null 2>&1; then
    BUILD_ID=$(jq -r '.' "$trigger_body_file")
else
    BUILD_ID=$(tr -d '[:space:]' < "$trigger_body_file")
fi

case "$BUILD_ID" in
    ''|*[!0-9]*)
        printf 'Error: Trigger response did not contain a numeric build ID.\n' \
            >&2
        exit 1
        ;;
esac

printf 'Triggered ERPU SaaS build: %s\n' "$BUILD_ID"
printf 'Commit: %s\n' "$GITHUB_SHA"
printf 'Environment: %s\n' "$environment"

wait_for_build
exit "$?"
