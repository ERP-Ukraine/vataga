from html import unescape
from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import tagged
from odoo.tools.misc import formatLang

from .common import InvoiceHeaderAnalyticsCommon
from ..controllers import mail_thread
from ..models.account_invoice_autolog import business_fields, format_value, snapshot, changes
from ..models.account_invoice_autolog import NATIVE_ACCOUNTING_NOISE_FIELDS, tracking_context


@tagged('post_install', '-at_install')
class TestInvoiceAutolog(InvoiceHeaderAnalyticsCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US'))
        cls.subtype = cls.env.ref('account_vataga.mt_invoice_autolog')

    def _logs(self, invoice):
        return self.env['mail.message'].search([
            ('model', '=', 'account.move'), ('res_id', '=', invoice.id),
            ('subtype_id', '=', self.subtype.id),
        ])

    def _flush_native_tracking(self):
        self.env.flush_all()
        self.env.cr.precommit.run()

    def _native_values(self, invoice):
        return self.env['mail.tracking.value'].search([
            ('mail_message_id.model', '=', 'account.move'),
            ('mail_message_id.res_id', '=', invoice.id),
        ])

    def _invoice_with_native_tracking(self, **kwargs):
        # Accounting test fixtures can disable tracking: explicitly exercise it.
        invoice = self._create_header_invoice(headers={}, **kwargs).with_context(
            tracking_disable=False, mail_notrack=False,
        )
        self._flush_native_tracking()
        return invoice

    def _assert_no_accounting_tracking(self, invoice, before):
        self._flush_native_tracking()
        added = self._native_values(invoice) - before
        self.assertFalse(set(added.field_id.mapped('name')) & NATIVE_ACCOUNTING_NOISE_FIELDS)

    def test_quantity_recompute_has_no_accounting_tracking(self):
        for with_partner in (False, True):
            with self.subTest(with_partner=with_partner):
                tax = self.company_data['default_tax_sale']
                invoice = self._invoice_with_native_tracking(invoice_line_ids=[
                    Command.create(self._invoice_line_vals(
                        quantity=quantity, price_unit=price, tax_ids=[Command.set(tax.ids)],
                    )) for quantity, price in ((12, 100), (25, 200))
                ])
                # Native line tracking runs only for moves that have been posted.
                invoice.action_post()
                invoice.button_draft()
                self._flush_native_tracking()
                self.assertTrue(invoice.posted_before)
                technical = invoice.line_ids - invoice.invoice_line_ids
                self.assertTrue(technical.filtered(lambda line: line.display_type == 'tax'))
                self.assertTrue(technical.filtered(lambda line: line.display_type == 'payment_term'))
                old_balances = {line.id: line.balance for line in technical}
                before = self._logs(invoice)
                native_before = self._native_values(invoice)
                messages_before = invoice.message_ids
                commands = [Command.update(line.id, {
                    'quantity': quantity,
                    **({'partner_id': self.partner_b.id} if with_partner else {}),
                }) for line, quantity in zip(invoice.invoice_line_ids.sorted('id'), (25, 12))]
                invoice.write({
                    'invoice_line_ids': commands,
                    **({'partner_id': self.partner_b.id} if with_partner else {}),
                })
                self._assert_no_accounting_tracking(invoice, native_before)
                self.assertFalse((invoice.message_ids - messages_before).filtered(
                    lambda message: 'Journal Item' in str(message.body)
                ))
                self.assertTrue(any(line.balance != old_balances[line.id]
                                    for line in technical.exists()))
                messages = self._logs(invoice) - before
                line_messages = messages.filtered(lambda message: 'В рядку' in str(message.body))
                self.assertEqual(len(line_messages), 2)
                self.assertEqual(len(messages), 3 if with_partner else 2)
                for message in line_messages:
                    self.assertIn('Quantity:', str(message.body))
                    self.assertNotIn('Partner:', str(message.body))

    def test_direct_line_edit_suppresses_sibling_accounting_noise(self):
        invoice = self._invoice_with_native_tracking()
        invoice.action_post()
        invoice.button_draft()
        self._flush_native_tracking()
        native_before = self._native_values(invoice)
        custom_before = self._logs(invoice)
        invoice.invoice_line_ids.write({'quantity': 8})
        self._assert_no_accounting_tracking(invoice, native_before)
        self.assertEqual(len(self._logs(invoice) - custom_before), 1)

    def test_posting_preserves_status_without_accounting_noise(self):
        invoice = self._invoice_with_native_tracking()
        invoice.action_post()
        invoice.button_draft()
        self._flush_native_tracking()
        native_before = self._native_values(invoice)
        custom_before = self._logs(invoice)
        invoice.action_post()
        self._assert_no_accounting_tracking(invoice, native_before)
        self.assertIn('state', (self._native_values(invoice) - native_before).field_id.mapped('name'))
        self.assertEqual(self._logs(invoice), custom_before)

    def test_accounting_tracking_scope_on_invoice_and_entry(self):
        invoice = self._invoice_with_native_tracking()
        entry = self.env['account.move'].create({'move_type': 'entry', 'line_ids': [
            Command.create({'account_id': self.company_data['default_account_revenue'].id,
                            'name': 'Independent journal item'}),
        ]})
        other_invoice = self._invoice_with_native_tracking()
        for line in (invoice.invoice_line_ids | invoice.line_ids.filtered(
                lambda record: record.display_type == 'payment_term') | entry.line_ids
                | other_invoice.invoice_line_ids):
            tracked = line.fields_get(['balance'], attributes=['string', 'type', 'currency_field'])
            initial = {'balance': line.balance + 1}
            # Direct hook calls test a real tracked field outside the custom scope.
            self.assertTrue(line._mail_track(tracked, initial)[1])
            scoped = line.with_context(**tracking_context(invoice, lines=invoice.invoice_line_ids))
            values = scoped._mail_track(tracked, initial)[1]
            if line.move_id == invoice:
                self.assertFalse(values)
            else:
                self.assertTrue(values)

    def test_partner_tracking_and_header_propagation(self):
        invoice = self._invoice_with_native_tracking(invoice_line_ids=[
            Command.create(self._invoice_line_vals()) for _ in range(3)
        ])
        before = self._logs(invoice)
        native_before = self._native_values(invoice)
        invoice.write({'partner_id': self.partner_b.id})
        self._flush_native_tracking()
        messages = self._logs(invoice) - before
        self.assertEqual(len(messages), 1)
        self.assertIn('Змінено ', str(messages.body))
        self.assertNotIn('В рядку', str(messages.body))
        self.assertEqual(invoice.invoice_line_ids.partner_id, self.partner_b.commercial_partner_id)
        self.assertNotIn('partner_id', (self._native_values(invoice) - native_before).field_id.mapped('name'))

    def test_two_header_fields_no_native_duplicates(self):
        invoice = self._invoice_with_native_tracking()
        bank = self.env['res.partner.bank'].create({
            'acc_number': 'AUTOLOG-RECIPIENT-BANK', 'partner_id': invoice.company_id.partner_id.id,
        })
        before = self._logs(invoice)
        native_before = self._native_values(invoice)
        # Cover precommit values already prepared by a preceding read/compute.
        invoice._track_prepare(iter(['partner_id', 'partner_bank_id']))
        invoice.write({'partner_id': self.partner_b.id, 'partner_bank_id': bank.id})
        self._flush_native_tracking()
        messages = self._logs(invoice) - before
        self.assertEqual(len(messages), 1)
        self.assertIn(invoice._fields['partner_id'].string, str(messages.body))
        self.assertIn(invoice._fields['partner_bank_id'].string, str(messages.body))
        tracked = (self._native_values(invoice) - native_before).field_id.mapped('name')
        self.assertFalse({'partner_id', 'partner_bank_id'} & set(tracked))

    def test_native_status_survives_custom_write_and_posting(self):
        invoice = self._invoice_with_native_tracking()
        before = self._logs(invoice)
        native_before = self._native_values(invoice)
        invoice.write({'ref': 'Custom change before posting'})
        invoice.action_post()
        self._flush_native_tracking()
        self.assertEqual(invoice.state, 'posted')
        self.assertEqual(len(self._logs(invoice) - before), 1)
        tracked = (self._native_values(invoice) - native_before).field_id.mapped('name')
        self.assertIn('state', tracked)
        self.assertNotIn('ref', tracked)

    def test_posted_line_label_has_only_custom_tracking(self):
        invoice = self._invoice_with_native_tracking()
        invoice.action_post()
        self._flush_native_tracking()
        for through_invoice in (False, True):
            with self.subTest(through_invoice=through_invoice):
                before = self._logs(invoice)
                native_before = self._native_values(invoice)
                vals = {'name': 'New invoice line label %s' % through_invoice}
                if through_invoice:
                    invoice.write({'invoice_line_ids': [Command.update(invoice.invoice_line_ids.id, vals)]})
                else:
                    invoice.invoice_line_ids.write(vals)
                self._flush_native_tracking()
                messages = self._logs(invoice) - before
                self.assertEqual(len(messages), 1)
                self.assertIn('В рядку', str(messages.body))
                self.assertNotIn('name', (self._native_values(invoice) - native_before).field_id.mapped('name'))

    def test_real_line_edits_survive_header_change(self):
        invoice = self._invoice_with_native_tracking()
        before = self._logs(invoice)
        invoice.write({
            'partner_id': self.partner_b.id,
            'invoice_line_ids': [Command.update(invoice.invoice_line_ids.id, {
                'partner_id': self.partner_b.commercial_partner_id.id,
                'quantity': 7, 'price_unit': 123,
                'analytic_distribution': {str(self.replacement_project.id): 100},
            })],
        })
        self._flush_native_tracking()
        messages = self._logs(invoice) - before
        self.assertEqual(len(messages), 2)
        line_message = messages.filtered(lambda message: 'В рядку' in str(message.body))
        self.assertEqual(len(line_message), 1)
        for name in ('quantity', 'price_unit', 'analytic_distribution'):
            self.assertIn(invoice.invoice_line_ids._fields[name].string, str(line_message.body))
        self.assertNotIn('Partner:', str(line_message.body))

    def test_web_payload_partner_only_creates_header_message(self):
        invoice = self._invoice_with_native_tracking(invoice_line_ids=[
            Command.create(self._invoice_line_vals()) for _ in range(3)
        ])
        before = self._logs(invoice)
        invoice.write({
            'partner_id': self.partner_b.id,
            'invoice_line_ids': [Command.update(line.id, {
                'partner_id': self.partner_b.commercial_partner_id.id,
            }) for line in invoice.invoice_line_ids],
        })
        self._flush_native_tracking()
        messages = self._logs(invoice) - before
        self.assertEqual(len(messages), 1)
        self.assertIn(invoice._fields['partner_id'].string, str(messages.body))
        self.assertNotIn('В рядку', str(messages.body))

    def test_web_payload_partner_and_two_line_quantities(self):
        invoice = self._invoice_with_native_tracking(invoice_line_ids=[
            Command.create(self._invoice_line_vals(quantity=23)),
            Command.create(self._invoice_line_vals(quantity=15)),
        ])
        first, second = invoice.invoice_line_ids.sorted('id')
        before = self._logs(invoice)
        invoice.write({
            'partner_id': self.partner_b.id,
            'invoice_line_ids': [
                Command.update(first.id, {
                    'partner_id': self.partner_b.commercial_partner_id.id, 'quantity': 12,
                }),
                Command.update(second.id, {
                    'partner_id': self.partner_b.commercial_partner_id.id, 'quantity': 25,
                }),
            ],
        })
        self._flush_native_tracking()
        messages = self._logs(invoice) - before
        line_messages = messages.filtered(lambda message: 'В рядку' in str(message.body))
        self.assertEqual(len(messages), 3)
        self.assertEqual(len(line_messages), 2)
        self.assertIn(invoice._fields['partner_id'].string, str((messages - line_messages).body))
        for message in line_messages:
            self.assertIn(first._fields['quantity'].string, str(message.body))
            self.assertNotIn(first._fields['partner_id'].string + ':', str(message.body))
        self.assertEqual(first.quantity, 12)
        self.assertEqual(second.quantity, 25)

    def test_line_partner_without_header_change_remains_logged(self):
        invoice = self._invoice_with_native_tracking()
        before = self._logs(invoice)
        invoice.write({'invoice_line_ids': [Command.update(invoice.invoice_line_ids.id, {
            'partner_id': self.partner_b.commercial_partner_id.id,
        })]})
        messages = self._logs(invoice) - before
        self.assertEqual(len(messages), 1)
        self.assertIn('В рядку', str(messages.body))
        self.assertIn(invoice.invoice_line_ids._fields['partner_id'].string, str(messages.body))

    def test_currency_and_related_header_mirrors(self):
        invoice = self._invoice_with_native_tracking()
        line = invoice.invoice_line_ids
        mirrors = line._invoice_autolog_header_mirrors({'partner_id', 'currency_id', 'date', 'invoice_date', 'ref'})
        self.assertTrue({'partner_id', 'currency_id'} <= mirrors)
        for name in ('date', 'invoice_date', 'ref'):
            # Odoo 17 declares these as readonly related fields: already excluded.
            self.assertTrue(name not in business_fields(line) or name in mirrors)
        self.assertNotIn('analytic_distribution', line._invoice_autolog_header_mirrors(self.headers))
        currency = self.env['res.currency'].with_context(active_test=False).search([
            ('id', '!=', invoice.currency_id.id),
        ], limit=1)
        currency.active = True
        before = self._logs(invoice)
        invoice.write({
            'currency_id': currency.id,
            'invoice_line_ids': [Command.update(line.id, {'currency_id': currency.id})],
        })
        self._flush_native_tracking()
        messages = self._logs(invoice) - before
        self.assertEqual(len(messages), 1)
        self.assertIn(invoice._fields['currency_id'].string, str(messages.body))
        self.assertNotIn('В рядку', str(messages.body))

    def test_journal_entry_native_tracking_is_untouched(self):
        invoice = self._invoice_with_native_tracking()
        entry = self.env['account.move'].create({'move_type': 'entry'}).with_context(
            tracking_disable=False, mail_notrack=False,
        )
        self._flush_native_tracking()
        native_before = self._native_values(entry)
        (invoice | entry).write({'ref': 'Mixed batch'})
        self._flush_native_tracking()
        self.assertIn('ref', (self._native_values(entry) - native_before).field_id.mapped('name'))
        self.assertFalse(self._logs(entry))

    def test_odoo17_field_access_and_form_save(self):
        invoice = self._create_header_invoice(headers={}, invoice_line_ids=[
            Command.create(self._invoice_line_vals(quantity=1)),
        ])
        line = invoice.invoice_line_ids
        self.assertEqual(business_fields(invoice, ['ref', 'partner_id']), ['ref', 'partner_id'])
        self.assertEqual(business_fields(line, ['quantity', 'price_unit']), ['quantity', 'price_unit'])
        self.assertIn('ref', business_fields(invoice))
        self.assertIn('quantity', business_fields(line))
        self.assertEqual(business_fields(invoice, []), [])
        self.assertEqual(business_fields(invoice, ['missing_autolog_field']), [])

        before = self._logs(invoice)
        invoice.write({'ref': 'TEST'})
        self.assertEqual(invoice.ref, 'TEST')
        self.assertEqual(len(self._logs(invoice) - before), 1)

        before = self._logs(invoice)
        line.write({'quantity': 2})
        self.assertEqual(line.quantity, 2)
        self.assertEqual(len(self._logs(invoice) - before), 1)

        before = self._logs(invoice)
        invoice.write({'invoice_line_ids': [Command.create(self._invoice_line_vals())]})
        self.assertEqual(len(invoice.invoice_line_ids), 2)
        messages = self._logs(invoice) - before
        self.assertEqual(len(messages), 1)
        self.assertIn('Додано рядок:', str(messages.body))

    def test_field_discovery_skips_group_restricted_fields(self):
        invoice = self._create_header_invoice(headers={}).with_user(self.env.ref('base.user_admin'))
        self.assertFalse(invoice.env.su)
        # An explicit forbidden field list raises in Odoo 17. Autolog discovery
        # must omit it when scanning a full model or a supplied candidate list.
        with patch.object(invoice._fields['ref'], 'groups', 'base.group_no_one'):
            with patch.object(type(invoice), 'user_has_groups', return_value=False):
                self.assertNotIn('ref', business_fields(invoice))
                self.assertEqual(business_fields(invoice, ['ref', 'partner_id']), ['partner_id'])

    def _edit(self, record, vals, label):
        invoice = record if record._name == 'account.move' else record.move_id
        before = self._logs(invoice)
        record.write(vals)
        messages = self._logs(invoice) - before
        self.assertTrue(messages)
        body = unescape(' '.join(messages.mapped('body')))
        self.assertIn(label, body)
        self.assertIn('було "', body)
        self.assertIn('", стало "', body)
        before = self._logs(invoice)
        record.write(vals)
        self.assertEqual(self._logs(invoice), before, 'Same values must not create logs')
        return body

    def test_fields_outside_previous_lists_and_grouped_header(self):
        invoice = self._create_header_invoice(headers={})
        # Neither field was part of the previous HEADER_FIELDS. No autolog
        # configuration is needed to discover either a relation or a text field.
        salesperson = self.env['res.users'].create({
            'name': 'Autolog salesperson', 'login': 'dynamic_autolog_salesperson',
            'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set(self.env.ref('base.group_user').ids)],
        })
        before = self._logs(invoice)
        vals = {'invoice_user_id': salesperson.id, 'invoice_source_email': 'supplier@example.test'}
        body = self._edit(invoice, vals, invoice._fields['invoice_source_email'].string)
        self.assertEqual(len(self._logs(invoice) - before), 1)
        self.assertIn(invoice._fields['invoice_user_id'].string, body)
        self.assertIn(salesperson.display_name, body)
        self.assertIn('supplier@example.test', body)

    def test_boolean_selection_date_formatting(self):
        invoice = self._create_header_invoice(headers={})
        before = self._logs(invoice)
        vals = {'to_check': True, 'auto_post': 'at_date', 'invoice_date': '2026-09-20'}
        body = self._edit(invoice, vals, invoice._fields['to_check'].string)
        self.assertEqual(len(self._logs(invoice) - before), 1)
        self.assertIn('було "Ні", стало "Так"', body)
        self.assertIn('2026-09-20', body)
        self.assertIn(dict(invoice._fields['auto_post']._description_selection(self.env))['at_date'], body)
        self.assertNotIn('"at_date"', body)
        self._edit(invoice, {'to_check': False}, 'було "Так", стало "Ні"')
        raw, display = format_value(invoice, 'create_date')
        self.assertEqual(raw, invoice.create_date)
        self.assertTrue(display)

    def test_technical_fields_and_metadata(self):
        invoice = self._create_header_invoice(headers={})
        line = invoice.invoice_line_ids
        for record, excluded in (
            (invoice, ['write_date', 'write_uid', 'message_ids', 'activity_ids',
                       'access_token', 'amount_total', 'amount_residual', 'payment_state',
                       'invoice_line_ids', 'line_ids', 'display_name', 'needed_terms']),
            (line, ['move_id', 'sequence', 'balance', 'debit', 'credit', 'price_subtotal',
                    'price_total', 'tax_tag_ids', 'currency_rate', 'parent_state']),
        ):
            self.assertFalse(set(business_fields(record)) & set(excluded))
        # Editable computed fields must survive the generic metadata filter.
        self.assertIn('price_unit', business_fields(line))
        self.assertIn('tax_ids', business_fields(line))
        self.assertIn('analytic_distribution', business_fields(line))
        before = self._logs(invoice)
        invoice.write({'write_date': fields.Datetime.now(), 'posted_before': True})
        line.write({'sequence': 123})
        invoice._compute_amount()
        line._compute_totals()
        self.env.flush_all()
        self.assertEqual(self._logs(invoice), before)

    def test_plain_text_separators_preserve_real_names(self):
        invoice = self._create_header_invoice(headers={})
        body = self._edit(invoice, {'ref': 'Contract 195'},
                          'було "Порожньо", стало "Contract 195"')
        self.assertNotIn('→', body)
        self.assertNotIn('—', body)
        self._edit(invoice, {'ref': False}, 'було "Contract 195", стало "Порожньо"')
        self._edit(invoice, {'ref': 'Contract A → B — original'},
                   'було "Порожньо", стало "Contract A → B — original"')

        line = invoice.invoice_line_ids
        first = self.env['account.analytic.account'].browse(self.headers['project_account_id'])
        second = self.replacement_project
        for distribution in (
            {str(first.id): 100},
            {str(first.id): 60, str(second.id): 40},
        ):
            line.write({'analytic_distribution': distribution})
            expected = '; '.join(
                '%s (%g%%)' % (self.env['account.analytic.account'].browse(int(key)).display_name, percent)
                for key, percent in sorted(distribution.items(), key=lambda item: int(item[0]))
            )
            self.assertEqual(format_value(line, 'analytic_distribution')[1], expected)
            self.assertNotIn('—', expected)
            self.assertNotIn('→', expected)
        first.name = 'Project A → B — original'
        line.write({'analytic_distribution': {str(first.id): 100}})
        self.assertEqual(format_value(line, 'analytic_distribution')[1],
                         '%s (100%%)' % first.display_name)

    def test_detailed_command_create_and_delete(self):
        invoice = self._create_header_invoice(headers={})
        distribution = {str(self.replacement_project.id): 100}
        tax = self.company_data['default_tax_sale']
        before = self._logs(invoice)
        invoice.write({'invoice_line_ids': [Command.create(self._invoice_line_vals(
            name='Detailed invoice line', quantity=5, price_unit=100, discount=10,
            analytic_distribution=distribution, tax_ids=[Command.set(tax.ids)],
        ))]})
        messages = self._logs(invoice) - before
        self.assertEqual(len(messages), 1)
        body = unescape(str(messages.body))
        line = invoice.invoice_line_ids.filtered(lambda record: record.name == 'Detailed invoice line')
        self.assertEqual(line.price_subtotal, 450)
        self.assertIn('Додано рядок:', body)
        for name in ('product_id', 'name', 'quantity', 'product_uom_id', 'price_unit',
                     'discount', 'analytic_distribution', 'account_id', 'tax_ids', 'price_subtotal'):
            self.assertIn(line._fields[name].string, body)
        for value in (line.product_id.display_name, line.product_uom_id.display_name,
                      'Detailed invoice line', '10%', self.replacement_project.display_name,
                      '100%', tax.display_name,
                      formatLang(self.env, 450, currency_obj=invoice.currency_id)):
            self.assertIn(value, body)
        self.assertNotIn('Command.', body)
        self.assertNotIn('[(0,', body)
        before = self._logs(invoice)
        invoice.write({'invoice_line_ids': [Command.delete(line.id)]})
        deleted = self._logs(invoice) - before
        self.assertEqual(len(deleted), 1)
        self.assertEqual(unescape(str(deleted.body)), body.replace('Додано рядок:', 'Видалено рядок:', 1))

    def test_multiple_line_edits_and_raw_identity(self):
        invoice = self._create_header_invoice(headers={})
        line = invoice.invoice_line_ids
        before = self._logs(invoice)
        self._edit(line, {'quantity': 7, 'name': 'Edited', 'price_unit': 123, 'blocked': True}, 'Edited')
        messages = self._logs(invoice) - before
        self.assertEqual(len(messages), 1)
        body = unescape(str(messages.body))
        self.assertNotIn('→', body)
        self.assertNotIn('—', body)
        self.assertIn('; ', body)
        for name in ('quantity', 'name', 'price_unit', 'blocked'):
            self.assertIn(line._fields[name].string, body)
        # Equal display names do not imply equal relation values.
        first = self.env['account.analytic.account'].browse(self.headers['project_account_id'])
        second = first.copy({'name': first.name})
        invoice.write({'project_account_id': first.id})
        before_values = snapshot(invoice, ['project_account_id'])
        invoice.write({'project_account_id': second.id})
        after_values = snapshot(invoice, ['project_account_id'])
        self.assertNotEqual(before_values['project_account_id'][0], after_values['project_account_id'][0])
        self.assertEqual(len(changes(invoice, before_values, after_values)), 1)

    def test_header_fields(self):
        invoice = self._create_header_invoice(headers={})
        journal = self.env['account.journal'].create({
            'name': 'Autolog sales', 'code': 'ALOG', 'type': 'sale',
            'company_id': invoice.company_id.id,
        })
        currency = self.env['res.currency'].with_context(active_test=False).search([
            ('id', '!=', invoice.currency_id.id),
        ], limit=1)
        currency.active = True
        term = self.env['account.payment.term'].create({'name': 'Autolog term'})
        for name, value, label in (
            ('partner_id', self.partner_b.id, 'Постачальника / Покупця'),
            ('invoice_date', '2026-09-20', 'Дату рахунку'),
            ('date', '2026-09-21', 'Дату обліку'),
            ('ref', 'AUTOLOG-REF', 'Референс'),
            ('currency_id', currency.id, 'Валюту'),
            ('journal_id', journal.id, 'Журнал'),
            ('invoice_payment_term_id', term.id, 'Умови оплати'),
            ('name', 'AUTOLOG/2026/0001', 'Номер'),
        ):
            with self.subTest(field=name):
                self._edit(invoice, {name: value}, invoice._fields[name].string)

    def test_line_fields(self):
        invoice = self._create_header_invoice(headers={})
        line = invoice.invoice_line_ids
        account = self.company_data['default_account_revenue'].copy({'name': 'Autolog revenue'})
        for name, value, label in (
            ('product_id', self.product_b.id, 'Товар'),
            ('name', 'New description <script>alert(1)</script>', 'Опис'),
            ('quantity', 3, 'Кількість'),
            ('product_uom_id', self.env.ref('uom.product_uom_dozen').id, 'Одиницю виміру'),
            ('price_unit', 120, 'Ціну'),
            ('discount', 10, 'Знижку'),
            ('account_id', account.id, 'Рахунок'),
        ):
            with self.subTest(field=name):
                self._edit(line, {name: value}, line._fields[name].string)
        self.assertNotIn('<script>', ''.join(self._logs(invoice).mapped('body')))

    def test_create_delete_and_currency(self):
        invoice = self._create_header_invoice(headers={})
        self.assertEqual(len(self._logs(invoice)), 1)
        before = self._logs(invoice)
        line = self.env['account.move.line'].create({
            'move_id': invoice.id, **self._invoice_line_vals(product_id=False, name='Послуги'),
        })
        amount = formatLang(self.env, line.price_subtotal, currency_obj=invoice.currency_id)
        created = self._logs(invoice) - before
        self.assertEqual(len(created), 1)
        self.assertIn('Додано рядок:', unescape(str(created.body)))
        self.assertIn('Послуги', unescape(str(created.body)))
        self.assertIn(amount, unescape(str(created.body)))
        before = self._logs(invoice)
        line.unlink()
        deleted = self._logs(invoice) - before
        self.assertEqual(len(deleted), 1)
        self.assertIn('Видалено рядок:', unescape(str(deleted.body)))
        self.assertIn('Послуги', unescape(str(deleted.body)))
        self.assertIn(amount, unescape(str(deleted.body)))

    def test_tax_names_and_no_commands(self):
        invoice = self._create_header_invoice(headers={})
        tax = self.company_data['default_tax_sale']
        body = self._edit(invoice.invoice_line_ids, {
            'tax_ids': [Command.set(tax.ids)],
        }, invoice.invoice_line_ids._fields['tax_ids'].string)
        self.assertIn(tax.display_name, body)
        self.assertNotIn(str(Command.set(tax.ids)), body)
        self.assertNotIn('[(6,', body)
        self._edit(invoice.invoice_line_ids, {'tax_ids': [Command.clear()]}, 'Порожньо')

    def test_combined_analytics_and_header_sync(self):
        invoice = self._create_header_invoice()
        old = invoice.invoice_line_ids.analytic_distribution.copy()
        before = self._logs(invoice)
        invoice.write({'project_account_id': self.replacement_project.id})
        messages = self._logs(invoice) - before
        self.assertEqual(len(messages), 2)
        body = unescape(' '.join(messages.mapped('body')))
        self.assertIn(invoice._fields['project_account_id'].string, body)
        self.assertIn(invoice.invoice_line_ids._fields['analytic_distribution'].string, body)
        self.assertIn(self.replacement_project.display_name, body)
        self.assertNotIn(str(old), body)
        for key in old:
            self.assertNotIn(key, body)
        self._assert_invoice_distribution(invoice, dict(
            self.headers, project_account_id=self.replacement_project.id,
        ))
        self._edit(invoice, dict.fromkeys(self.headers, False), 'Порожньо')
        self._assert_invoice_distribution(invoice, {})
        distribution = {','.join(str(value) for value in self.headers.values()): 65}
        body = self._edit(invoice.invoice_line_ids, {'analytic_distribution': distribution}, invoice.invoice_line_ids._fields['analytic_distribution'].string)
        self.assertIn('65%', body)
        for account in self.env['account.analytic.account'].browse(list(self.headers.values())):
            self.assertIn(account.display_name, body)
            self.assertNotRegex(body, r'(?<!\d)%s(?![\d%%])' % account.id)
        self.assertNotIn(str(distribution), body)

    def test_posting_and_posted_header_sync(self):
        invoice = self._create_header_invoice()
        before = self._logs(invoice)
        invoice.action_post()
        self.assertEqual(self._logs(invoice), before)
        self._edit(invoice, {'project_account_id': self.replacement_project.id}, invoice.invoice_line_ids._fields['analytic_distribution'].string)
        self.assertTrue(invoice.invoice_line_ids.analytic_line_ids)
        self._assert_invoice_distribution(invoice, dict(
            self.headers, project_account_id=self.replacement_project.id,
        ))

    def test_computed_write_is_quiet(self):
        invoice = self._create_header_invoice(headers={})
        line = invoice.invoice_line_ids
        before = self._logs(invoice)
        # Emulate the field protection used by ORM recomputations.
        with self.env.protecting([line._fields['name']], line):
            line.write({'name': 'Internally computed description'})
        self.assertEqual(self._logs(invoice), before)
        self._edit(line, {'name': 'Explicit user description'}, line._fields['name'].string)

    def test_invoice_refund_and_receipt_scope(self):
        for move_type in ('out_invoice', 'in_invoice', 'out_refund', 'in_refund',
                          'out_receipt', 'in_receipt'):
            with self.subTest(move_type=move_type):
                invoice = self._create_header_invoice(headers={}, move_type=move_type)
                self.assertEqual(len(self._logs(invoice)), 1)
                self._edit(invoice, {'ref': 'Scope test'}, invoice._fields['ref'].string)

    def test_technical_lines_and_journal_entry(self):
        invoice = self._create_header_invoice(headers={})
        before = self._logs(invoice)
        for display_type in ('tax', 'payment_term', 'rounding', 'cogs', 'epd'):
            with self.subTest(display_type=display_type):
                line = self.env['account.move.line'].with_context(check_move_validity=False).create({
                    'move_id': invoice.id, 'display_type': display_type,
                    'account_id': self.company_data[
                        'default_account_receivable' if display_type == 'payment_term'
                        else 'default_account_revenue'
                    ].id,
                    'date_maturity': invoice.invoice_date if display_type == 'payment_term' else False,
                    'name': 'Technical',
                })
                line.write({'name': 'Technical recompute'})
                line.unlink()
        self.assertEqual(self._logs(invoice), before)
        entry = self.env['account.move'].create({
            'move_type': 'entry',
            'line_ids': [Command.create({
                'account_id': self.company_data['default_account_revenue'].id,
                'name': 'Journal item',
            })],
        })
        entry.write({'ref': 'Journal reference'})
        entry.line_ids.write({'name': 'Changed journal item'})
        entry.line_ids.unlink()
        self.assertFalse(self._logs(entry))

    def test_author(self):
        user = self.env['res.users'].create({
            'name': 'Invoice autolog accountant', 'login': 'invoice_autolog_accountant',
            'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set(self.env.ref('account.group_account_user').ids)],
        })
        invoice = self._create_header_invoice()
        before = self._logs(invoice)
        invoice.with_user(user).write({'ref': 'By another accountant'})
        message = self._logs(invoice) - before
        self.assertEqual(message.author_id, user.partner_id)
        self.assertNotIn(user.name, str(message.body))

    def test_commands_batch_and_sections(self):
        invoices = self._create_header_invoice() | self._create_header_invoice()
        before = {move.id: self._logs(move) for move in invoices}
        invoices.write({'project_account_id': self.replacement_project.id})
        for move in invoices:
            self.assertEqual(len(self._logs(move) - before[move.id]), 2)
        invoice = invoices[0]
        before = self._logs(invoice)
        invoice.write({'invoice_line_ids': [
            Command.update(invoice.invoice_line_ids.id, {'quantity': 5, 'discount': 10}),
            Command.create(self._invoice_line_vals()),
            Command.create({'display_type': 'line_section', 'name': 'Section'}),
        ]})
        self.assertEqual(len(self._logs(invoice) - before), 3)
        before = self._logs(invoice)
        invoice.write({'invoice_line_ids': [Command.delete(invoice.invoice_line_ids[0].id)]})
        self.assertEqual(len(self._logs(invoice) - before), 1)

    def test_route_hides_only_autologs_and_tracking(self):
        invoice = self._create_header_invoice()
        note = invoice.message_post(body='User note', subtype_xmlid='mail.mt_note')
        with patch.object(mail_thread, 'request', SimpleNamespace(env=self.env)):
            controller = mail_thread.InvoiceThreadController()
            visible = controller.invoice_thread_messages('account.move', invoice.id)
            hidden = controller.invoice_thread_messages(
                'account.move', invoice.id, hide_invoice_autologs=True,
            )
        visible_ids = {message['id'] for message in visible['messages']}
        hidden_ids = {message['id'] for message in hidden['messages']}
        self.assertTrue(set(self._logs(invoice).ids) <= visible_ids)
        self.assertFalse(set(self._logs(invoice).ids) & hidden_ids)
        self.assertIn(note.id, hidden_ids)
