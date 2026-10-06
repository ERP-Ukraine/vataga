from datetime import date
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from odoo.tests.common import BaseCase

from ..wizard import account_payment_register


class TestPaymentPurpose(BaseCase):
    """Unit tests of purpose formatting with the invoice tax_totals payload."""

    def setUp(self):
        super().setUp()
        translations = {
            'without VAT': 'без ПДВ',
            'uah': 'грн',
            'incl. %s = %s': 'в т.ч. %s = %s',
            'Payment is reasonable. Ref №%s in %s, %s.':
                'Оплата згідно рах. №%s від %s, %s.',
        }
        self.translation_patch = patch.object(
            account_payment_register, '_', side_effect=translations.__getitem__,
        )
        self.translation_patch.start()
        self.addCleanup(self.translation_patch.stop)
        model = account_payment_register.AccountPaymentRegister
        self.wizard = SimpleNamespace(percent_of_amount=0, ensure_one=lambda: None)
        for method in ('_prepare_tax_info', '_prepare_purpose_pumb', '_prepare_purpose_dcu'):
            setattr(self.wizard, method, MethodType(getattr(model, method), self.wizard))
        self.wizard._format_date = model._format_date

    @staticmethod
    def _group(name='Не є ПДВ', amount=0.0):
        return {
            'tax_group_name': name,
            'tax_group_amount': amount,
            'tax_group_base_amount': 8900.0,
        }

    def _move(self, groups, move_type='in_invoice'):
        return SimpleNamespace(
            ref='329', invoice_date=date(2026, 9, 29), move_type=move_type,
            tax_totals={'groups_by_subtotal': groups},
            currency_id=SimpleNamespace(fiscal_country_codes='UA'),
            sale_ua_contract_id=False, ua_contract_id=False,
        )

    def _assert_pumb(self, move, tax_text):
        self.assertEqual(
            self.wizard._prepare_purpose_pumb(move),
            'Оплата згідно рах. №329 від 29.09.2026, %s.' % tax_text,
        )

    def test_supplier_bill_explicit_no_vat(self):
        self._assert_pumb(self._move({'Untaxed': [self._group()]}), 'без ПДВ')

    def test_bill_without_tax_groups(self):
        self._assert_pumb(self._move({}), 'без ПДВ')

    def test_regular_vat(self):
        self._assert_pumb(
            self._move({'Untaxed': [self._group('ПДВ 20%', 1780.0)]}),
            'в т.ч. ПДВ 20% = 1780.00 грн',
        )

    def test_partial_vat_payment(self):
        self.wizard.percent_of_amount = 50
        self._assert_pumb(
            self._move({'Untaxed': [self._group('ПДВ 20%', 1780.0)]}),
            'в т.ч. ПДВ 20% = 890.00 грн',
        )

    def test_other_zero_amount_taxes_are_preserved(self):
        for name in ('ПДВ 0%', 'ПДВ 20%', 'Інший податок'):
            with self.subTest(name=name):
                self._assert_pumb(
                    self._move({'Untaxed': [self._group(name)]}),
                    'в т.ч. %s = 0.00 грн' % name,
                )

    def test_mixed_groups_preserve_first_group_in_both_orders(self):
        no_vat = self._group()
        for amount in (0.0, 1780.0):
            vat = self._group('ПДВ 20%', amount)
            for first, second in ((no_vat, vat), (vat, no_vat)):
                for separate_subtotals in (False, True):
                    with self.subTest(amount=amount, first=first, separate=separate_subtotals):
                        groups = (
                            {'First': [first], 'Second': [second]}
                            if separate_subtotals else {'Untaxed': [first, second]}
                        )
                        self._assert_pumb(
                            self._move(groups),
                            'в т.ч. %s = %.2f грн' % (
                                first['tax_group_name'], first['tax_group_amount'],
                            ),
                        )

    def test_nonzero_no_vat_group_is_preserved(self):
        self._assert_pumb(
            self._move({'Untaxed': [self._group(amount=1.0)]}),
            'в т.ч. Не є ПДВ = 1.00 грн',
        )

    def test_other_move_types_are_preserved(self):
        for move_type in ('out_invoice', 'in_refund', 'out_refund', 'entry'):
            with self.subTest(move_type=move_type):
                self._assert_pumb(
                    self._move({'Untaxed': [self._group()]}, move_type=move_type),
                    'в т.ч. Не є ПДВ = 0.00 грн',
                )

    def test_dku_keeps_original_tax_text(self):
        params = SimpleNamespace(get_param=lambda key, default: '${tax_info}')
        params.sudo = lambda: params
        self.wizard.env = {'ir.config_parameter': params}
        move = self._move({'Untaxed': [self._group()]})
        self._assert_pumb(move, 'без ПДВ')
        self.assertEqual(
            self.wizard._prepare_purpose_dcu(move), 'в т.ч. Не є ПДВ = 0.00 грн',
        )
