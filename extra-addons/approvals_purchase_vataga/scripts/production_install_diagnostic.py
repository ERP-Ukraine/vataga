"""MANUAL read-only Odoo shell diagnostic, before/after FIRST installation.

Run with the currently deployed code; does not import this addon. Prints only
metadata, counts and hashes, not employee/document values. No SQL, sudo, writes,
install, commit, network, or files. Execute in a quiet maintenance window under
the SAME authorized user/company scope before and after. Keep stdout locally.
Never run an unreviewed server action or copy the production DB to staging.
"""
import hashlib
import json
from lxml import etree


PEOPLE = ('x_studio_iniciation', 'x_studio_who_recived')
STUDIO = (
    ('studio_customization.odoo_studio_approval_b0d5c6ab-c35d-4780-a1fe-7fbf979462c6',
     'approvals.approval_request_view_form', 160,
     """<data>
  <xpath expr="//field[@name='category_id']" position="after">
    <field name="x_studio_iniciation" string="Ініціатор закупівлі" invisible="category_id not in [9, 12]"/>
    <field name="x_studio_who_recived" string="Отримувач" invisible="category_id not in [9, 12]"/>
  </xpath>
</data>"""),
    ('studio_customization.odoo_studio_approval_35bca016-a3a0-46ef-b7e7-9dc97af8e0ec',
     'approvals.approval_request_view_tree', 99,
     """<data>
  <xpath expr="//tree[1]/field[@name='name']" position="after">
    <field name="create_date" optional="show"/>
    <field name="date_start" optional="show"/>
    <field name="date_end" optional="show"/>
    <field name="partner_id" optional="show"/>
  </xpath>
  <xpath expr="//field[@name='request_owner_id']" position="after">
    <field name="x_studio_who_recived" optional="show"/>
    <field name="x_studio_iniciation" optional="hide"/>
  </xpath>
</data>"""),
)
SOURCE_IDS = (
    'studio_customization.many2one_approval_re_ef8bf1c2-68d7-47eb-89dc-a425fb65a32e',
    'studio_customization.many2one_approval_re_b86112ec-6382-46d9-a609-3c26ea6ed9cd',
)


def canonical_xml(value):
    parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False, no_network=True)
    return etree.tostring(etree.fromstring(value.encode(), parser), method='c14n')


def fingerprint(records, names, max_records):
    """Stable native stored fields only; no dependent business action calls."""
    names = ['id'] + [n for n in names if n in records._fields and records._fields[n].store]
    if len(records) > max_records:
        return {'status': 'BLOCKED_VOLUME', 'count': len(records), 'fields': names}
    digest = hashlib.sha256()
    ids = sorted(records.ids)
    for start in range(0, len(ids), 500):
        for row in records.browse(ids[start:start + 500]).read(names, load=None):
            digest.update(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode())
            digest.update(b'\n')
    return {'count': len(ids), 'fields': names, 'sha256': digest.hexdigest()}


def production_install_diagnostic(env, max_records=50000):
    report = {'database': env.cr.dbname, 'uid': env.uid, 'superuser_mode': env.su,
              'allowed_company_ids': sorted(env.companies.ids), 'errors': [],
              'note': 'Current authorized visibility only. Compare SAME user/companies, quiet DB. No values exported.'}
    model = env['approval.request'].with_context(active_test=False, lang='en_US')
    modules = env['ir.module.module'].search([
        ('name', 'in', ['approvals_purchase_vataga','approvals','approvals_purchase','purchase','hr'])])
    report['modules'] = modules.read(['name','state','latest_version'], load=None)
    report['all_installed_modules'] = env['ir.module.module'].search([
        ('state','=','installed')], order='name').read(['name','latest_version'], load=None)
    namespace = env['ir.model.fields'].search([
        ('model','in',['approval.request','approval.product.line','product.template','purchase.order','res.company']),
        ('name','=like','vataga_%')], order='model,name')
    report['existing_vataga_namespace'] = namespace.read(
        ['model','name','ttype','relation','state','modules'], load=None)
    installed = modules.filtered(lambda m: m.name == 'approvals_purchase_vataga' and m.state == 'installed')
    if namespace and not installed:
        report['errors'].append('PREEXISTING_VATAGA_NAMESPACE_REQUIRES_SCHEMA_OWNERSHIP_REVIEW')
    report['source_fields'] = {}
    for name, expected_xml_id in zip(PEOPLE, SOURCE_IDS):
        field = model._fields.get(name)
        meta = env['ir.model.fields'].search([('model','=','approval.request'),('name','=',name)])
        expected = env.ref(expected_xml_id, raise_if_not_found=False)
        good = bool(field and field.type == 'many2one' and field.comodel_name == 'hr.employee'
                    and field.store and not field.required and not field.readonly and len(meta) == 1)
        report['source_fields'][name] = {
            'compatible': good, 'metadata_ids': meta.ids,
            'expected_xml_id_matches': bool(expected and expected == meta),
            'actual_xml_ids': meta.get_external_id(),
            'state': meta.state if len(meta) == 1 else None,
            'modules': meta.modules if len(meta) == 1 else None,
            'has_field_default': bool(field and field.default),
            'domain': str(field.domain) if field else None,
            'groups': field.groups if field else None,
            'has_compute': bool(field and field.compute),
            'related': field.related if field else None,
        }
        if not good or not expected or expected != meta or meta.state != 'manual':
            report['errors'].append('SOURCE_FIELD_OR_XML_ID: ' + name)
        if field and callable(field.domain):
            report['errors'].append('CALLABLE_SOURCE_DOMAIN_REQUIRES_VIEW_COMPATIBILITY_REVIEW: ' + name)
    report['studio'] = []
    for xml_id, parent_id, priority, expected_arch in STUDIO:
        view = env.ref(xml_id, raise_if_not_found=False)
        parent = env.ref(parent_id, raise_if_not_found=False)
        info = {'xml_id': xml_id, 'present': bool(view)}
        if view:
            info.update(id=view.id, active=view.active, priority=view.priority,
                        inherit_id=view.inherit_id.id,
                        parent_matches=bool(parent and view.inherit_id == parent),
                        exact_supplied_xml=canonical_xml(view.with_context(lang='en_US').arch_db) == canonical_xml(expected_arch))
        if not (view and view.active and parent and view.inherit_id == parent
                and view.priority == priority and info['exact_supplied_xml']):
            report['errors'].append('STUDIO_CHANGED_OR_ABSENT: ' + xml_id)
        report['studio'].append(info)
    report['view_anchors'] = {}
    for kind in ('form','tree'):
        base = env.ref('approvals.approval_request_view_' + kind)
        # Full actual combined inheritance on THIS database; never edits a view.
        arch = base.with_context(lang='en_US')._get_combined_arch()
        selectors = ["//field[@name='name']", "//field[@name='request_owner_id']", "//field[@name='category_id']"]
        if kind == 'form':
            selectors += ["//sheet", "//field[@name='reference']", "//field[@name='date_start']",
                          "//field[@name='date_end']", "//field[@name='product_line_ids']",
                          "//page[@name='products']", "//button[@name='action_create_purchase_orders']",
                          "//group[@name='request_details']"]
        report['view_anchors'][kind] = {expr: len(arch.xpath(expr)) for expr in selectors}
        if not all(report['view_anchors'][kind].values()):
            report['errors'].append('MISSING_XPATH_ANCHOR: ' + kind)
        report['view_anchors'][kind]['employee_node_counts'] = {
            name: len(arch.xpath(".//field[@name='%s']" % name)) for name in PEOPLE}
    report['extensions'] = env['ir.ui.view'].with_context(active_test=False).search([
        ('model','=','approval.request'), ('type','in',['form','tree'])], order='priority,id').read(
            ['name','active','priority','inherit_id'], load=None)
    report['period_types'] = {name: model._fields[name].type for name in ('date_start','date_end')}
    report['public_employee_model'] = 'hr.employee.public' in env
    if not report['public_employee_model']:
        report['errors'].append('MISSING_PUBLIC_EMPLOYEE_MODEL')
    # No category IDs are assumed. No records are created or changed.
    requests = model.search([('approval_type','=','purchase')], order='id')
    lines = env['approval.product.line'].with_context(active_test=False, lang='en_US').search([
        ('approval_request_id','in',requests.ids)], order='id')
    approvers = env['approval.approver'].with_context(active_test=False, lang='en_US').search([
        ('request_id','in',requests.ids)], order='id')
    linked = lines.purchase_order_line_id
    orders = linked.order_id
    order_lines = env['purchase.order.line'].with_context(active_test=False, lang='en_US').search([
        ('order_id','in',orders.ids)], order='id')
    report['historical_fingerprints'] = {
        'requests': fingerprint(requests, ['name','reference','category_id','company_id','request_owner_id',
            'request_status','partner_id','date_start','date_end','create_uid','create_date','write_uid','write_date', *PEOPLE], max_records),
        'lines': fingerprint(lines, ['approval_request_id','company_id','product_id','description','quantity',
            'product_uom_id','purchase_order_line_id','warehouse_id','create_uid','create_date','write_uid','write_date'], max_records),
        'approvers': fingerprint(approvers, ['request_id','user_id','sequence','required','status','company_id'], max_records),
        'orders': fingerprint(orders, ['name','origin','state','partner_id','company_id','currency_id','create_date','write_date'], max_records),
        'order_lines': fingerprint(order_lines, ['order_id','product_id','name','product_qty','product_uom',
            'price_unit','taxes_id','create_date','write_date'], max_records),
    }
    if any(value.get('status') == 'BLOCKED_VOLUME' for value in report['historical_fingerprints'].values()):
        report['errors'].append('FINGERPRINT_VOLUME_LIMIT_REQUIRES_REVIEW')
    report['history_counts'] = {
        'purchase_requests': len(requests), 'request_lines': len(lines),
        'lines_with_rfq_link': len(lines.filtered('purchase_order_line_id')),
        'approved_without_current_link': len(requests.filtered(lambda r: r.request_status == 'approved'
                                                and not r.product_line_ids.purchase_order_line_id)),
    }
    if 'vataga_rfq_created' in model._fields:
        report['history_counts']['linked_requests_without_vataga_marker'] = len(requests.filtered(
            lambda r: r.product_line_ids.purchase_order_line_id and not r.vataga_rfq_created))
    report['warning'] = ('A missing historical RFQ link cannot distinguish never-created from previously-deleted RFQ. '
                         'Do not re-form such production requests without historical audit. '
                         'Hashes do not prove role isolation or first-install success; background activity may change them.')
    report['status'] = 'BLOCKED' if report['errors'] else 'METADATA_PASS_RUNTIME_NOT_PROVEN'
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, default=str))
    return report


if 'env' in globals():
    production_install_diagnostic(env)
