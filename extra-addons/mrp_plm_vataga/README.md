# Manufacturing PLM for Vataga

Version: **17.0.1.0.0**. Requires Odoo 17 `mrp` and Enterprise `mrp_plm`.

Menu: **Виробництво → Звітність → Журнал змін специфікацій**.

## Report contract

The report reads the existing `mrp.eco.bom.change` model directly. Its action
domain is `[('eco_id', '!=', False)]`. A row appears as soon as standard PLM
creates a change attached to an ECO, including ECOs in progress or rebase.
Technical rebase rows without `eco_id` are excluded. There is no `done` filter.
Edits and deletions of standard change records immediately affect the report.
This is a view of PLM's current change records, not an immutable applied-event
audit trail.

The only added field is `vataga_bom_id`, a stored, indexed, readonly Many2one
related to `eco_id.bom_id`. Storage enables standard SQL grouping; it adds a
column to the existing table, not a separate journal table. `compute_sudo=False`
avoids elevating access during related-field computation. The link identifies
the ECO's original BoM, including archived versions. No new/previous BoM field
is needed to calculate the report.

`write_date` means the last modification of the standard PLM change row, **not
the date of applying the ECO**. Product, old/new quantities, and optional hidden
old/new UoMs are standard fields displayed unchanged, with their standard
precision. Add/remove/update semantics and component matching belong to PLM;
this module neither converts UoMs nor calculates a BoM diff. There are no
workflow hooks, synchronization, cron jobs, or copies of change records.

## Access

The report's tree/action disable create, edit and delete. There is no custom
form view. Many2one links use normal access checks on their target records.
No ACLs, record rules, groups, or sudo searches are added: standard PLM access
and company rules remain authoritative. Manufacturing users without standard
read access to `mrp.eco.bom.change` do not gain it by installing this module;
Odoo filters action menus according to model read access.

Read-only applies to this report UI. Users who already have standard PLM write
rights retain them for PLM operations. It would break standard PLM to revoke
those rights globally on the shared model. Any pre-existing gaps in standard
record rules are not repaired by this reporting extension.

## Provenance and validation

PLM field names and state/change-type values follow the production/staging
metadata supplied with the task. The Enterprise implementation was unavailable
locally and has not been independently executed here. The reporting parent
`mrp.menu_mrp_reporting` is already used in this repository by
`mrp_vataga/wizard/component_availability_views.xml` and is part of the standard
[Odoo 17 Manufacturing menus](https://github.com/odoo/odoo/blob/17.0/addons/mrp/views/mrp_views_menus.xml).

The seven Odoo tests cover report inclusion/exclusion, related BoM and grouping,
unchanged standard values for add/remove/update, unfinished ECOs, updates and
deletions without duplication, read-only views, and absence of extra access
grants or a journal table. They create standard change records as report
fixtures; they do not substitute for testing PLM's own diff algorithm.

Run on a disposable Odoo 17 Enterprise test database with these addons on its
addons path (installation is required before the post-install tests):

```sh
odoo -d plm_vataga_test -i mrp_plm_vataga --without-demo=all \
  --test-enable --test-tags /mrp_plm_vataga --stop-after-init --no-http
```

Before staging acceptance, run the suite and verify the menu and linked records
with the intended Manufacturing/PLM roles in each allowed company. No claim of
Enterprise integration or multi-company runtime validation is made until those
checks run successfully.
