# Transfer/package labels are disabled. The product label override remains active
# through views/report_product_label_dymo.xml.
#
# from odoo import _, api, fields, models
# from odoo.exceptions import UserError, ValidationError
#
#
# class StockPicking(models.Model):
#     _inherit = 'stock.picking'
#
#     box_count = fields.Integer(
#         string='Кількість коробок',
#         default=1,
#         required=True,
#         copy=False,
#     )
#
#     @api.constrains('box_count')
#     def _check_box_count(self):
#         for picking in self:
#             if picking.box_count < 1:
#                 raise ValidationError(_('The number of boxes must be at least 1.'))
#
#     def _use_vataga_transfer_labels(self):
#         self.ensure_one()
#         return self.picking_type_code in ('incoming', 'outgoing', 'internal')
#
#     def _get_vataga_transfer_label_locations(self):
#         self.ensure_one()
#
#         source_name = self.location_id.complete_name or ''
#         destination_name = self.location_dest_id.complete_name or ''
#         partner_name = self.partner_id.commercial_partner_id.display_name or ''
#
#         if self.picking_type_code == 'incoming':
#             return partner_name or source_name, destination_name
#         if self.picking_type_code == 'outgoing':
#             return source_name, partner_name or destination_name
#         if self.picking_type_code in ('internal', 'mrp_operation'):
#             return source_name, destination_name
#         return source_name, destination_name
#
#     def _get_vataga_transfer_label_title(self):
#         self.ensure_one()
#         return self.name or ''
#
#     def _get_vataga_transfer_label_lines(self):
#         self.ensure_one()
#         source_name, destination_name = self._get_vataga_transfer_label_locations()
#         partner_name = self.partner_id.commercial_partner_id.display_name or ''
#
#         if self.picking_type_code == 'incoming':
#             return (
#                 f'Постачальник: {partner_name or source_name}',
#                 f'Призначення: {destination_name}',
#             )
#         if self.picking_type_code == 'outgoing':
#             return (
#                 f'Джерело: {source_name}',
#                 f'Клієнт: {partner_name or destination_name}',
#             )
#         return (
#             f'Джерело: {source_name}',
#             f'Призначення: {destination_name}',
#         )
#
#     def action_print_vataga_transfer_labels(self):
#         printable_pickings = self.filtered(lambda picking: picking._use_vataga_transfer_labels())
#         if not printable_pickings:
#             raise UserError(_('Transfer labels are available only for receipts, deliveries and internal transfers.'))
#
#         return self.env.ref(
#             'product_vataga.action_report_stock_picking_transfer_labels_v2'
#         ).report_action(printable_pickings, config=False)
#
#     def action_open_label_layout(self):
#         if not all(picking._use_vataga_transfer_labels() for picking in self):
#             return super().action_open_label_layout()
#         return self.action_print_vataga_transfer_labels()
#
#     def action_open_label_type(self):
#         if not all(picking._use_vataga_transfer_labels() for picking in self):
#             return super().action_open_label_type()
#         return self.action_print_vataga_transfer_labels()
