"""Inspect actual registered implementations; names never authorize providers."""
import hashlib
import inspect


def method_definitions(records, method):
    """Return definitions in registry MRO order, including shadowed overrides."""
    return [(owner, owner.__dict__[method]) for owner in type(records).__mro__
            if method in owner.__dict__]


def provider_name(owner, implementation):
    return '%s.%s -> %s.%s' % (
        owner.__module__, owner.__qualname__,
        getattr(implementation, '__module__', '?'),
        getattr(implementation, '__qualname__', '?'))


def describe_method(records, method, allowed=(), include_source=False):
    """Read code metadata without invoking a provider or reading business rows.

    Fingerprints are evidence for comparing deployments, never an allowlist.
    MRO order alone does not prove that an implementation calls super().
    """
    result = []
    for owner, implementation in method_definitions(records, method):
        row = {'provider': provider_name(owner, implementation),
               'approved': implementation in allowed, 'wrappers': []}
        seen = set()
        current = implementation
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            item = {'module': getattr(current, '__module__', None),
                    'qualname': getattr(current, '__qualname__', None)}
            try:
                item['file'] = inspect.getsourcefile(current)
                source, item['line'] = inspect.getsourcelines(current)
                source = ''.join(source)
                item['source_sha256'] = hashlib.sha256(source.encode('utf-8')).hexdigest()
                if include_source:
                    item['source'] = source
            except (TypeError, OSError, IOError) as error:
                item['source_error'] = type(error).__name__
            row['wrappers'].append(item)
            current = getattr(current, '__wrapped__', None)
        result.append(row)
    return result
