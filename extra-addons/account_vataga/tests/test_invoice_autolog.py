from html import unescape
from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command
from odoo.tests import tagged
from odoo.tools.misc import formatLang

from .common import InvoiceHeaderAnalyticsCommon
from ..controllers import mail_thread


@tagged('post_install', '-at_install')
class TestInvoiceAutolog(InvoiceHeaderAnalyticsCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.subtype = cls.env.ref('account_vataga.mt_invoice_autolog')

    def _logs(self, invoice):
        return self.env['mail.message'].search([
            ('model', '=', 'account.move'), ('res_id', '=', invoice.id),
            ('subtype_id', '=', self.subtype.id),
        ])

    def _edit(self, record, vals, label):
        invoice = record if record._name == 'account.move' else record.move_id
        before = self._logs(invoice)
        record.write(vals)
        messages = self._logs(invoice) - before
        self.assertTrue(messages)
        body = unescape(' '.join(messages.mapped('body')))
        self.assertIn(label, body)
        self.assertIn('→', body)
        before = self._logs(invoice)
        record.write(vals)
        self.assertEqual(self._logs(invoice), before, 'Same values must not create logs')
        return body

    def test_header_allowlist(self):
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
                self._edit(invoice, {name: value}, label)

    def test_line_allowlist(self):
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
                self._edit(line, {name: value}, label)
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
        self.assertIn('Додано рядок: Послуги', unescape(str(created.body)))
        self.assertIn(amount, unescape(str(created.body)))
        before = self._logs(invoice)
        line.unlink()
        deleted = self._logs(invoice) - before
        self.assertEqual(len(deleted), 1)
        self.assertIn('Видалено рядок: Послуги', unescape(str(deleted.body)))
        self.assertIn(amount, unescape(str(deleted.body)))

    def test_tax_names_and_no_commands(self):
        invoice = self._create_header_invoice(headers={})
        tax = self.company_data['default_tax_sale']
        body = self._edit(invoice.invoice_line_ids, {
            'tax_ids': [Command.set(tax.ids)],
        }, 'Податки')
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
        self.assertEqual(len(messages), 1)
        body = unescape(str(messages.body))
        self.assertIn('Аналітику', body)
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
        body = self._edit(invoice.invoice_line_ids, {'analytic_distribution': distribution}, 'Аналітику')
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
        self._edit(invoice, {'project_account_id': self.replacement_project.id}, 'Аналітику')
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
        self._edit(line, {'name': 'Explicit user description'}, 'Опис')

    def test_invoice_refund_and_receipt_scope(self):
        for move_type in ('out_invoice', 'in_invoice', 'out_refund', 'in_refund',
                          'out_receipt', 'in_receipt'):
            with self.subTest(move_type=move_type):
                invoice = self._create_header_invoice(headers={}, move_type=move_type)
                self.assertEqual(len(self._logs(invoice)), 1)
                self._edit(invoice, {'ref': 'Scope test'}, 'Референс')

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
            self.assertEqual(len(self._logs(move) - before[move.id]), 1)
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
