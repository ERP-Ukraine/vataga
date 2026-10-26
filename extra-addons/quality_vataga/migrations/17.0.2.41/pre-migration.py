def migrate(cr, version):
    # Scopes are now append-only per request, rather than one mutable MO-wide
    # freeze. Existing v1 snapshots remain unchanged and retain their policy.
    cr.execute('ALTER TABLE quality_stock_cost_scope DROP CONSTRAINT IF EXISTS quality_stock_cost_scope_production_unique')
