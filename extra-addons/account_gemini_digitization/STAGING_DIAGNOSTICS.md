# Temporary staging diagnostics

Deploy only to staging. Restart Odoo and upgrade `account_gemini_digitization`
with the normal staging deployment procedure to load the diagnostic form XML.
The manifest version is intentionally unchanged.

Reproduce OCR from a draft vendor bill or purchase order. An ERROR notification
now includes the job ID, stage and actual exception text (with secrets removed).
The failed job remains stored; other pipeline statuses retain the old cleanup.

The source document's chatter receives an internal note with a compact summary
and a link to the read-only job form. In that form, open **Raw JSON**:

- `raw_request_json`: attachment metadata, model and endpoint without API key.
- `raw_response_json.diagnostic`: job ID, state, mode, stage, exception, HTTP
  status, transport error, promptFeedback, finishReason and candidate counts.
- `attempts` / `final_response`: existing GeminiClient response captures.
- `text_extraction`, `json_parse_error`, `extracted_text_for_json_parse`,
  `json_text_candidate`: existing extraction and JSON parser evidence.
  Null/absent data means the corresponding stage was not reached.

Direct form URL: `/web#id=<JOB_ID>&model=account.gemini.digitization.job&view_type=form`.
The chatter summary survives subsequent job deletion. Full OCR text stays on
the job. Credentials and binary payloads are redacted before diagnostic writes.

Stages: preflight, attachment_decode, request, http_response, text_extraction,
json_parse, response_parse, ocr_line_create, matching. The OCR creation stage is
identified from the failing parser frame, without changing ResponseParser.
Existing matching warnings and ApplyService/manual-review behavior are unchanged.

Run isolated regression tests (ORM/HTTP doubles, no database required;
MarkupSafe, an existing Odoo dependency, must be available):

```sh
python -B extra-addons/account_gemini_digitization/tests/test_staging_diagnostics.py -v
```

These tests do not replace staging validation of Odoo transactions, access rules,
chatter and the XML form. No real Gemini request is made by the tests.
