"""Pure validation shared by the ORM and portable tests."""
import ipaddress
import math
import re
from urllib.parse import urlsplit


def valid_product_url(value):
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or '\\' in value:
        return False
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
            return False
        if parts.username is not None or parts.password is not None:
            return False
        if parts.port is not None and not 0 < parts.port <= 65535:
            return False
        host = parts.hostname
        try:
            ipaddress.ip_address(host)
        except ValueError:
            labels = host.rstrip('.').encode('idna').decode('ascii').split('.')
            if not all(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', x)
                       for x in labels):
                return False
        return True
    except (ValueError, UnicodeError):
        return False


def positive_quantity(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def line_errors(product, description, url, quantity):
    errors = []
    if not positive_quantity(quantity):
        errors.append('Кількість має бути більшою за нуль.')
    if not product and not (description or '').strip():
        errors.append('Укажіть опис товару.')
    if (url or not product) and not valid_product_url(url):
        errors.append('Укажіть коректний URL з протоколом HTTP або HTTPS.')
    return errors
