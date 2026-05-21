from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSupplierReliabilityRating(TransactionCase):

    def test_supplier_reliability_rating_defaults_to_trial(self):
        partner = self.env["res.partner"].create({"name": "Test Supplier"})

        self.assertEqual(partner.supplier_reliability_rating, "trial")

    def test_supplier_reliability_rating_can_be_changed(self):
        partner = self.env["res.partner"].create({"name": "Test Supplier"})

        partner.supplier_reliability_rating = "approved"

        self.assertEqual(partner.supplier_reliability_rating, "approved")
        self.assertIn("Затверджено", partner.supplier_reliability_badge)

    def test_display_name_has_marker_for_suppliers_only(self):
        customer = self.env["res.partner"].create({"name": "Customer"})
        supplier = self.env["res.partner"].create({
            "name": "Supplier",
            "supplier_rank": 1,
        })

        self.assertFalse(customer.display_name.startswith("🟧 "))
        self.assertTrue(supplier.display_name.startswith("🟧 "))
