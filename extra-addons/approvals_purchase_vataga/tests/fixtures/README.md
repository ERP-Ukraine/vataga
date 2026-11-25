# User-supplied production Studio XML, 2026-11-25

These two complete `<data>` documents are the exact XML supplied by the user,
including the existing `category_id not in [9, 12]` modifier. They are test data,
not manifest data, and are never loaded into shared staging/production views.
The numbers remain only in the historical Studio fixture, never addon logic.

- Form: production view4309, priority160,
  `studio_customization.odoo_studio_approval_b0d5c6ab-c35d-4780-a1fe-7fbf979462c6`,
  parent `approvals.approval_request_view_form`.
- Tree: production view2319, priority99,
  `studio_customization.odoo_studio_approval_35bca016-a3a0-46ef-b7e7-9dc97af8e0ec`,
  parent `approvals.approval_request_view_tree`.

Portable tests always verify the full supplied fixtures and the actual helper
modifiers. With `VATAGA_STAGING_ARCH_DIR` they also use the full real staging
architectures read through native developer UI. The combined form already
contains our fallback employee nodes: the local test copy removes only that
pair before applying the complete production Studio inheritance, then runs the
same helper again. No native/ERP section is reduced to a synthetic skeleton.
The native tree is read directly from its original view, without removing nodes.
Licensed native architecture exports stay outside Git under ignored evidence.

Isolated Enterprise tests use real native inheritance and full supplied specs,
creating temporary test views only inside TransactionCase rollback. They must
never run on shared staging. Neither test route proves first installation or
identity of all other production/staging extensions, data, or permissions.
