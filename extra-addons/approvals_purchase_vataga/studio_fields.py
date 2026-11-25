"""Consume pre-existing manual employee fields without owning their metadata."""
from copy import deepcopy
from lxml import etree

EMPLOYEE_FIELDS = ('x_studio_iniciation', 'x_studio_who_recived')
EMPLOYEE_LABELS = ('Ініціатор закупівлі', 'Отримувач')


def validate_employee_fields(fields):
    for name in EMPLOYEE_FIELDS:
        field = fields.get(name)
        if (not field or field.type != 'many2one' or field.comodel_name != 'hr.employee'
                or not field.store or field.required or field.readonly):
            raise TypeError('Vataga prerequisite: approval.request.%s must already exist as '
                            'optional editable stored Many2one(hr.employee). Run the Studio preflight BEFORE deploying .5.' % name)


def employee_form_arch(arch, source_domains=None):
    """Run after the full native/ERP/Studio inheritance, irrespective of priority.

    Existing Studio nodes stay in place with nonpurchase behavior unchanged.
    Missing nodes are added only for purchase; no Studio view is modified.
    """
    result = deepcopy(arch)
    anchor = result.xpath(".//field[@name='request_owner_id']")
    if not anchor and not any(result.xpath(".//field[@name='%s']" % n) for n in EMPLOYEE_FIELDS):
        return result
    if not anchor:
        anchor = result.xpath(".//field[@name='x_studio_iniciation' or @name='x_studio_who_recived']")
    for name in ('approval_type', 'company_id', 'vataga_lines_locked'):
        if not result.xpath(".//field[@name='%s']" % name):
            result.append(etree.Element('field', name=name, invisible='1'))
    for name, label in zip(EMPLOYEE_FIELDS, EMPLOYEE_LABELS):
        nodes = result.xpath(".//field[@name='%s']" % name)
        if not nodes:
            node = etree.Element('field', name=name, string=label,
                                 invisible="approval_type != 'purchase'",
                                 options="{'no_create': True, 'no_create_edit': True}")
            anchor[-1].addnext(node)
            anchor = [node]
            nodes = [node]
        for node in nodes:
            readonly = node.get('readonly', 'False')
            node.set('readonly', "vataga_lines_locked if approval_type == 'purchase' else (%s)" % readonly)
            invisible = node.get('invisible', 'False')
            node.set('invisible', "False if approval_type == 'purchase' else (%s)" % invisible)
            required = node.get('required', 'False')
            node.set('required', "False if approval_type == 'purchase' else (%s)" % required)
            domain = node.get('domain', (source_domains or {}).get(name) or '[]')
            node.set('domain', "(%s) + ([('active', '=', True)] + ([('company_id', 'in', [False, company_id])] if company_id else []) if approval_type == 'purchase' else [])" % domain)
    return result
