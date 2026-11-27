"""Process-local capabilities. JSON/RPC contexts cannot reproduce these objects."""
from contextvars import ContextVar
from collections.abc import Mapping
from copy import deepcopy

INTERNAL = object()
RFQ = object()
WORKFLOW = object()
PO_APPROVAL = object()
APPROVAL_SCOPE = ContextVar('vataga_purchase_approval_scope', default=None)


def registry_period_arch(arch, source_types):
    """Format Date projections as calendar dates; leave Datetime native.

    The stored Datetime projections retain their schema and export contract.
    Only our registry's display changes, using the real source field types.
    """
    result = deepcopy(arch)
    for source, field_type in source_types.items():
        if field_type == 'date':
            for node in result.xpath(".//field[@name='vataga_%s']" % source):
                node.set('widget', 'vataga_calendar_date')
    return result


def clean_defaults(context, names):
    return {key: value for key, value in context.items()
            if key not in {'default_' + name for name in names}
            and not key.startswith('default_vataga_') and not key.startswith('_vataga_')}


def public_result(result):
    if isinstance(result, dict) and isinstance(result.get('context'), Mapping):
        return dict(result, context={k: v for k, v in result['context'].items()
                                     if not k.startswith('_vataga_')})
    return result


def product_form_context(context, product_id):
    """Keep user locale/companies, never carry request defaults into products.

    Defaults such as approval.category's default_category_id have unrelated
    meanings for product/supplier/partner forms. Active IDs must also identify
    the opened product, rather than the transient wizard or approval request.
    """
    result = {key: context[key] for key in ('lang', 'tz', 'allowed_company_ids') if key in context}
    # Native action_service.doActionButton re-merges non-default caller keys
    # after this action is returned. An absent category_id therefore leaks back
    # from approval.category; res.partner._default_category reads that raw key.
    # The base approval partner field neutralizes the same key explicitly.
    result.update(category_id=False, active_model='product.product', active_id=product_id, active_ids=[product_id])
    return result


def refreshable_fields(records):
    """Do not evict fields (or relational inverses) protected by ORM compute."""
    return [field.name for field in records._fields.values()
            if not any(records.env.is_protected(field, record) for record in records)
            and not any(records.env.protected(inverse)
                        for inverse in records.pool.field_inverses[field])]
