-- Run with a read-only staging account, before individually opting in QCP/MO.
-- Works before and after 17.0.2.53: absent policy columns are read as false.
-- No recommendation to enable points is inferred from the required QC role.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

SELECT current_database() AS database, current_user AS reader;

SELECT p.id AS qcp_id, p.title, p.x_control_stage,
       COALESCE((to_jsonb(p)->>'quality_unit_route_required')::boolean, false) AS explicit_unit_policy,
       count(DISTINCT c.id) AS existing_checks,
       count(DISTINCT m.id) FILTER (WHERE m.state NOT IN ('done', 'cancel')) AS open_mos_with_existing_checks,
       count(DISTINCT c.id) FILTER (WHERE m.state NOT IN ('done', 'cancel')
           AND c.operation_run_id IS NULL) AS open_checks_without_qrun
FROM quality_point p
LEFT JOIN quality_check c ON c.point_id=p.id
LEFT JOIN mrp_workorder w ON w.id=c.workorder_id
LEFT JOIN mrp_production m ON m.id=COALESCE(c.production_id, w.production_id)
WHERE p.x_control_stage='in_process'
GROUP BY p.id ORDER BY p.id;

SELECT m.id AS mo_id, m.name, m.state, m.product_qty, m.qty_producing,
       m.quality_transfer_controlled,
       COALESCE((to_jsonb(m)->>'quality_unit_route_required')::boolean, false) AS explicit_unit_policy,
       c.id AS qc_id, c.name AS qc_name, c.point_id AS qcp_id,
       c.quality_state, c.unit_admission_role, c.operation_run_id,
       (SELECT count(*) FROM quality_check_sample s WHERE s.quality_check_id=c.id) AS matrix_samples,
       (SELECT count(DISTINCT s.controlled_unit_id) FROM quality_check_sample s
           WHERE s.quality_check_id=c.id) AS identified_units,
       (SELECT count(*) FROM quality_unit_admission a WHERE a.operation_run_id=c.operation_run_id
           AND a.state='confirmed') AS confirmed_run_admissions
FROM mrp_production m
JOIN quality_check c ON c.production_id=m.id OR c.workorder_id IN
    (SELECT id FROM mrp_workorder WHERE production_id=m.id)
JOIN quality_point p ON p.id=c.point_id AND p.x_control_stage='in_process'
WHERE m.state NOT IN ('done', 'cancel')
   OR m.name IN ('CAB/MO/05115', 'CAB/MO/05114', 'CAB/MO/05113', 'CAB/MO/05110')
ORDER BY m.id, c.id;

ROLLBACK;
