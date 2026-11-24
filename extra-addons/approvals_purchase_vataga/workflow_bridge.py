"""A conservative bridge for a validation-only Enterprise inheritance layer.

No Enterprise workflow is copied here. The installed method's source must
prove that its ONLY behavior is the missing-product guard plus a cooperative
super call. Unknown implementations keep the original method and fail closed.
"""
import ast
from contextlib import contextmanager
import functools
import inspect
import logging
import textwrap

from .internal import APPROVAL_SCOPE

_logger = logging.getLogger(__name__)


def _attribute(node, owner, name):
    return (isinstance(node, ast.Attribute) and node.attr == name
            and isinstance(node.value, ast.Name) and node.value.id == owner)


def _purchase_test(node, owner):
    return (isinstance(node, ast.Compare) and _attribute(node.left, owner, 'approval_type')
            and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Constant)
            and node.comparators[0].value == 'purchase')


def _missing_test(node, owner):
    negate = isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)
    call = node.operand if negate else node
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == ('all' if negate else 'any')
            and len(call.args) == 1 and not call.keywords):
        return False
    generator = call.args[0]
    if not isinstance(generator, (ast.GeneratorExp, ast.ListComp)) or len(generator.generators) != 1:
        return False
    comp = generator.generators[0]
    if (not isinstance(comp.target, ast.Name) or comp.ifs or comp.is_async
            or not _attribute(comp.iter, owner, 'product_line_ids')):
        return False
    element = generator.elt
    if not negate:
        if not isinstance(element, ast.UnaryOp) or not isinstance(element.op, ast.Not):
            return False
        element = element.operand
    return _attribute(element, comp.target.id, 'product_id')


def _only_raise(body):
    if len(body) != 1 or not isinstance(body[0], ast.Raise) or body[0].cause:
        return False
    call = body[0].exc
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id in ('UserError', 'ValidationError')
            and len(call.args) == 1 and not call.keywords):
        return False
    message = call.args[0]
    if isinstance(message, ast.Call):
        if (not isinstance(message.func, ast.Name) or message.func.id != '_'
                or len(message.args) != 1 or message.keywords):
            return False
        message = message.args[0]
    return isinstance(message, ast.Constant) and isinstance(message.value, str)


def _guard(node, owner):
    if not isinstance(node, ast.If) or node.orelse:
        return False
    if isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.And):
        return (len(node.test.values) == 2 and _purchase_test(node.test.values[0], owner)
                and _missing_test(node.test.values[1], owner) and _only_raise(node.body))
    return (_purchase_test(node.test, owner) and len(node.body) == 1
            and isinstance(node.body[0], ast.If) and not node.body[0].orelse
            and _missing_test(node.body[0].test, owner) and _only_raise(node.body[0].body))


def validation_only_source(source):
    """Return (compatible, requires_singleton). Reject ANY extra behavior."""
    try:
        tree = ast.parse(textwrap.dedent(source))
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
            return False, False
        func = tree.body[0]
        if func.decorator_list:
            return False, False
        body = list(func.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body.pop(0)  # docstring only
        ensure_one = False
        if body and ast.dump(body[0]) == ast.dump(ast.parse('self.ensure_one()').body[0]):
            ensure_one = True
            body.pop(0)
        if len(body) != 2:
            return False, False
        guard, delegate = body
        if isinstance(guard, ast.For):
            if (not isinstance(guard.target, ast.Name) or guard.orelse or len(guard.body) != 1
                    or not isinstance(guard.iter, ast.Name) or guard.iter.id != 'self'):
                return False, False
            valid = _guard(guard.body[0], guard.target.id)
        else:
            valid = _guard(guard, 'self')
        # Verify exact cooperative forwarding, not just a call with the same name.
        args = func.args
        if args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg:
            return False, False
        names = [a.arg for a in args.args]
        if not names or names[0] != 'self':
            return False, False
        expected = ast.parse('return super().action_approve(%s)' % ', '.join(names[1:])).body[0]
        return bool(valid and ast.dump(delegate) == ast.dump(expected)), ensure_one or not isinstance(guard, ast.For)
    except (SyntaxError, ValueError, TypeError):
        return False, False


@contextmanager
def approval_scope(requests):
    token = APPROVAL_SCOPE.set((requests.env.cr, requests.env.uid, frozenset(requests.ids)))
    try:
        yield
    finally:
        APPROVAL_SCOPE.reset(token)


def install_bridge(model_class):
    for cls in model_class.__mro__:
        if not cls.__module__.startswith('odoo.addons.approvals_purchase.'):
            continue
        original = cls.__dict__.get('action_approve')
        if original is None:
            continue
        if getattr(original, '_vataga_validation_bridge', False):
            return
        if hasattr(original, '__wrapped__'):
            _logger.warning('Vataga: decorated purchase approval layer; preserving the original method')
            return
        try:
            valid, ensure_one = validation_only_source(inspect.getsource(original))
        except (OSError, TypeError):
            valid, ensure_one = False, False
        if not valid:
            _logger.warning('Vataga: unverified approvals_purchase.action_approve; '
                            'the original workflow remains active. '
                            'Missing-product compatibility requires Enterprise testing.')
            return
        signature = inspect.signature(original)

        @functools.wraps(original)
        def bridge(records, *args, **kwargs):
            scope = APPROVAL_SCOPE.get()
            authorized = (scope is not None and scope[0] is records.env.cr
                          and scope[1] == records.env.uid and set(records.ids) <= scope[2]
                          and 'vataga_approved_once' in records._fields)
            if authorized:
                if ensure_one:
                    records.ensure_one()
                bound = signature.bind(records, *args, **kwargs)
                bound.apply_defaults()
                # This removes only the PROVEN validation-only layer. All
                # inherited methods above and below it remain in the MRO.
                return super(cls, records).action_approve(*bound.args[1:], **bound.kwargs)
            return original(records, *args, **kwargs)

        bridge._vataga_validation_bridge = True
        cls.action_approve = bridge
        _logger.info('Vataga: installed source-verified purchase validation bridge on %s', cls)
        return
