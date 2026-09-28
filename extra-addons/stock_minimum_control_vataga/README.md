# Контроль мінімальних залишків — Odoo 17

Version: `17.0.1.0.2`. Dependency: `stock` (which already depends on `web` and `product`).
Install the addon and open **Склад → Звітність → Контроль залишків**.

The minimum settings block is a full-width section after Traceability and
immediately before Packaging in the Inventory tab in both
`product.product_template_only_form_view` and `product.product_normal_form_view`.
The variant form uses Odoo's delegated template field (`_inherits`); editing any
variant updates the same minimum on its template and all sibling variants.
Update the addon to apply the views. Both forms target the direct child
`//page[@name='inventory']/group[@name='packaging']` with `position="before"`;
the minimum section is not nested inside Traceability or the two-column group.

## Architecture and source investigation

The existing purchase **Попит** entry is
`sale_demand_vataga.menu_product_analytic`, linked to
`sale_demand_vataga.action_product_analytic_report` / `product.analytic`.
`purchase_demand_vataga/views/product_views.xml` adds a measure to that view.
The implementation uses the standard pivot with custom subclasses:

- `sale_demand_vataga/static/src/views/pivot/pivot_view.js`: `PivotModelDemand`,
  `PivotSearchModelDemand` and standard `PivotController` / `PivotArchParser`.
- `pivot_renderer.js` / `pivot_renderer.xml`: `PivotRendererDemand` inherits
  `web.PivotRenderer`; `getPivotCellClasses` colors the `closed` measure.
- `pivot_renderer.scss`: scoped conditional colors.
- Row/column hierarchies come from standard pivot group subdivisions and
  `read_group`, rather than a standalone client action. Category grouping is
  available in the search view; the base pivot itself starts with product rows.

This addon uses a small **OWL client action**, with standard pivot/table classes,
ORM service, float formatting and expand/collapse controls. Reusing only the
existing renderer would not solve the data-model differences: a fixed minimum
column outside the measure axis, empty aggregate minima, and warehouse values
replaced by exact-location values. A normal additive pivot containing both
warehouse facts and location facts would double-count. No dependency on the
purchase/demand addons and no global renderer patch are required.

Odoo 17 source checked against upstream commit
`7f8eadd121af45e243fb0e943815f29f7e3ab94a` (the repository's configured enterprise
image is not locally running):

- `addons/stock/views/product_views.xml`: `stock.action_product_stock_view`,
  `stock.product_product_stock_tree` show `qty_available`, `free_qty`,
  `virtual_available`. The standard action selects storable product variants;
  this report uses the same product type, including zero minima and zero stock.
- `addons/stock/models/product.py`: `_compute_quantities` delegates to
  `_compute_quantities_dict`, which groups quants, reservations and pending moves
  and applies each product's UoM rounding. This addon calls that batch method;
  it does not implement alternative inventory/forecast formulas.
- `_get_domain_locations` uses the warehouse's `view_location_id` hierarchy;
  `_get_domain_locations_new` supports `strict=True` for exact locations.
- Verified XML IDs: `stock.menu_warehouse_report`, `stock.view_warehouse`,
  `product.product_template_only_form_view` (inherits
  `product.product_template_form_view`, containing page `inventory`).

## Quantities, scope and performance

Collapsed warehouse: standard `warehouse=<id>` quantity context. Expanded
warehouse: only internal locations in its view-location hierarchy, each with
`warehouse=<id>, location=<id>, strict=True`. A parent internal location shows
only stock directly in it, and children show their own stock. Warehouse totals
are **not** included alongside their location columns. Transfers between two
displayed locations affect their forecasts in opposite directions; the
collapsed warehouse forecast comes directly from Odoo.

Inactive internal locations are included as well: archiving a location must
not silently drop historical stock still present there. View/customer/supplier/
inventory/transit locations are not offered as quantity columns. The collapsed
warehouse always retains standard Odoo semantics, even for unusually configured
location trees.

Quantities are computed in batches of 1,000 products **per visible scope**, not
per cell. Location quantities are requested only for expanded warehouses.
Category and total values are summed from those product batches, never computed
in separate stock queries. Only 80 product rows per expanded category are sent
to the browser; pagination does not change category/total sums. Search matches
product name or internal reference and limits the whole report, including totals.
Use search and collapse unused scopes for very wide reports; current quantities
are recomputed on refresh/expansion (there is no stale persisted fact table).

Each row is a `product.product` variant, using its standard `display_name` and
its template's minimum. Minima are never summed for categories or totals. Stock
aggregates across different products/UoMs are simple numeric sums, as requested;
the product row displays its UoM.

`allowed_company_ids` are validated by the normal environment. Warehouse searches
and product searches obey record rules, and each quantity batch is restricted to
its warehouse company, with no `sudo`. Shared products participate in each
selected company; company-owned products only participate in their own company.
The report service grants stock users read ACL only and checks stock membership.
The reporting parent menu retains standard Odoo permissions (stock manager in
upstream 17); no standard menu permissions are widened. Warehouse/product edits
retain their existing model permissions and are absent from the report.

All new UI source strings are Ukrainian; no existing translations are modified.

## Manual acceptance scenario

1. Install the addon. Confirm a new warehouse has the checkbox disabled and a
   new product has minimum `0`. Enable **Виконувати контроль мінімальних залишків**
   on MH and GL2; leave another warehouse disabled.
2. In product A's **Склад → Контроль залишків**, set minimum `100`. Put `120` in
   MH and `6` in GL2. Product B: minimum `10`, MH stock `10.5` (use an appropriate
   fractional UoM). Negative minima must raise the Ukrainian validation error.
3. Open the report, expand **Разом → category → products**. A: minimum `100`,
   MH on-hand `120` green, GL2 `6` red. B: `10.5` yellow. Only on-hand cells are
   colored; minimum/free/forecast and aggregate cells are not.
4. Check A with on-hand `99.9 / 100 / 105 / 110 / 110.1`: red/yellow/yellow/yellow/
   green. With minimum `0`, negative/zero/positive stock is red/yellow/green.
5. Put `90` directly in MH/Stock and `30` in MH/Stock/Components. Expand MH:
   exact columns show `90` and `30`, not `120` and `30`; collapsing shows `120`.
   Verify pending receipts/deliveries and reservations against **Запаси** with
   the same warehouse selected. A planned internal move must not change the
   collapsed warehouse forecast.
6. Verify both variants of a template keep their own quantities and share its
   minimum; switch active companies; check a stock user and an unauthorized user.
   Confirm disabled/inaccessible warehouses and non-internal locations are absent.
7. Search by name/code, page through a category with over 80 variants, and scroll
   horizontally with multiple expanded warehouses. Totals stay complete and the
   product/minimum columns remain visible.

## Automated validation

Backend: `tests/test_stock_minimum_report.py` covers defaults, non-negative
fractional minima on create/write, enabled warehouses, field-equivalent warehouse
and strict-location quantities, reservations/forecast/internal transfers,
location usages, categories, zero minima, variants, company isolation including
forged company context, report ACLs, pagination, batching and inherited views.

Frontend: `static/tests/stock_minimum_report_tests.js` covers inclusive color
thresholds, fractions/zero, measure-specific coloring and mounted OWL expansion /
collapse with replacement of warehouse columns by locations.

Run on a disposable Odoo 17 database:

```sh
odoo -d minimum_control_test -i stock_minimum_control_vataga \
  --test-enable --test-tags=/stock_minimum_control_vataga \
  --without-demo=all --stop-after-init
```

QUnit: `/web/tests?mod=stock_minimum_control_vataga&filter=stock_minimum_control_vataga`.

Validated on 2026-09-28 in an isolated upstream Odoo 17 / Python 3.12 /
PostgreSQL 18 database:

- Fresh installation and upgrade succeeded; 15 backend test methods passed
  after the `17.0.1.0.2` layout fix, including compiled section order in both forms
  and delegated variant edits.
- Browser verification of the view fix: Inventory → Products template form and
  the stock Product Variants action both show the full-width block after
  Traceability and before Packaging. Values `100` and `10.5` survive save/reopen;
  `-0.5` raises the Ukrainian
  validation error. Backend form tests also verify shared sibling-variant minima
  and their use in the existing report.
- Headless Edge / Odoo QUnit: 3 tests, 29 assertions passed, no browser errors.
- Actual RPC/browser acceptance: 120 green, 6 red, 10.5 yellow; exact CSS colors;
  90 + 30 location split without warehouse duplication; horizontal scrolling
  keeps the minimum column fixed.
- Python compilation, XML parsing, manifest/dependency/asset-path validation,
  Node JS syntax checks, libsass compilation and Odoo asset bundles passed.
- Synthetic benchmark (2,002 products, 12 warehouses, 80 visible product rows):
  collapsed, 139 SQL queries / 0.374 s; all 61 internal locations expanded,
  552 SQL queries / 1.050 s. Local timings are illustrative, not a production
  SLA; benchmark data was rolled back. Query growth follows batches/scopes,
  not individual product cells.

The full ERP-Ukraine enterprise deployment image was not available for testing;
deployment should include the manual scenario above in staging.
