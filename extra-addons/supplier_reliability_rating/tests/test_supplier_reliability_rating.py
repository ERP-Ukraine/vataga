from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSupplierReliabilityRating(TransactionCase):

    def test_supplier_reliability_rating_defaults_to_trial(self):
        partner = self.env["res.partner"].create({"name": "Test Supplier"})

        self.assertEqual(partner.supplier_reliability_rating_id.code, "trial")

    def test_supplier_reliability_rating_can_be_changed(self):
        partner = self.env["res.partner"].create({"name": "Test Supplier"})
        approved_rating = self.env.ref(
            "supplier_reliability_rating.supplier_reliability_rating_approved"
        )

        partner.supplier_reliability_rating_id = approved_rating

        self.assertEqual(partner.supplier_reliability_rating_id.code, "approved")
        self.assertIn("Затверджено", partner.supplier_reliability_badge)

    def test_display_name_has_marker_for_suppliers_only(self):
        customer = self.env["res.partner"].create({"name": "Customer"})
        supplier_values = {"name": "Supplier"}
        if "supplier_rank" in self.env["res.partner"]._fields:
            supplier_values["supplier_rank"] = 1
        supplier = self.env["res.partner"].create(supplier_values)

        if "supplier_rank" in self.env["res.partner"]._fields:
            self.assertFalse(customer.display_name.startswith("🟠 "))
        self.assertTrue(supplier.display_name.startswith("🟠 "))
