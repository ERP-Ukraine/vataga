"""Process-local capabilities. JSON/RPC contexts cannot reproduce these objects."""
from contextvars import ContextVar
from collections.abc import Mapping

INTERNAL = object()
RFQ = object()
WORKFLOW = object()
APPROVAL_SCOPE = ContextVar('vataga_purchase_approval_scope', default=None)


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
    result.update(active_model='product.product', active_id=product_id, active_ids=[product_id])
    return result


def refreshable_fields(records):
    """Do not evict fields (or relational inverses) protected by ORM compute."""
    return [field.name for field in records._fields.values()
            if not any(records.env.is_protected(field, record) for record in records)
            and not any(records.env.protected(inverse)
                        for inverse in records.pool.field_inverses[field])]
