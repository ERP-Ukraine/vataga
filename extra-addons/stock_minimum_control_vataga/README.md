# Контроль мінімальних залишків — Odoo 17

Version: `17.0.1.0.7`. Dependency: `stock` (which already depends on `web` and `product`).
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

The table area follows `web.PivotRenderer` / `web.PivotHeader`: standard
`table-hover table-sm table-bordered table-borderless`, `bg-view` / `bg-100`,
measure/header hover classes, `o_value` numeric cells and 5 + 30px-per-level row
indentation. Compact native buttons retain keyboard access while inheriting
pivot typography/padding.

Body row labels, minimum cells and measure cells explicitly use Bootstrap
`align-top`. Numeric cells retain `text-end`, and the standard `.o_value` wrapper
is unchanged. Header alignment is unchanged. Previously alignment depended on
inherited table/theme styles; clean upstream Odoo already inherited `top`, so the
reported production mismatch was not reproduced there. The regression test also
sets parent table alignment to `baseline` and verifies explicit top alignment.
Version `17.0.1.0.6`: 19 backend tests and 8 QUnit tests / 109 assertions passed.
Browser checks confirmed identical numeric text tops (0px difference) on total,
category and product rows, including a name wrapping from five to eight lines
after product/minimum/measure resizing. Colors and right alignment are preserved.

Version `17.0.1.0.7` identifies and overrides a cross-addon style conflict:
`product_alternatives_vataga/static/src/scss/analog_marker.scss` globally targets
`.o_pivot table tbody tr > td:nth-child(5n + 6)`, forcing centered/middle cells and
centered flex `.o_value` containers. Scoped numeric-cell rules in this report
have higher specificity; `.o_value` is explicitly block/full-width/right-aligned
with zero margin/padding. The standard Pivot cell padding and existing `align-top`
remain. No other addon's styles are changed and no dependency is added.

Validation: 19 backend tests and 9 QUnit tests / 128 assertions passed. QUnit
injects the conflicting marker CSS after report assets and checks three locations,
all measures, numeric content widths and equal right gaps before/after resize.
Browser acceptance used the original compiled marker CSS, 15 products, 12 added
internal locations, Ukrainian number formatting with four decimal places, zeros
and quantities 100 / 6,950 / 18,350. Removing the new scoped rules reproduced 204
centered containers. With the fix all 1,020 numeric measure cells had block,
full-content-width, right-aligned values; right gaps were consistently 5.297px
(standard padding plus collapsed border) before and after resizing. Minimum
padding matched the measures, and all three conditional colors remained correct.

### Standard search and report actions

The existing `ir.actions.client` is retained. Its wrapper uses Odoo 17
`WithSearch` / `SearchModel`, a server search view on `product.product`, `Layout`,
`SearchBar`, the responsive search toggler and `CogMenu`. No search controls or
Favorites storage are reimplemented. Search fields are product name/code (OR)
and category; standard custom Filters and Favorites are available. Group By is
hidden because category → variant is a fixed hierarchy. Favorites are scoped to
this action; default Favorites are loaded before the first report RPC.

`get_report(domain=...)` ANDs the SearchModel domain with mandatory storable
product, positive minimum and active-company restrictions. Record rules still
apply. The old optional `search` argument remains compatible and is also ANDed.
Domain changes reset pagination and recalculate totals; stale overlapping RPC
responses cannot replace newer results. Quantity computations are unchanged.

`web.ReportViewMeasures` supplies the actual standard Dropdown/DropdownItem UI.
Only on-hand, free and forecast measures are selectable; all start enabled.
Disabling a measure removes its leaf columns and updates colgroup, group colspans
and total width. Stable measure keys retain widths when re-enabled. Product and
minimum remain visible even when every measure is disabled; color stays attached
only to on-hand, never to a measure's current visible index.

Expand all updates total/category/warehouse expansion state together and makes
one report RPC. It retains category pagination (80 products per category).
Refresh is a secondary icon. XLSX uses standard `download`, an authenticated
CSRF-protected controller and Odoo's `xlsxwriter`. The server recomputes the same
domain/scopes with the user's rights and selected companies. Export matches the
current expanded hierarchy and current page of each category, not every hidden
product; category/overall totals still include every matching product. Minimum
aggregate cells remain blank, numeric cells are numeric, and product names are
written as literal text. Select the wanted measures/pages before downloading.

Flip Axis is intentionally absent: transposition would require moving the
product-specific fixed minimum into a different axis and redesigning the report.
There is no decorative spreadsheet insertion button. The repository Dockerfile
references `erpukraine/odoo-ee-erpu:17.0-latest`; that image's Enterprise source is
not checked in, and the local Docker engine is unavailable. The exact production
insertion provider/API therefore cannot be verified. Available Community source
`spreadsheet/static/src/pivot/pivot_data_source.js` instantiates
`SpreadsheetPivotModel`, which extends standard `PivotModel` and its read_group
contract. This RPC report does not implement that contract: context-dependent
warehouse quantities and exact locations replace one another, and minimum is
outside the measure axis. A correct live spreadsheet integration needs a dedicated
data-source/model adapter plus the actual Enterprise insertion flow. Guessing an
addon dependency or passing this RPC to the normal pivot source would be incorrect.

Column resizing follows Demand's `.o_resize` pointer-drag affordance, with local
`o_resizing` / `o_column_resizing` feedback. Only product, minimum and leaf measures
have handles; colspan warehouse/location headers do not. A fixed-layout table
and one `<col>` per leaf avoid resizing unrelated columns. Pointer move updates
the selected width and total table width; pointer up/cancel, window blur, keydown
and component unmount remove drag listeners. Handles also support arrow keys.

Component-local reactive widths are keyed by `product`, `minimum`,
`w<ID>:qty_available`, `l<ID>:free_qty`, etc. They survive category/warehouse
expansion, collapse and data refresh, but intentionally reset on full page reload.
Defaults are 280 / 160 / 120px for product / minimum / measures; lower bounds are
140 / 100 / 72px. New locations receive default widths. Product and group labels
can wrap. The minimum sticky column uses `left: var(--smc-product-width)`, updated
from the same state that sizes the product column; no fixed sticky offset remains.

Odoo 17 source checked against upstream commit
`7f8eadd121af45e243fb0e943815f29f7e3ab94a` (the repository's configured enterprise
image is not locally running):

- `addons/stock/views/product_views.xml`: `stock.action_product_stock_view`,
  `stock.product_product_stock_tree` show `qty_available`, `free_qty`,
  `virtual_available`. The standard action selects storable product variants;
  this report uses the same product type and additionally requires
  `minimum_stock_qty > 0`. Zero stock remains eligible for configured products.
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

Only products with a positive template minimum participate. The delegated-field
ORM domain `('minimum_stock_qty', '>', 0)` is applied in `get_report()` before
grouping, counts, pagination and quantity computation. Zero means control is not
configured: those products and categories containing only those products are
absent, including from name/code search and all totals. All storable variants of
a configured template participate; setting its minimum to zero excludes them all.

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
   green. Set minimum to `0`: the product disappears from rows, search and totals.
   Color classification itself is unchanged.
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
location usages, categories, exclusion of zero minima before stock computation,
filtered search/counts/pagination, variants, company isolation including
forged company context, report ACLs, pagination, batching and inherited views.

Frontend: `static/tests/stock_minimum_report_tests.js` covers inclusive color
thresholds, fractions/zero, measure-specific coloring and mounted OWL expansion /
collapse with replacement of warehouse columns by locations, leaf resize handles,
drag and lower bounds, sticky adjacency, retained widths after rerender/refresh,
new location defaults, pointer cancellation and keyboard resizing.

Run on a disposable Odoo 17 database:

```sh
odoo -d minimum_control_test -i stock_minimum_control_vataga \
  --test-enable --test-tags=/stock_minimum_control_vataga \
  --without-demo=all --stop-after-init
```

QUnit: `/web/tests?mod=stock_minimum_control_vataga&filter=stock_minimum_control_vataga`.

Validated on 2026-09-28 in an isolated upstream Odoo 17 / Python 3.12 /
PostgreSQL 18 database:

- Fresh installation and upgrade succeeded; 19 backend test methods passed
  with `17.0.1.0.5`, including domain filtering/totals, XLSX/access, compiled
  section order in both forms and delegated variant edits.
- Browser verification of the view fix: Inventory → Products template form and
  the stock Product Variants action both show the full-width block after
  Traceability and before Packaging. Values `100` and `10.5` survive save/reopen;
  `-0.5` raises the Ukrainian
  validation error. Backend form tests also verify shared sibling-variant minima
  and their use in the existing report.
- Headless Edge / Odoo QUnit: all 7 tests / 81 assertions passed against the real
  Odoo asset bundles, including measures, domain forwarding, batched expand all,
  resize and existing color/hierarchy coverage.
- Version `17.0.1.0.5` browser acceptance: category autocomplete/facet, name/code
  search, filtered totals, saved default Favorite after reopening, measure toggle
  and colors, real downloaded XLSX contents, facet removal, resize/sticky/scroll.
  Side-by-side standard PivotController with the original Demand renderer on a
  test product model and the real report: both SearchBars measured 422.109px ×
  35px at the same vertical offset in equal-width panes. This validates frontend
  components, not the unavailable full production Demand/Enterprise application.
- Version `17.0.1.0.4` browser acceptance: three collapsed warehouses, expanded
  warehouse with several locations, long product/location names, actual pointer
  drag, horizontal scrolling, dynamic sticky adjacency and width persistence on
  refresh/collapse. Side-by-side comparison uses the unmodified Demand renderer
  JS and inherited XML, with its compiled SCSS and deterministic fixture data:
  full Demand backend dependencies are unavailable locally. Both table areas
  measured 14px font, 8px/4.8px padding and 38px ordinary rows in this environment.
- Actual RPC/browser acceptance: 120 green, 6 red, 10.5 yellow; exact CSS colors;
  90 + 30 location split without warehouse duplication; horizontal scrolling
  keeps the minimum column fixed.
- Python compilation, XML parsing, manifest/dependency/asset-path validation,
  Node JS syntax checks, libsass compilation and Odoo asset bundles passed.
- Initial-version synthetic benchmark (2,002 products before minimum filtering,
  12 warehouses, 80 visible product rows):
  collapsed, 139 SQL queries / 0.374 s; all 61 internal locations expanded,
  552 SQL queries / 1.050 s. Local timings are illustrative, not a production
  SLA; benchmark data was rolled back. Query growth follows batches/scopes,
  not individual product cells.

The full ERP-Ukraine enterprise deployment image was not available for testing;
deployment should include the manual scenario above in staging.
