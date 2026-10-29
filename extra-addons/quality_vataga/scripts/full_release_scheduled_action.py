# Paste into ONE temporary Scheduled Action / Execute Python Code.
# Set mo_name to the NEW staging test MO. Business data are read-only;
# log() writes only the diagnostic result to the standard technical log.
mo_name = 'REPLACE_WITH_NEW_TEST_MO'
mos = env['mrp.production'].search([('name', '=', mo_name)])
if len(mos) != 1:
    log('FULL-QUNIT: expected one MO, found ' + str(len(mos)), level='warning')
else:
    mo = mos
    moves = mo.move_finished_ids.filtered(lambda m: m.product_id == mo.product_id and not m.scrapped)
    lines = moves.filtered(lambda m: m.state == 'done').move_line_ids
    facts = env['quality.unit.stock.fact'].search([('production_id', '=', mo.id)])
    runs = env['quality.production.run'].search([('production_id', '=', mo.id)])
    admissions = env['quality.unit.admission'].search([('operation_run_id', 'in', runs.ids), ('state', '=', 'confirmed')])
    children = env['mrp.production'].search([('quality_native_parent_id', '=', mo.id)])
    quants = env['stock.quant'].search([('product_id', '=', mo.product_id.id), ('company_id', '=', mo.company_id.id), ('location_id', 'child_of', mo.location_dest_id.id)])
    result = {'mo': mo.name, 'state': mo.state, 'planned': mo.product_qty, 'produced': mo.qty_produced,
        'moves': [(m.id, m.state, m.quantity) for m in moves], 'lines': [(l.id, l.quantity) for l in lines],
        'facts': [(f.unit_id.id, f.move_line_id.id) for f in facts], 'unique_units': len(set(facts.unit_id.ids)),
        'qrun': runs.ids, 'identified_units': runs.unit_ids.ids, 'confirmed_admissions': admissions.ids,
        'effective_admissions': admissions.filtered(lambda a: not a._get_effectiveness_error()).ids,
        'backorders': children.ids, 'destination_quantity': sum(quants.mapped('quantity')),
        'raw_moves': [(m.id, m.state, m.quantity) for m in mo.move_raw_ids],
        'valuation': [(v.id, v.quantity, v.value) for v in (mo.move_raw_ids | moves).stock_valuation_layer_ids]}
    result['full_release_ok'] = (mo.state == 'done' and mo.qty_produced == mo.product_qty
        and sum(lines.mapped('quantity')) == mo.product_qty and len(facts) == mo.product_qty
        and len(set(facts.unit_id.ids)) == mo.product_qty and set(facts.unit_id.ids) == set(runs.unit_ids.ids) and not children
        and all(f.move_line_id in lines and f.move_line_id.move_id.state == 'done' for f in facts))
    log('FULL-QUNIT: ' + str(result), level='info')
