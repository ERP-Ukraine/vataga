from odoo import api, fields, models


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    def action_confirm(self):
        super().action_confirm()
        for production in self:
            if production.user_id:
                all_child_productions = production._get_all_children()
                if all_child_productions:
                    all_child_productions.write({'user_id': self.env.user.id})

    def _get_all_children(self):
        self.ensure_one()
        all_children = self.env['mrp.production']

        def _find_all_children_recurse(prods):
            nonlocal all_children
            for prod in prods:
                children = prod._get_children()
                if children:
                    new_children = children - all_children
                    all_children |= new_children
                    _find_all_children_recurse(new_children)

        _find_all_children_recurse(self)
        return all_children.filtered(lambda child: not child.user_id and child.state not in ['cancel', 'done'])
