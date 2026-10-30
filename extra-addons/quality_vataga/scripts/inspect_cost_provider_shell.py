"""Read-only Odoo shell export, including on the original 7c7b307 deployment.

No import from new addon code, model writes, cost calls, commit or fixture.
Usage: odoo shell --no-http -d odoo17-vataga-staging < this_file.py > report.json
The shell must use the SAME config/addons paths as the staging service.
"""
import hashlib
import inspect
import json


if env.cr.dbname != 'odoo17-vataga-staging' or not env.user.has_group('base.group_system'):
    raise RuntimeError('Exact staging database and system administrator required')

report = {'database': env.cr.dbname, 'mode': 'READ ONLY', 'methods': {}, 'modules': {},
          'order': 'registry MRO definitions; source review must establish actual super calls'}
for model, method in (('mrp.production', '_cal_price'), ('mrp.workorder', '_cal_cost')):
    definitions = []
    for owner in type(env[model]).__mro__:
        if method not in owner.__dict__:
            continue
        function = owner.__dict__[method]
        entry = {'owner': owner.__module__ + '.' + owner.__qualname__,
                 'implementation': function.__module__ + '.' + function.__qualname__}
        try:
            entry['file'] = inspect.getsourcefile(function)
            source, entry['line'] = inspect.getsourcelines(function)
            entry['source'] = ''.join(source)
            entry['source_sha256'] = hashlib.sha256(entry['source'].encode('utf-8')).hexdigest()
            module = inspect.getmodule(function)
            # Full provider file is needed to inspect imported cost helpers and
            # additional overrides, rather than authorizing from a method name.
            if module.__name__ not in report['modules']:
                module_source = inspect.getsource(module)
                report['modules'][module.__name__] = {
                    'file': inspect.getsourcefile(module), 'source': module_source,
                    'source_sha256': hashlib.sha256(module_source.encode('utf-8')).hexdigest()}
        except (TypeError, OSError, IOError) as error:
            entry['source_error'] = type(error).__name__ + ': ' + str(error)
        definitions.append(entry)
    report['methods'][model + '.' + method] = definitions
print(json.dumps(report, ensure_ascii=False, indent=2))
