from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPartnerNameValidation(TransactionCase):

    def test_create_normalizes_latin_name_to_uppercase(self):
        partner = self.env["res.partner"].create({
            "name": "  vataga   trade   llc  ",
            "company_type": "company",
        })

        self.assertEqual(partner.name, "VATAGA TRADE LLC")

    def test_cyrillic_name_requires_activity_form(self):
        with self.assertRaisesRegex(
            ValidationError,
            "Для назв кирилицею обов’язково вкажіть форму власності",
        ):
            self.env["res.partner"].create({
                "name": "ВАТАГА",
                "company_type": "company",
            })

    def test_cyrillic_name_with_activity_form_is_allowed(self):
        partner = self.env["res.partner"].create({
            "name": "ватага тов",
            "company_type": "company",
        })

        self.assertEqual(partner.name, "ВАТАГА ТОВ")

    def test_write_normalizes_and_validates_name(self):
        partner = self.env["res.partner"].create({
            "name": "VATAGA TRADE LLC",
            "company_type": "company",
        })

        partner.write({"name": "  muller   logistik  "})
        self.assertEqual(partner.name, "MULLER LOGISTIK")

        with self.assertRaisesRegex(
            ValidationError,
            "Для назв кирилицею обов’язково вкажіть форму власності",
        ):
            partner.write({"name": "ВАТАГА"})

    def test_invalid_symbols_are_rejected(self):
        with self.assertRaisesRegex(
            ValidationError,
            "Дозволені спецсимволи",
        ):
            self.env["res.partner"].create({
                "name": "VATAGA/TRADE LLC",
                "company_type": "company",
            })

    def test_extended_latin_letters_are_allowed(self):
        partner = self.env["res.partner"].create({
            "name": "müller škoda łukasz rené",
            "company_type": "company",
        })

        self.assertEqual(partner.name, "MÜLLER ŠKODA ŁUKASZ RENÉ")

    def test_person_partner_is_not_validated_as_counterparty(self):
        partner = self.env["res.partner"].create({
            "name": "іван петренко",
        })

        self.assertEqual(partner.name, "іван петренко")

    def test_child_contact_is_not_validated_as_counterparty(self):
        company = self.env["res.partner"].create({
            "name": "ВАТАГА ТОВ",
            "company_type": "company",
        })

        contact = self.env["res.partner"].create({
            "name": "іван петренко",
            "parent_id": company.id,
        })

        self.assertEqual(contact.name, "іван петренко")

    def test_empty_name_has_no_format_error(self):
        self.assertFalse(
            self.env["res.partner"]._get_partner_name_validation_error("")
        )

    def test_name_ending_with_letters_at_is_not_activity_form(self):
        with self.assertRaisesRegex(
            ValidationError,
            "Для назв кирилицею обов’язково вкажіть форму власності",
        ):
            self.env["res.partner"].create({
                "name": "КОМБІНАТ",
                "company_type": "company",
            })

    def test_long_activity_form_has_priority_over_shorter_suffix(self):
        partner = self.env["res.partner"].create({
            "name": "ВАТАГА ПРАТ",
            "company_type": "company",
        })

        self.assertEqual(partner.name, "ВАТАГА ПРАТ")

    def test_onchange_returns_warning_for_invalid_company_name(self):
        partner = self.env["res.partner"].new({
            "name": "ватага",
            "company_type": "company",
        })

        result = partner._onchange_partner_name_format()

        self.assertEqual(partner.name, "ВАТАГА")
        self.assertTrue(result)
        self.assertIn("warning", result)
        self.assertEqual(
            result["warning"]["message"],
            "Назву необхідно вводити ВЕЛИКИМИ ЛІТЕРАМИ (верхній регістр).",
        )

    def test_onchange_returns_warning_for_missing_activity_form(self):
        partner = self.env["res.partner"].new({
            "name": "ВАТАГА",
            "company_type": "company",
        })

        result = partner._onchange_partner_name_format()

        self.assertTrue(result)
        self.assertEqual(
            result["warning"]["message"],
            "Для назв кирилицею обов’язково вкажіть форму власності (наприклад, ТОВ, ФОП).",
        )

    def test_onchange_returns_warning_for_special_characters(self):
        partner = self.env["res.partner"].new({
            "name": "VATAGA/TRADE LLC",
            "company_type": "company",
        })

        result = partner._onchange_partner_name_format()

        self.assertEqual(partner.name, "VATAGA/TRADE LLC")
        self.assertTrue(result)
        self.assertEqual(
            result["warning"]["message"],
            "Дозволені спецсимволи: «+», «-» або дефіс. Будь ласка, видаліть інші символи з назви.",
        )

    def test_onchange_skips_warning_for_child_contact(self):
        company = self.env["res.partner"].create({
            "name": "ВАТАГА ТОВ",
            "company_type": "company",
        })
        partner = self.env["res.partner"].new({
            "name": "іван петренко",
            "parent_id": company.id,
        })

        result = partner._onchange_partner_name_format()

        self.assertEqual(partner.name, "іван петренко")
        self.assertFalse(result)
