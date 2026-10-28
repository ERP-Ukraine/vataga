def migrate(cr, version):
    # Scopes are now append-only per request, rather than one mutable MO-wide
    # freeze. Existing v1 snapshots remain unchanged and retain their policy.
    # A direct upgrade from a version predating the stock kernel has no
    # scope table yet. Never assume another database has staging's schema.
    cr.execute('ALTER TABLE IF EXISTS quality_stock_cost_scope DROP CONSTRAINT IF EXISTS quality_stock_cost_scope_production_unique')
