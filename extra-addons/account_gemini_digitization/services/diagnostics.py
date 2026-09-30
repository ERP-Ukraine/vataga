"""Temporary staging diagnostics: never persist credentials or binary payloads."""
import re
from urllib.parse import quote, quote_plus


def sanitize_diagnostics(value, secrets=()):
    sensitive_keys = {'apikey', 'key', 'xgoogapikey', 'authorization',
                      'inlinedata', 'data', 'datas', 'base64'}
    if isinstance(value, dict):
        return {sanitize_diagnostics(str(key), secrets):
                '[REDACTED]' if re.sub(r'[^a-z]', '', str(key).lower()) in sensitive_keys
                else sanitize_diagnostics(item, secrets)
                for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [sanitize_diagnostics(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    for secret in secrets:
        if isinstance(secret, bytes):
            secret = secret.decode('ascii', errors='ignore')
        if secret:
            for variant in (str(secret), quote(str(secret), safe=''), quote_plus(str(secret))):
                value = value.replace(variant, '[REDACTED]')
    value = re.sub(r'(?i)([?&]key=)[^&\s"<>]+', r'\1[REDACTED]', value)
    value = re.sub(r'(?i)data:[^;\s]+;base64,[A-Za-z0-9+/=\s]+', '[REDACTED]', value)
    return value
