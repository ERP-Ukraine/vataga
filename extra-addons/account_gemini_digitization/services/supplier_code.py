import re
import unicodedata


class SupplierArticleNormalizer:
    """Normalize supplier article codes without treating them as numbers."""

    INVISIBLE_RE = re.compile(r'[\u200b-\u200f\u202a-\u202e\u2060\ufeff]')
    SEPARATOR_SPACE_RE = re.compile(r'\s*([/\-_])\s*')

    @classmethod
    def normalize(cls, value):
        if value is None or value is False:
            return ''
        value = unicodedata.normalize('NFKC', str(value))
        value = cls.INVISIBLE_RE.sub('', value)
        value = value.strip().casefold()
        value = cls.SEPARATOR_SPACE_RE.sub(r'\1', value)
        value = re.sub(r'\s+', ' ', value)
        return value.strip()

    @classmethod
    def separatorless(cls, value):
        normalized = cls.normalize(value)
        if not normalized:
            return ''
        return re.sub(r'[/\-_ ]+', '', normalized)

    @classmethod
    def equals(cls, left, right):
        left_normalized = cls.normalize(left)
        right_normalized = cls.normalize(right)
        return bool(left_normalized and left_normalized == right_normalized)


class TechnicalCodeNormalizer:
    """Normalize full technical model codes while preserving all suffix segments."""

    INVISIBLE_RE = SupplierArticleNormalizer.INVISIBLE_RE
    DASH_TRANSLATION = str.maketrans({
        '\u2010': '-',
        '\u2011': '-',
        '\u2012': '-',
        '\u2013': '-',
        '\u2014': '-',
        '\u2212': '-',
    })
    LOOKALIKE_TRANSLATION = str.maketrans({
        '\u0406': 'I', '\u0456': 'I',
        '\u0407': 'I', '\u0457': 'I',
        '\u0404': 'E', '\u0454': 'E',
        '\u0410': 'A', '\u0430': 'A',
        '\u0412': 'B', '\u0432': 'B',
        '\u0415': 'E', '\u0435': 'E',
        '\u041a': 'K', '\u043a': 'K',
        '\u041c': 'M', '\u043c': 'M',
        '\u041d': 'H', '\u043d': 'H',
        '\u041e': 'O', '\u043e': 'O',
        '\u0420': 'P', '\u0440': 'P',
        '\u0421': 'C', '\u0441': 'C',
        '\u0422': 'T', '\u0442': 'T',
        '\u0425': 'X', '\u0445': 'X',
    })
    FULL_CODE_RE = re.compile(
        r'(?<![A-Z0-9])'
        r'(?:'
        r'[A-Z]{2,}[A-Z0-9]*(?:[-/][A-Z0-9]+){2,}'
        r'|'
        r'[A-Z]+[0-9][A-Z0-9]*(?:[-/][A-Z0-9]+)+'
        r')'
        r'(?![A-Z0-9])'
    )

    @classmethod
    def normalize(cls, value):
        if value is None or value is False:
            return ''
        value = unicodedata.normalize('NFKC', str(value))
        value = cls.INVISIBLE_RE.sub('', value)
        value = value.translate(cls.LOOKALIKE_TRANSLATION)
        value = value.translate(cls.DASH_TRANSLATION)
        value = value.upper()
        value = re.sub(r'\s*-\s*', '-', value)
        value = re.sub(r'\s*/\s*', '/', value)
        value = re.sub(r'\s+', ' ', value)
        return value.strip()

    @classmethod
    def key(cls, value):
        normalized = cls.normalize(value)
        return re.sub(r'[^A-Z0-9]+', '', normalized)

    @classmethod
    def extract(cls, value):
        normalized = cls.normalize(value)
        if not normalized:
            return []
        codes = []
        seen = set()
        for match in cls.FULL_CODE_RE.findall(normalized):
            code = match.strip(':-.,; ')
            key = cls.key(code)
            if not key or key in seen:
                continue
            seen.add(key)
            codes.append(code)
        return codes

    @classmethod
    def equals(cls, left, right):
        left_key = cls.key(left)
        right_key = cls.key(right)
        return bool(left_key and right_key and left_key == right_key)
