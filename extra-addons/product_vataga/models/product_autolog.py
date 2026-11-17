from collections import defaultdict
from contextlib import contextmanager

from markupsafe import Markup

from odoo import api, models
from odoo.exceptions import AccessError
from odoo.tools import html2plaintext
from odoo.tools.misc import clean_context


SUBTYPE = 'product_vataga.mt_product_autolog'
COMPANY_SUBTYPE = 'product_vataga.mt_product_autolog_company'
BATCH = 'vataga_product_autolog_batch'
SKIP = 'vataga_product_autolog_skip'
VARIANT_TOGGLE = 'vataga_product_variant_toggle'
INTERNAL = object()
COST_RECOMPUTE = 'vataga_product_cost_recompute'
PRODUCT_MODELS = ('product.template', 'product.product')
LABELS = {
    'name': 'Назва', 'default_code': 'Артикул', 'barcode': 'Штрихкод',
    'categ_id': 'Категорія', 'detailed_type': 'Тип товару', 'active': 'Активний',
    'uom_id': 'Одиниця виміру', 'uom_po_id': 'Одиниця виміру закупівлі',
    'list_price': 'Ціна продажу', 'standard_price': 'Собівартість',
    'description': 'Опис', 'description_sale': 'Опис для продажу',
    'description_purchase': 'Опис для закупівлі', 'responsible_id': 'Відповідальний',
    'weight': 'Вага', 'volume': 'Обʼєм', 'sequence': 'Порядок',
    'product_id': 'Товар', 'partner_id': 'Постачальник', 'qty': 'Кількість',
    'product_name': 'Назва у постачальника', 'product_code': 'Артикул у постачальника',
    'min_qty': 'Мінімальна кількість', 'price': 'Ціна', 'discount': 'Знижка',
    'currency_id': 'Валюта', 'date_start': 'Початок дії', 'date_end': 'Кінець дії',
    'delay': 'Термін поставки', 'attribute_id': 'Атрибут', 'value_ids': 'Значення',
    'price_extra': 'Доплата', 'exclude_for': 'Виключення',
    'sale_ok': 'Можна продавати', 'purchase_ok': 'Можна купувати',
    'sales': 'Продаж', 'purchase': 'Закупівля', 'company_id': 'Компанія',
    'product_template_attribute_value_id': 'Значення атрибута',
    'tracking': 'Відстеження товару', 'route_ids': 'Маршрути',
    'taxes_id': 'Податки продажу', 'supplier_taxes_id': 'Податки закупівлі',
    'product_tag_ids': 'Мітки товару', 'additional_product_tag_ids': 'Додаткові мітки',
}
TYPE_LABELS = {'consu': 'Витратний товар', 'product': 'Складський товар', 'service': 'Послуга'}
TRACKED_TYPES = {'boolean', 'char', 'date', 'datetime', 'float', 'html', 'integer',
                 'many2many', 'many2one', 'monetary', 'selection', 'text'}
IGNORED_FIELDS = {
    'id', 'create_uid', 'create_date', 'write_uid', 'write_date', 'display_name',
    'product_tmpl_id', 'type', 'combination_indices', 'product_template_attribute_value_ids',
    'product_template_variant_value_ids', 'color', 'last_website_so_id',
}
RELATED_MODELS = {
    'product.packaging', 'product.supplierinfo', 'product.template.attribute.line',
    'product.template.attribute.value', 'product.template.attribute.exclusion', 'product.analog',
}
EMPTY = 'Порожньо'


def autolog_subtype_ids(env):
    model_data = env['ir.model.data']
    return tuple(
        model_data._xmlid_to_res_id(xmlid, raise_if_not_found=False)
        for xmlid in (SUBTYPE, COMPANY_SUBTYPE)
    )


def skipped(records):
    ctx = records.env.context
    return (
        ctx.get(SKIP) is INTERNAL
        or ctx.get('tracking_disable')
        or not records.env.registry.ready
        or not records.env.user._is_internal()
    )


def business_fields(records, names):
    allowed = set(records.check_field_access_rights('read', None))
    for name in names:
        field = records._fields.get(name)
        if not field or name not in allowed or name in IGNORED_FIELDS:
            continue
        # Chatter bodies can be read by any internal reader of the product.
        if field.groups and field.groups != 'base.group_user':
            continue
        if name.startswith(('message_', 'activity_', 'access_', 'website_message_')):
            continue
        if field.type not in TRACKED_TYPES:
            continue
        if field.inverse or (field.store and not field.readonly) or (
            field.company_dependent and not field.readonly
        ):
            yield name


def ukrainian(records):
    lang = records.env['res.lang']._lang_get('uk_UA')
    return records.with_context(lang='uk_UA') if lang else records


def snapshot(record, names):
    values = {}
    for name in names:
        field = record._fields[name]
        value = record[name]
        if field.type in ('many2one', 'many2many'):
            raw = tuple(sorted(value.ids))
            try:
                display = ', '.join(value.sorted('id').mapped('display_name'))
            except AccessError:
                display = 'Обмежений доступ'
        elif field.type == 'boolean':
            raw, display = value, 'Так' if value else 'Ні'
        elif field.type == 'selection':
            choices = TYPE_LABELS if name == 'detailed_type' else dict(
                field._description_selection(ukrainian(record).env)
            )
            raw, display = value, choices.get(value, value)
        elif field.type == 'html':
            raw, display = value or False, html2plaintext(value) if value else EMPTY
        else:
            raw = value if value or isinstance(value, (float, int)) else False
            display = '' if value is False or value is None else str(value)
        values[name] = (raw, display or EMPTY)
    return values


def label(record, name):
    return LABELS.get(name) or record._fields[name]._description_string(record.env)


def changes(record, before, after):
    return [
        '%s: було «%s», стало «%s»' % (label(record, name), old[1], after[name][1])
        for name, old in before.items() if name in after and old[0] != after[name][0]
    ]


class ProductAutologBatch:
    """Collect net changes for the outer operation and post once per card."""

    def __init__(self, origin, is_product=False):
        self.env = origin.env
        self.origin = origin if is_product else None
        self.entries = defaultdict(list)
        self.lines = {}
        self.variants_by_template = defaultdict(list)
        if is_product and origin._name == 'product.product':
            for product in origin:
                self.variants_by_template[product.product_tmpl_id.id].append(product.id)

    def destination(self, owner, company_id):
        if owner._name == 'product.template':
            variants = self.variants_by_template.get(owner.id, [])
            if len(variants) == 1:
                return ('product.product', variants[0], company_id)
        elif self.origin is not None and self.origin._name == 'product.template':
            if owner.product_tmpl_id in self.origin:
                return ('product.template', owner.product_tmpl_id.id, company_id)
        return (owner._name, owner.id, company_id)

    def add_product_changes(self, record, before, after):
        fields_by_company = defaultdict(dict)
        for name in before:
            field = record._fields[name]
            company_dependent = field.company_dependent or (
                field.inherited and field.related_field.company_dependent
            ) or name == 'standard_price'
            company = self.env.company if company_dependent else record.company_id
            fields_by_company[company.id][name] = before[name]
        for company_id, values in fields_by_company.items():
            self.entries[record._name, record.id, company_id].extend(changes(record, values, after))

    def add_line(self, kind, record, before, after):
        key = (record._name, record.id)
        if key in self.lines:
            before = self.lines[key][2]
        self.lines[key] = kind, record, before, after

    def post(self):
        for kind, record, before, after in self.lines.values():
            old_owner, old_title, old_values = before
            new_owner, new_title, new_values = after
            if old_owner and old_owner != new_owner:
                self.entries[old_owner].append('Видалено %s «%s»' % (kind, old_title))
            if new_owner and new_owner != old_owner:
                details = ', '.join('%s: %s' % (label(record, name), value[1])
                                    for name, value in new_values.items())
                self.entries[new_owner].append('Додано %s «%s» (%s)' % (kind, new_title, details))
            if new_owner and new_owner == old_owner:
                diff = changes(record, old_values, new_values)
                if diff:
                    self.entries[new_owner].append('Змінено %s «%s». %s' % (kind, old_title, '. '.join(diff)))

        if not any(self.entries.values()):
            return
        subtype_id, company_subtype_id = autolog_subtype_ids(self.env)
        if not subtype_id or not company_subtype_id:
            # Code can start on an existing database before the module data is upgraded.
            return
        messages = []
        author_id, email_from = self.env['mail.thread']._message_compute_author(
            None, None, raise_on_email=False,
        )
        for model in PRODUCT_MODELS:
            entries = [(record_id, company_id, lines)
                       for (name, record_id, company_id), lines in self.entries.items() if name == model and lines]
            if not entries:
                continue
            Model = self.env[model].with_context(clean_context(self.env.context))
            if not Model.check_access_rights('read', raise_exception=False):
                continue
            allowed_ids = set(Model.browse(list({entry[0] for entry in entries})).exists()._filter_access_rules('read').ids)
            for record_id, company_id, lines in entries:
                if record_id not in allowed_ids:
                    continue
                body = Markup('<p><strong>Змінено товар:</strong></p><ul>%s</ul>') % Markup().join(
                    Markup('<li>%s</li>') % line for line in lines
                )
                # The underlying line mutation is already authorized. The user may have
                # write access to supplier prices and only read access to the card.
                messages.append({
                    'model': model, 'res_id': record_id, 'body': body,
                    'subtype_id': company_subtype_id if company_id else subtype_id,
                    'author_id': author_id, 'email_from': email_from, 'message_type': 'notification',
                    'is_internal': True, 'record_company_id': company_id,
                })
        if messages:
            self.env['mail.message'].sudo().with_context(clean_context(self.env.context)).create(messages)


def product_write(records, vals, write):
    if not records or skipped(records) or isinstance(records.env.context.get(BATCH), ProductAutologBatch):
        return write(records, vals)
    aliases = {'type': 'detailed_type', 'lst_price': 'list_price'}
    field_names = dict.fromkeys(aliases.get(name, name) for name in vals)
    if records.env.context.get('disable_auto_svl') or records.env.context.get(COST_RECOMPUTE) is INTERNAL:
        field_names.pop('standard_price', None)
    native = records._track_get_fields()
    # Same-card native tracking continues to own the fields it already logs.
    names = [name for name in business_fields(records, field_names) if name not in native]
    related = any(records._fields.get(name) and records._fields[name].type == 'one2many'
                  and records._fields[name].comodel_name in RELATED_MODELS for name in vals)
    if not names and not related and not {'active', 'attribute_line_ids'} & vals.keys():
        return write(records, vals)
    localized = ukrainian(records)
    # Read translatable values in the language of the write, not the message.
    before = {record.id: snapshot(record, names) for record in records} if names else {}
    batch = ProductAutologBatch(records, is_product=True)
    result = write(records.with_context(**{BATCH: batch}), vals)
    if names:
        for record in records.exists():
            batch.add_product_changes(localized.browse(record.id), before[record.id], snapshot(record, names))
    batch.post()
    return result


EMPTY_LINE = (None, '', {})


def line_states(records, batch, fields, owner, title, visible):
    names = list(business_fields(records, fields))
    result = {}
    for record in ukrainian(records):
        product = owner(record)
        if not product or not visible(record):
            continue
        company = (record.company_id if 'company_id' in record._fields else False) or product.company_id
        result[record.id] = batch.destination(product, company.id), title(record), snapshot(record, names)
    return result


@contextmanager
def related_audit(records, kind, fields, owner, title, visible=lambda record: True):
    """Track line lifecycle, including moves between owners and soft deletion."""
    batch = records.env.context.get(BATCH)
    outer = not isinstance(batch, ProductAutologBatch)
    if outer:
        batch = ProductAutologBatch(records)
    before = line_states(records, batch, fields, owner, title, visible)
    outcome = {'records': records.with_context(**{BATCH: batch})}
    yield outcome
    remaining = outcome['records'].exists()
    after = line_states(remaining, batch, fields, owner, title, visible)
    for record_id in before.keys() | after.keys():
        batch.add_line(kind, records.browse(record_id), before.get(record_id, EMPTY_LINE),
                       after.get(record_id, EMPTY_LINE))
    if outer:
        batch.post()


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.model_create_multi
    def create(self, vals_list):
        return super(ProductTemplate, self.with_context(**{SKIP: INTERNAL})).create(vals_list).with_env(self.env)

    def write(self, vals):
        if self.env.context.get(VARIANT_TOGGLE) is INTERNAL:
            return super().write(vals)
        return product_write(self, vals, lambda records, values: super(ProductTemplate, records).write(values))

    def unlink(self):
        return super(ProductTemplate, self.with_context(**{SKIP: INTERNAL})).unlink()

    def _create_variant_ids(self):
        records = self.with_context(**{SKIP: INTERNAL})
        return super(ProductTemplate, records)._create_variant_ids()


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.model_create_multi
    def create(self, vals_list):
        return super(ProductProduct, self.with_context(**{SKIP: INTERNAL})).create(vals_list).with_env(self.env)

    def write(self, vals):
        return product_write(self, vals, lambda records, values: super(ProductProduct, records).write(values))

    def unlink(self):
        return super(ProductProduct, self.with_context(**{SKIP: INTERNAL})).unlink()

    def toggle_active(self):
        records = self.with_context(**{VARIANT_TOGGLE: INTERNAL})
        return super(ProductProduct, records).toggle_active()

    def _set_price_from_bom(self, *args, **kwargs):
        records = self.with_context(**{COST_RECOMPUTE: INTERNAL})
        return super(ProductProduct, records)._set_price_from_bom(*args, **kwargs)
