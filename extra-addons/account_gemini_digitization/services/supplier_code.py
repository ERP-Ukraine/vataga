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
