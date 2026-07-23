#!/bin/sh

# Deploy script for ERPU SaaS
# Usage: ./deploy-erpu-saas.sh <production|staging1>

set -u

readonly MAX_WAIT_ATTEMPTS=60
readonly POLL_INTERVAL=5

BUILD_ID=''
FINAL_STATE='not_started'
LAST_DISPLAY_NAME=''
LAST_LOGS_URL=''
SUMMARY_REASON=''
API_RESPONSE=''

sanitize_summary_value() {
    printf '%s' "$1" | tr '\r\n' '  ' | sed 's/|/\\|/g'
}

write_job_summary() {
    [ -n "${GITHUB_STEP_SUMMARY:-}" ] || return 0

    summary_environment=$(sanitize_summary_value "${environment:-unknown}")
    summary_commit=$(sanitize_summary_value "${GITHUB_SHA:-unknown}")
    summary_build_id=$(sanitize_summary_value "${BUILD_ID:-unknown}")
    summary_state=$(sanitize_summary_value "${FINAL_STATE:-unknown}")
    summary_logs_url=$(sanitize_summary_value "${LAST_LOGS_URL:-}")
    summary_reason=$(sanitize_summary_value "${SUMMARY_REASON:-}")

    {
        printf '### ERPU SaaS deployment\n\n'
        printf '| Field | Value |\n'
        printf '|---|---|\n'
        printf '| Environment | %s |\n' "$summary_environment"
        printf '| Commit | %s |\n' "$summary_commit"
        printf '| Build ID | %s |\n' "$summary_build_id"
        printf '| Final state | %s |\n' "$summary_state"
        if [ -n "$summary_logs_url" ]; then
            printf '| External logs | %s |\n' "$summary_logs_url"
        fi
        if [ -n "$summary_reason" ]; then
            printf '| Details | %s |\n' "$summary_reason"
        fi
    } >> "$GITHUB_STEP_SUMMARY"
}

print_safe_diagnostics() {
    printf '%s' "$API_RESPONSE" | jq -c --arg build_id "$BUILD_ID" '
        def redact:
            if type == "object" then
                with_entries(
                    if (
                        .key
                        | test(
                            "(token|secret|password|authorization|credential|api[_-]?key)";
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
        {
            build_id: $build_id,
            state: (.state // null),
            display_name: (.display_name // null),
            message: (.message // null),
            error: (.error // null),
            details: ((.details // null) | redact),
            logs_url: (.logs_url // null),
            url: (.url // null)
        }
    '
}

set_response_fields() {
    LAST_STATE=$(
        printf '%s' "$API_RESPONSE" |
            jq -r '.state'
    )
    LAST_DISPLAY_NAME=$(
        printf '%s' "$API_RESPONSE" |
            jq -r '
                (.display_name // "")
                | if type == "string" then . else tostring end
                | gsub("[\r\n\t]"; " ")
            '
    )
    LAST_MESSAGE=$(
        printf '%s' "$API_RESPONSE" |
            jq -r '
                (.message // "")
                | if type == "string" then . else tostring end
                | gsub("[\r\n\t]"; " ")
            '
    )
    LAST_ERROR=$(
        printf '%s' "$API_RESPONSE" |
            jq -r '
                (.error // "")
                | if type == "string" then . else tostring end
                | gsub("[\r\n\t]"; " ")
            '
    )
    LAST_LOGS_URL=$(
        printf '%s' "$API_RESPONSE" |
            jq -r '
                (.logs_url // .url // "")
                | if type == "string" then . else tostring end
                | gsub("[\r\n\t]"; "")
            '
    )
}

wait_for_build() {
    attempt=1
    LAST_STATE=''

    while [ "$attempt" -le "$MAX_WAIT_ATTEMPTS" ]; do
        if ! API_RESPONSE=$(
            curl \
                --silent \
                --show-error \
                --fail-with-body \
                -H "Authorization: Bearer ${ERPUSAAS_DEPLOY_SECRET}" \
                "${API_BASE_URL}/erpusaas/build/${BUILD_ID}/status"
        ); then
            FINAL_STATE='http_error'
            SUMMARY_REASON='ERPU SaaS status request failed.'
            printf 'Error: ERPU SaaS status request failed for build %s.\n' \
                "$BUILD_ID" >&2
            if printf '%s' "$API_RESPONSE" | jq -e 'type == "object"' \
                >/dev/null 2>&1; then
                print_safe_diagnostics >&2
            else
                printf '%s\n' \
                    'Response body omitted because it is not valid JSON.' \
                    >&2
            fi
            write_job_summary
            return 3
        fi

        if ! printf '%s' "$API_RESPONSE" |
            jq -e '
                type == "object"
                and (.state | type == "string" and length > 0)
            ' >/dev/null 2>&1; then
            FINAL_STATE='invalid_response'
            SUMMARY_REASON='Status API returned invalid JSON or no state.'
            printf 'Error: Invalid status response for build %s.\n' \
                "$BUILD_ID" >&2
            if printf '%s' "$API_RESPONSE" | jq -e 'type == "object"' \
                >/dev/null 2>&1; then
                print_safe_diagnostics >&2
            else
                printf '%s\n' \
                    'Response body omitted because it is not valid JSON.' \
                    >&2
            fi
            write_job_summary
            return 1
        fi

        set_response_fields
        printf '[%s/%s] build=%s state=%s name="%s"\n' \
            "$attempt" \
            "$MAX_WAIT_ATTEMPTS" \
            "$BUILD_ID" \
            "$LAST_STATE" \
            "$LAST_DISPLAY_NAME"
        if [ -n "$LAST_MESSAGE" ]; then
            printf '  message: %s\n' "$LAST_MESSAGE"
        fi
        if [ -n "$LAST_LOGS_URL" ]; then
            printf '  logs: %s\n' "$LAST_LOGS_URL"
        fi

        case "$LAST_STATE" in
            building)
                sleep "$POLL_INTERVAL"
                ;;
            running)
                FINAL_STATE='running'
                SUMMARY_REASON='Deployment completed; instance is running.'
                printf '%s\n' \
                    'Deployment completed; instance is running.'
                write_job_summary
                return 0
                ;;
            failed)
                FINAL_STATE='failed'
                SUMMARY_REASON=$LAST_MESSAGE
                [ -n "$SUMMARY_REASON" ] ||
                    SUMMARY_REASON=$LAST_ERROR
                [ -n "$SUMMARY_REASON" ] ||
                    SUMMARY_REASON='ERPU SaaS reported a failed build.'
                printf 'ERPU SaaS build %s failed.\n' "$BUILD_ID" >&2
                print_safe_diagnostics >&2
                write_job_summary
                return 1
                ;;
            *)
                FINAL_STATE=$LAST_STATE
                SUMMARY_REASON="Unknown ERPU SaaS state: ${LAST_STATE}"
                printf 'Error: Unknown ERPU SaaS state for build %s: %s\n' \
                    "$BUILD_ID" "$LAST_STATE" >&2
                print_safe_diagnostics >&2
                write_job_summary
                return 1
                ;;
        esac

        attempt=$((attempt + 1))
    done

    FINAL_STATE='timeout'
    waited_seconds=$((MAX_WAIT_ATTEMPTS * POLL_INTERVAL))
    SUMMARY_REASON="Timeout after ${waited_seconds} seconds."
    printf 'Timeout waiting for ERPU SaaS build %s after %s seconds.\n' \
        "$BUILD_ID" "$waited_seconds" >&2
    printf 'Last state: %s; display name: %s\n' \
        "${LAST_STATE:-unknown}" "${LAST_DISPLAY_NAME:-}" >&2
    if [ -n "$API_RESPONSE" ] &&
        printf '%s' "$API_RESPONSE" | jq -e 'type == "object"' \
            >/dev/null 2>&1; then
        print_safe_diagnostics >&2
    fi
    write_job_summary
    return 2
}

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

for required_command in curl jq; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        printf 'Error: Required command %s was not found.\n' \
            "$required_command" >&2
        exit 1
    fi
done

readonly API_BASE_URL="${ERPUSAAS_API_URL%/}"

if [ "$#" -ne 1 ]; then
    printf 'Usage: %s <production|staging1>\n' "$0" >&2
    exit 1
fi

environment="$1"
case "$environment" in
    production|staging1)
        ;;
    *)
        printf 'Error: Invalid environment %s. Expected production or staging1.\n' \
            "$environment" >&2
        exit 1
        ;;
esac

printf 'ERPU SaaS deployment to %s\n' "$environment"

if ! TRIGGER_RESPONSE=$(
    curl \
        --silent \
        --show-error \
        --fail-with-body \
        -X POST \
        -H "Authorization: Bearer ${ERPUSAAS_DEPLOY_SECRET}" \
        -F "commit=${GITHUB_SHA}" \
        -F "build=${GITHUB_RUN_NUMBER}" \
        "${API_BASE_URL}/erpusaas/project/${ERPUSAAS_DEPLOY_PROJECT}/${environment}/rebuild"
); then
    FINAL_STATE='trigger_failed'
    SUMMARY_REASON='ERPU SaaS rebuild request failed.'
    API_RESPONSE=$TRIGGER_RESPONSE
    printf 'Error: Failed to trigger ERPU SaaS rebuild.\n' >&2
    if printf '%s' "$API_RESPONSE" | jq -e 'type == "object"' \
        >/dev/null 2>&1; then
        print_safe_diagnostics >&2
    else
        printf '%s\n' \
            'Response body omitted because it is not valid JSON.' >&2
    fi
    write_job_summary
    exit 1
fi

BUILD_ID=$(printf '%s' "$TRIGGER_RESPONSE" | tr -d '[:space:]')
case "$BUILD_ID" in
    ''|*[!0-9]*)
        FINAL_STATE='invalid_build_id'
        SUMMARY_REASON='Trigger response did not contain a numeric build ID.'
        API_RESPONSE=$TRIGGER_RESPONSE
        printf 'Error: ERPU SaaS returned an invalid build ID.\n' >&2
        if printf '%s' "$API_RESPONSE" | jq -e 'type == "object"' \
            >/dev/null 2>&1; then
            print_safe_diagnostics >&2
        fi
        write_job_summary
        exit 1
        ;;
esac

printf 'Triggered ERPU SaaS build: %s\n' "$BUILD_ID"
printf 'Commit: %s\n' "$GITHUB_SHA"
printf 'Environment: %s\n' "$environment"

wait_for_build
exit "$?"
