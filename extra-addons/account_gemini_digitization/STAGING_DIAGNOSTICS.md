# Gemini OCR failure diagnostics

Version: 17.0.1.0.1. Upgrade this module on staging, then retry vendor bill
238330. Production is outside this change.

Failed attempts add an internal note to the source bill or purchase order with
UTC attempt time, failed stage, a fixed safe description, HTTP status and numeric
Gemini API error code when available, document ID and attachment ID.
Stages: preparation, request, response receipt, parsing/validation, product
matching, result application. Known attachment, transport and JSON errors have
specific safe descriptions. Arbitrary exception/API messages, PDF/base64,
credentials, prompts and full responses are never included in the note.

Temporary jobs and recognition lines are deleted for every returned outcome.
Processing and application failures roll back their savepoint before the note
is posted. There are no manual commits. Successful runs add no diagnostic note.
Partial recognition, ambiguous matches, and automatic application safety checks
retain the existing staging behavior. Unexpected matcher exceptions abort the
attempt rather than allowing partially matched results to be applied.

The earlier temporary staging diagnostic job links are superseded by durable
notes; retained old jobs are not migrated or deleted by this change.

Run isolated mocked client/formatter tests without Odoo:

```sh
python -B extra-addons/account_gemini_digitization/tests/test_staging_diagnostics.py -v
```

Run database-backed regression tests in an Odoo 17 test environment:

```sh
odoo -d test-db -u account_gemini_digitization --test-enable --stop-after-init --test-tags /account_gemini_digitization
```

All HTTP requests in tests are mocked. The isolated tests do not validate actual
Odoo transactions or chatter persistence; those require the database tests.
