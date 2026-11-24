import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../static/src/url_policy.js', import.meta.url), 'utf8');
const { safePurchaseHref } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const valid = ['http://vendor.ua/a', 'https://vendor.ua/a?x=1', 'HTTPS://example.com/a'];
for (const value of valid) {
    assert.equal(safePurchaseHref(value), value);
}
const invalid = [false, '', 'example.com', 'ftp://example.com', 'javascript:alert(1)',
    '//example.com', 'data:text/html,hi', 'https://', 'https://u:p@example.com',
    'https://u@example.com', 'https://example.com\\@other.com', 'https://example.com/ hello',
    'http://example.com:70000'];
for (const value of invalid) {
    assert.equal(safePurchaseHref(value), undefined, String(value));
}
console.log(`PASS: safe URL policy (${valid.length + invalid.length} protocol and URL cases)`);
