import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


RELEASE_STATES = [
    ('not_ready', 'Не готовий'), ('blocked', 'Заблоковано'),
    ('ready', 'Готовий до випуску'), ('authorized', 'Дозволено до випуску'),
    ('lapsed', 'Дозвіл втратив чинність'),
    ('produced', 'Випущено на склад'),
]


class ReleaseReadinessError(Exception):
    def __init__(self, code, reason, state='blocked'):
        self.code, self.reason, self.state = code, reason, state


class QualityUnitReleaseReadiness(models.Model):
    _inherit = 'quality.production.unit'

    quality_release_revision = fields.Integer(default=0, readonly=True, copy=False)
    quality_release_line_ids = fields.One2many('quality.unit.release.line', 'unit_id', string='Історія дозволів випуску', readonly=True)
    quality_release_state = fields.Selection(RELEASE_STATES, string='Готовність до випуску', compute='_compute_release_readiness')
    quality_release_reason = fields.Text(string='Причина недоступності', compute='_compute_release_readiness')
    quality_release_last_workorder_id = fields.Many2one('mrp.workorder', string='Остання операція', compute='_compute_release_readiness')
    quality_release_execution_done = fields.Boolean(string='Обробку завершено', compute='_compute_release_readiness')
    quality_release_quality_result = fields.Char(string='Контроль якості', compute='_compute_release_readiness')
    quality_release_admission_id = fields.Many2one('quality.unit.admission', string='Допуск інспектора', compute='_compute_release_readiness')

    @api.model_create_multi
    def create(self, vals_list):
        if any({'quality_release_revision', 'quality_release_line_ids', 'quality_release_state'}.intersection(vals) for vals in vals_list):
            raise UserError(_('Службові поля підготовки випуску встановлює система.'))
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_quality_release_')}
        return super(QualityUnitReleaseReadiness, self.with_context(context)).create(vals_list)

    def _release_records(self, model, where, params):
        """Discover protective IDs, including hidden rows; never grant access.

        All model/table and WHERE expressions are private constants. Every
        subsequent read uses normal ACLs and record rules, without sudo.
        """
        records = self.env[model]
        records.flush_model()
        self.env.cr.execute('SELECT id FROM "%s" WHERE %s ORDER BY id' % (records._table, where), params)
        records = records.browse([row[0] for row in self.env.cr.fetchall()])
        records.check_access_rights('read')
        records.check_access_rule('read')
        return records

    def _release_route(self):
        production = self.production_id
        route = self._release_records('mrp.workorder', 'production_id = %s', [production.id])
        if not route or len(route) > 1 and not production.allow_workorder_dependencies:
            raise ReleaseReadinessError('route_unknown', _('Не визначено однозначний маршрут із залежностями Work Orders.'))
        route.flush_recordset(['blocked_by_workorder_ids'])
        self.env.cr.execute('SELECT workorder_id, blocked_by_id FROM mrp_workorder_dependencies_rel WHERE workorder_id IN %s OR blocked_by_id IN %s', [tuple(route.ids), tuple(route.ids)])
        predecessors = {record.id: set() for record in route}
        successors = {record.id: set() for record in route}
        for destination, source in self.env.cr.fetchall():
            if source not in successors or destination not in predecessors:
                raise ReleaseReadinessError('route_foreign', _('Маршрут містить залежність від іншого MO.'))
            predecessors[destination].add(source)
            successors[source].add(destination)
        roots = [item for item in route.ids if not predecessors[item]]
        if len(roots) != 1 or any(len(items) > 1 for items in list(predecessors.values()) + list(successors.values())):
            raise ReleaseReadinessError('route_ambiguous', _('Розгалужений або неоднозначний маршрут поки не підтримується.'))
        ordered = []
        current = roots[0]
        while current and current not in ordered:
            ordered.append(current)
            current = next(iter(successors[current]), False)
        if current or len(ordered) != len(route):
            raise ReleaseReadinessError('route_cycle', _('Маршрут циклічний або містить непов’язані операції.'))
        route = route.browse(ordered)
        if any(item.production_id != production or item.company_id != self.company_id or item.product_id != self.product_id or item.state == 'cancel' for item in route):
            raise ReleaseReadinessError('route_identity', _('Операції, товар і компанія не відповідають MO.'))
        return route

    def _release_checkpoints(self):
        # Unidentified mandatory checks cannot silently disappear from a
        # per-unit decision. Other stages and explicitly optional checks are
        # not treated as new mandatory in-process evidence.
        workorder = ' OR workorder_id IN (SELECT id FROM mrp_workorder WHERE production_id = %s)' if 'workorder_id' in self.env['quality.check']._fields else ''
        params = [self.production_id.id] * (2 if workorder else 1)
        return self._release_records('quality.check', "(production_id = %s" + workorder + ") AND unit_admission_role = 'required' AND point_id IN (SELECT id FROM quality_point WHERE x_control_stage IN ('in_process','final'))", params)

    def _release_validate_admission(self, admission, workorder, run=None):
        if (not admission or admission.controlled_unit_id != self or admission.production_id != self.production_id
                or admission.product_id != self.product_id or admission.company_id != self.company_id
                or admission.workorder_id != workorder or run and admission.operation_run_id != run
                or admission.sample_id.controlled_unit_id != self or admission.sample_id.quality_check_id != admission.check_id
                or admission.check_id.operation_run_id != admission.operation_run_id):
            raise ReleaseReadinessError('admission_identity', _('Допуск не відповідає виробу, запуску, операції або MO.'))
        error = admission._get_effectiveness_error()
        if error:
            state = 'blocked' if admission.state == 'revoked' or admission.sample_id.sample_result == 'fail' else 'not_ready'
            raise ReleaseReadinessError('admission_invalid', error, state)

    def _release_final_points(self, last_workorder):
        # No frequency/operation API is invented for Enterprise. Select
        # potentially applicable final points conservatively from metadata.
        Point = self.env['quality.point'].with_context(active_test=False)
        clause = "x_control_stage = 'final'"
        params = []
        if 'active' in Point._fields:
            clause += ' AND active'
        if 'company_id' in Point._fields and Point._fields['company_id'].store:
            clause += ' AND (company_id IS NULL OR company_id = %s)'
            params.append(self.company_id.id)
        points = self._release_records('quality.point', clause, params)
        applicable = Point.browse()
        for point in points:
            if point.picking_type_ids and self.production_id.picking_type_id not in point.picking_type_ids:
                continue
            excluded = False
            for name in ('product_ids', 'product_tmpl_ids'):
                field = Point._fields.get(name)
                if field and point[name]:
                    if field.comodel_name == 'product.product' and self.product_id not in point[name]:
                        excluded = True
                    elif field.comodel_name == 'product.template' and self.product_id.product_tmpl_id not in point[name]:
                        excluded = True
            for name in ('operation_id', 'workorder_operation_id'):
                field = Point._fields.get(name)
                if field and getattr(field, 'comodel_name', None) == 'mrp.routing.workcenter' and point[name] and point[name] != last_workorder.operation_id:
                    excluded = True
            if not excluded:
                applicable |= point
        return applicable

    def _release_alert_evidence(self, checks):
        Alert = self.env['quality.alert']
        clauses, params = [], []
        if 'check_id' in Alert._fields:
            query = 'check_id IN (SELECT id FROM quality_check WHERE production_id = %s'
            params.append(self.production_id.id)
            if 'workorder_id' in self.env['quality.check']._fields:
                query += ' OR workorder_id IN (SELECT id FROM mrp_workorder WHERE production_id = %s)'
                params.append(self.production_id.id)
            clauses.append(query + ')')
        for name, model in [('production_id', 'mrp.production'), ('workorder_id', 'mrp.workorder')]:
            field = Alert._fields.get(name)
            if field and field.store and getattr(field, 'comodel_name', None) == model:
                clauses.append(name + (' = %s' if name == 'production_id' else ' IN (SELECT id FROM mrp_workorder WHERE production_id = %s)'))
                params.append(self.production_id.id)
        if 'product_id' in Alert._fields:
            unlinked = ['check_id IS NULL'] if 'check_id' in Alert._fields else []
            for name in ('production_id', 'workorder_id'):
                if name in Alert._fields and Alert._fields[name].store:
                    unlinked.append(name + ' IS NULL')
            clause = 'product_id = %s' + (' AND ' + ' AND '.join(unlinked) if unlinked else '')
            params.append(self.product_id.id)
            if 'company_id' in Alert._fields and Alert._fields['company_id'].store:
                clause += ' AND (company_id IS NULL OR company_id = %s)'
                params.append(self.company_id.id)
            clauses.append('(' + clause + ')')
        alerts = self._release_records('quality.alert', '(' + ' OR '.join(clauses) + ')', params) if clauses else Alert.browse()
        evidence = []
        for alert in alerts:
            check = alert.check_id if 'check_id' in Alert._fields else self.env['quality.check']
            units = check.sample_ids.controlled_unit_id
            # There is no structured failure-unit snapshot in the existing
            # alert model. Never parse editable text or current FAIL rows to
            # guess the historical scope of an alert on a multi-unit check.
            certified = alert.quality_release_scope_unit_id
            if not certified or alert.quality_release_scope_check_id != check or len(check.sample_ids) != 1 or units != certified or certified.production_id != self.production_id or certified.company_id != self.company_id:
                raise ReleaseReadinessError('alert_ambiguous', _('Quality Alert %(alert)s не має однозначної прив’язки до QUNIT. Уточніть сферу невідповідності перед дозволом випуску.', alert=alert.display_name))
            for name, model in [('production_id', 'mrp.production'), ('workorder_id', 'mrp.workorder')]:
                field = Alert._fields.get(name)
                if field and getattr(field, 'comodel_name', None) == model and alert[name]:
                    production = alert[name] if model == 'mrp.production' else alert[name].production_id
                    if production != self.production_id:
                        raise ReleaseReadinessError('alert_identity', _('Посилання Quality Alert суперечать MO цього виробу.'))
            if self not in units:
                continue
            if not (alert.is_approved and alert.approved_by_id and alert.approved_at and alert.decision_type == 'accept_as_is'):
                raise ReleaseReadinessError('alert_blocked', _('Quality Alert %(alert)s не має затвердженого рішення «Прийняти як є» для цього виробу. Сукупні кількості суцільного контролю не є поштучним дозволом.', alert=alert.display_name))
            evidence.append([alert.id, alert.decision_type, alert.approved_by_id.id, str(alert.approved_at), str(alert.write_date)])
        return evidence

    def _release_result_snapshot(self, checks):
        """Preserve this unit's actual facts as well as protected references."""
        results = []
        for check in checks.sorted('id'):
            sample = check.sample_ids.filtered(lambda row: row.controlled_unit_id == self)
            if len(sample) != 1:
                raise ReleaseReadinessError('sample_identity', _('Неоднозначна відповідність зразка під час фіксації доказів.'))
            row = dict(check=check.id, sample=sample.id, result=sample.sample_result)
            if 'visual_result' in sample._fields:
                row['visual'] = sample.visual_result
            values = []
            if 'measurement_value_ids' in sample._fields:
                for cell in sample.measurement_value_ids.sorted('id'):
                    values.append(dict(id=cell.id, column=cell.column_id.id, **{
                        name: cell[name] for name in ('numeric_value', 'numeric_input', 'boolean_value', 'string_value', 'manual_result', 'result') if name in cell._fields
                    }))
            row['values'] = values
            row['equipment'] = [dict(id=selection.id, equipment=sorted(selection.equipment_ids.ids),
                                     legacy=selection.equipment_id.id) for selection in check.equipment_selection_ids.sorted('id')]
            results.append(row)
        return results

    def _get_release_readiness(self, ignore_line=None):
        self.ensure_one()
        data = dict(state='not_ready', code='', reason='', route=self.env['mrp.workorder'],
                    last_workorder=self.env['mrp.workorder'], execution=self.env['quality.unit.execution'],
                    run=self.env['quality.production.run'], check=self.env['quality.check'],
                    admission=self.env['quality.unit.admission'], quality_result='pending', snapshot='')
        try:
            self.check_access_rights('read')
            self.check_access_rule('read')
            production = self.production_id
            production.check_access_rights('read')
            production.check_access_rule('read')
            if not production.quality_transfer_controlled:
                raise ReleaseReadinessError('uncontrolled', _('MO не використовує захищений поштучний маршрут.'), 'not_ready')
            if self in production._check_named_stock_history():
                data.update(state='produced', code='stock_posted', reason=_('Виріб уже випущено на склад.'))
                return data
            if production.state not in ('confirmed', 'progress') or self.company_id not in self.env.companies or production.company_id != self.company_id or production.product_id != self.product_id:
                raise ReleaseReadinessError('identity', _('Потрібен відкритий MO цього товару та дозволеної компанії.'))
            lot = self.lot_id
            if self.product_id.tracking in ('lot', 'serial') and not lot:
                raise ReleaseReadinessError('lot_required', _('Для товару з обліком lot/serial потрібно явно призначити справжню партію або серійний номер. QUNIT не замінює складський номер.'))
            if lot:
                lot.check_access_rights('read')
                lot.check_access_rule('read')
                if lot.product_id != self.product_id or lot.company_id and lot.company_id != self.company_id or self.product_id.tracking == 'serial' and self.serial_lot_id != lot:
                    raise ReleaseReadinessError('lot_identity', _('Партія або серійний номер не відповідає товару, компанії чи QUNIT.'))
            route = self._release_route()
            data.update(route=route, last_workorder=route[-1])
            origin = self.registration_run_id
            if origin.state != 'registered' or origin.workorder_id != route[0] or origin.production_id != production or origin.product_id != self.product_id or origin.company_id != self.company_id or self not in origin.unit_ids:
                raise ReleaseReadinessError('registration', _('Виріб не має сумісної первинної реєстрації на початку цього маршруту.'))
            self.env['quality.production.run'].flush_model(['unit_ids', 'production_id', 'product_id', 'company_id'])
            self.env.cr.execute('SELECT 1 FROM quality_production_run r JOIN quality_production_run_unit_rel rel ON rel.run_id = r.id WHERE rel.unit_id = %s AND (r.production_id != %s OR r.product_id != %s OR r.company_id != %s) LIMIT 1', [self.id, production.id, self.product_id.id, self.company_id.id])
            if self.env.cr.fetchone():
                raise ReleaseReadinessError('foreign_run', _('Історія QUNIT містить несумісний виробничий запуск.'))
            transfers = self._release_records('quality.unit.transfer', "controlled_unit_id = %s AND state != 'returned'", [self.id])
            executions = self._release_records('quality.unit.execution', 'controlled_unit_id = %s', [self.id])
            if executions.filtered(lambda row: row.state == 'progress'):
                raise ReleaseReadinessError('processing', _('Обробку цього виробу ще не завершено.'), 'not_ready')
            checkpoints = self._release_checkpoints()
            # Surface the original FAIL, independently of siblings' results.
            sample = origin.origin_check_id.sample_ids.filtered(lambda row: row.controlled_unit_id == self)
            if len(sample) != 1:
                raise ReleaseReadinessError('source_mapping', _('Початковий контроль не має однозначного зразка цього QUNIT.'))
            if sample.sample_result != 'pass':
                data['quality_result'] = sample.sample_result
                raise ReleaseReadinessError('source_quality', _('Початковий контроль виробу має FAIL або pending.'), 'blocked' if sample.sample_result == 'fail' else 'not_ready')
            used_transfers, used_executions, admissions = self.env['quality.unit.transfer'], self.env['quality.unit.execution'], self.env['quality.unit.admission']
            for index in range(1, len(route)):
                source, destination = route[index - 1], route[index]
                receipts = transfers.filtered(lambda row: row.source_workorder_id == source and row.destination_workorder_id == destination)
                expected = 'received' if index == len(route) - 1 else 'advanced'
                if len(receipts) != 1 or receipts.state != expected:
                    raise ReleaseReadinessError('receipt', _('Не завершено однозначну передачу та приймання між %(source)s і %(destination)s.', source=source.display_name, destination=destination.display_name), 'not_ready')
                if receipts.production_id != production or receipts.company_id != self.company_id or receipts.source_run_id.workorder_id != source or self not in receipts.source_run_id.unit_ids:
                    raise ReleaseReadinessError('receipt_identity', _('Передача не відповідає виробу та виробничому маршруту.'))
                self._release_validate_admission(receipts.admission_id, source, receipts.source_run_id)
                completed = executions.filtered(lambda row: row.workorder_id == destination and row.state == 'processed')
                if len(completed) != 1:
                    raise ReleaseReadinessError('operation_incomplete', _('Операцію %(operation)s для цього QUNIT ще не виконано.', operation=destination.display_name), 'not_ready')
                if completed.transfer_id != receipts or completed.production_id != production or completed.company_id != self.company_id or completed.batch_id.workorder_id != destination or not completed.operator_id or not completed.started_at or not completed.ended_at or not completed.ended_by_id or not completed.result_reference:
                    raise ReleaseReadinessError('execution_identity', _('Підтвердження виконання не відповідає прийманню або не має повного аудиту.'))
                if index > 1 and used_executions[-1] not in receipts.admission_id.check_id.quality_execution_ids:
                    raise ReleaseReadinessError('checkpoint_disconnected', _('Контроль попередньої операції не пов’язаний із її фактичним виконанням.'))
                used_transfers |= receipts
                used_executions |= completed
                admissions |= receipts.admission_id
            if not used_executions or set(transfers.ids) != set(used_transfers.ids):
                raise ReleaseReadinessError('execution_missing', _('Не підтверджено повний поштучний маршрут до останньої операції.'), 'not_ready')
            last = used_executions[-1]
            data['execution'] = last
            final_checks = self._release_records('quality.check', 'id IN (SELECT check_id FROM quality_check_execution_rel WHERE execution_id = %s)', [last.id]).filtered(lambda check: check.x_control_stage == 'in_process')
            last_samples = final_checks.sample_ids.filtered(lambda row: row.controlled_unit_id == self)
            if last_samples:
                data['quality_result'] = 'fail' if any(row.sample_result == 'fail' for row in last_samples) else 'pending' if any(row.sample_result != 'pass' for row in last_samples) else 'pass'
            last_admissions = self._release_records('quality.unit.admission', 'controlled_unit_id = %s AND workorder_id = %s', [self.id, route[-1].id]).filtered(lambda item: item.check_id in final_checks)
            candidates = last_admissions.filtered(lambda item: item.state == 'confirmed')
            if len(candidates) != 1:
                if not candidates and last_admissions:
                    raise ReleaseReadinessError('last_admission_revoked', _('Допуск інспектора після останньої операції скасовано. Потрібне нове явне рішення.'))
                failed = final_checks.sample_ids.filtered(lambda row: row.controlled_unit_id == self and row.sample_result == 'fail')
                raise ReleaseReadinessError('last_control', _('Після останньої операції потрібні поіменний контроль PASS і новий чинний допуск інспектора.'), 'blocked' if failed or len(candidates) > 1 else 'not_ready')
            admission = candidates
            data.update(run=admission.operation_run_id, check=admission.check_id, admission=admission)
            self._release_validate_admission(admission, route[-1])
            admissions |= admission
            known_runs = admissions.operation_run_id
            for check in checkpoints:
                if check.operation_run_id and self not in check.operation_run_id.unit_ids:
                    continue
                if check.x_control_stage == 'final':
                    raise ReleaseReadinessError('final_unmapped', _('Налаштовано фінальний контроль. Потрібне окреме підтверджене поіменне зіставлення QUNIT; загального PASS недостатньо.'))
                if check.operation_run_id not in known_runs:
                    raise ReleaseReadinessError('mandatory_check', _('Обов’язкова перевірка %(check)s ще не має однозначного підтвердження цього QUNIT у маршруті.', check=check.display_name))
            final_points = self._release_final_points(route[-1])
            if final_points:
                raise ReleaseReadinessError('final_required', _('Перед випуском потрібен налаштований фінальний контроль із захищеною прив’язкою до QUNIT. Поточний маршрут не підтверджує його поштучне проходження.'))
            alerts = self._release_alert_evidence(checkpoints)
            if not ignore_line:
                active = self._release_records('quality.unit.release.line', "unit_id = %s AND state = 'confirmed'", [self.id])
                if active:
                    raise ReleaseReadinessError('permission_exists', _('Для виробу вже є підтверджений дозвіл. Перевірте чинність або явно скасуйте попередній дозвіл.'))
            proof_checks = self.env['quality.check']
            for item in admissions:
                proof_checks |= item.check_id._unit_admission_required_checks()
            proof = dict(unit=self.id, production=production.id, product=self.product_id.id, company=self.company_id.id,
                         revision=self.quality_release_revision, route=route.ids,
                         transfers=used_transfers.ids, executions=used_executions.ids,
                         admissions=sorted(admissions.ids), checks=sorted(proof_checks.ids), alerts=alerts,
                         results=self._release_result_snapshot(proof_checks),
                         configuration=[production.picking_type_id.id, production.location_src_id.id, production.location_dest_id.id,
                                        production.bom_id.id, production.product_uom_id.id, self.product_id.tracking,
                                        self.lot_id.id, self.serial_lot_id.id])
            data.update(state='ready', snapshot=json.dumps(proof, sort_keys=True, separators=(',', ':')),
                        transfers=used_transfers, executions=used_executions, admissions=admissions, checks=proof_checks)
        except ReleaseReadinessError as error:
            data.update(state=error.state, code=error.code, reason=error.reason)
        except AccessError:
            data.update(state='blocked', code='access', reason=_('Немає доступу до всіх підстав готовності. Приховані перевірки або блокування не ігноруються.'))
        except (UserError, ValidationError) as error:
            data.update(state='blocked', code='evidence', reason=str(error))
        return data

    @api.depends('production_id.quality_transfer_revision', 'quality_release_revision', 'quality_release_line_ids.state', 'quality_execution_ids.state')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_release_readiness(self):
        for unit in self:
            data = unit._get_release_readiness(ignore_line=True)
            try:
                active = unit._release_records('quality.unit.release.line', "unit_id = %s AND state = 'confirmed'", [unit.id])
            except AccessError:
                active = self.env['quality.unit.release.line']
                data.update(state='blocked', reason=_('Немає доступу до історії дозволів цього QUNIT.'))
            state, reason = data['state'], data['reason']
            if active and state != 'produced':
                valid = state == 'ready' and len(active) == 1 and active.evidence_snapshot == data['snapshot']
                state = 'authorized' if valid else 'lapsed'
                reason = '' if valid else reason or _('Підстави змінилися. Скасуйте старий дозвіл та підтвердіть готовність знову.')
            unit.quality_release_state = state
            unit.quality_release_reason = reason
            unit.quality_release_last_workorder_id = data['last_workorder']
            unit.quality_release_execution_done = bool(data['execution'])
            unit.quality_release_quality_result = {'pass': 'PASS', 'fail': 'FAIL', 'pending': 'pending'}.get(data['quality_result'], 'pending')
            unit.quality_release_admission_id = data['admission']
