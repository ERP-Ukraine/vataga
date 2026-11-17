"""Print a fixed, redacted summary of the staging status response."""

import json
import re
import sys


OPTIONAL_FIELDS = (
    'stage', 'phase', 'error', 'error_code', 'error_type', 'error_message',
    'message', 'reason', 'failure_reason', 'detail', 'details', 'diagnostics',
    'traceback', 'log', 'logs', 'log_url', 'exit_code', 'modules', 'updated_modules',
)
SIGNATURES = {
    'odoo-xml-parse-error': r'\bParseError\b',
    'xpath-target-missing': r'cannot be located in parent view|element .* cannot be found in parent view',
    'odoo-registry-load-error': r'failed to load registry',
    'missing-external-id': r'external ID not found in the system',
    'undefined-database-column': r'\bUndefinedColumn\b',
    'module-update': r'\bto upgrade\b|updating module|\bmodule_update\b',
    'container-image-build': r'buildkit|\bdocker_build\b|failed to solve',
}


def summarize(response):
    present = [name for name in OPTIONAL_FIELDS if name in response]
    unknown = len(response.keys() - {*OPTIONAL_FIELDS, 'state', 'display_name'})
    values = ' '.join(
        response[name][:16000]
        for name in present if isinstance(response[name], str)
    )
    hints = [label for label, pattern in SIGNATURES.items() if re.search(pattern, values, re.I)]
    print('ERPU status optional fields: ' + (', '.join(present) or 'none'))
    print(f'ERPU status other fields: {unknown}')
    print('ERPU status known diagnostic signatures: ' + (', '.join(hints) or 'none'))


if __name__ == '__main__':
    try:
        document = json.load(sys.stdin)
    except (ValueError, UnicodeError):
        document = None
    if isinstance(document, dict):
        summarize(document)
    else:
        print('ERPU status diagnostics unavailable')
