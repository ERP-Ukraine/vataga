"""Inspect actual registered implementations; names never authorize providers."""
import hashlib
import inspect
import ast
import textwrap

ENTERPRISE_COST_MODULE = 'odoo.addons.mrp_workorder.models.mrp_workorder'
ENTERPRISE_COST_QUALNAME = 'MrpProductionWorkcenterLine._cal_cost'
ENTERPRISE_COST_SHA256 = '71c4a9b3bfda60a38874a601ca26620730a9c121de75e030de7c13c593049d12'


def enterprise_cost_candidate(records):
    """Attest the supplied staging source for an audit, NEVER approve it."""
    matches = []
    for owner, implementation in method_definitions(records, '_cal_cost'):
        if (owner.__module__ != ENTERPRISE_COST_MODULE
                or getattr(implementation, '__qualname__', None) != ENTERPRISE_COST_QUALNAME):
            continue
        row = {'provider': provider_name(owner, implementation), 'production_authorized': False,
               'expected_sha256': ENTERPRISE_COST_SHA256, 'source_matches': False}
        try:
            source = inspect.getsource(implementation)
            row['actual_sha256'] = hashlib.sha256(source.encode('utf-8')).hexdigest()
            row['source_matches'] = (row['actual_sha256'] == ENTERPRISE_COST_SHA256
                                     and getattr(implementation, '__wrapped__', None) is None
                                     and implementation.__module__ == ENTERPRISE_COST_MODULE)
        except (TypeError, OSError) as error:
            row['source_error'] = type(error).__name__
        matches.append(row)
    return matches


def reviewed_subcontracting_methods(records):
    """Only the reviewed Community 17 override, for non-subcontract MOs.

    Its sole extra branch changes extra_cost from a subcontract receipt. The
    caller must reject that branch's input before allowing native posting.
    An identical module or function NAME is insufficient.
    """
    for owner, implementation in method_definitions(records, '_cal_price'):
        if (owner.__module__ != 'odoo.addons.mrp_subcontracting_account.models.mrp_production'
                or implementation.__module__ != owner.__module__
                or getattr(implementation, '__wrapped__', None) is not None):
            continue
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(implementation)))
            # Python 3.12 adds empty type_params; it is absent in 3.10/3.11.
            canonical = ast.dump(tree.body[0], include_attributes=False).replace(', type_params=[]', '')
            digest = hashlib.sha256(canonical.encode()).hexdigest()
        except (TypeError, OSError, SyntaxError):
            continue
        if digest == '2028b44ccffe792370d64d58bd8f268a4b334f69d8804a90245b93ece32400aa':
            yield implementation


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
