# Manufacturing, QC and native actual-batch release — 17.0.2.67

## Cause and implementation

`quality_native_workorder.py` used the current operation's QC/admissions as a
condition for native completion, while initial QUNIT registration depended on
QC initialization. A partial batch was already acknowledged through immutable
executions and a native timer pause, but could not pass the actual-batch adapter:
`quality_actual_batch_release.py` expressly prohibited required unit routes.
Backorders in that adapter also expressly expected the required-route flag off.

The existing boundaries now have distinct responsibilities:

* Physical confirmation locks the MO, prepares the operation's draft matrix and
  reuses the existing identity registrar. Final physical completion does not
  require its own QC decision. Predecessor QC/admission gates remain active.
* A partial WO retains the exact actual quantity and processed identities and
  pauses its native timer. It remains `progress`, preserving its unmanufactured
  remainder; no direct `state='done'` shortcut is introduced.
* A pristine, unidentified draft matrix can follow actual manufacturing quantity.
  Only blank surplus samples can be discarded. Entered measurements, rework
  evidence, registered identities, completed checks and Alert references prohibit
  resizing. Retained sample numbers and all established QUNIT links remain stable.
* When correspondence cannot be established safely, physical quantity is retained
  in `qty_produced` with the protected `quality_native_identity_pending` marker.
  No per-unit execution or quality permission is invented. Existing QC mapping
  and identification confirmation must resolve it before aggregate WO closure
  or warehouse release. The original native time history remains intact.
* Draft snapshot/identity preparation is narrowly elevated only after validating
  operator access to the MO/WO, readable exact checks, product and company. It
  cannot set results, PASS, admissions or stock facts. Public input cannot set
  the pending marker or obtain the transaction-local execution capability.
* Actual-batch release accepts the required unit route without disabling it.
  It requires every identified manufactured unit, exact per-unit processing
  where such history exists, applicable completed QC, effective admissions and
  no open holds. Historical earlier native completions are accepted as aggregate
  evidence without rewriting them or fabricating historical unit executions.
* Linear dependencies are supported; parallel dependency branches remain rejected
  by this adapter. The native copy lineage maps dependencies to the new WOs.
* The existing native completion/backorder wizard, stock-boundary revalidation,
  consumption audit, real stock-line identity facts and surrounding savepoint
  remain the only actual-batch release path. FAIL/PENDING manufactured units
  cannot be silently treated as an unmanufactured remainder.
* A backorder inherits the required-route policy and remapped sticky QCP
  requirements, but no manufactured QUNIT, executions, completed QC or admissions.

The incremental release kernel and legacy transfer workflow were not replaced.
Obsolete required-route and earlier-WO-state bans were removed from the existing
adapter. Acceptance probe codes N5 (physically confirmed partial earlier WO) and
N12 (required unit route) now run positive stock audits instead of asserting
those obsolete bans. Its remaining negative scenarios retain rejection checks.

## Local verification

Python 3.12, with the already available `test-deps312` directory on `PYTHONPATH`:

```text
python extra-addons/quality_vataga/tests/run_portable_tests.py
322 tests — OK
Python AST parsing — 155 module Python files OK
lxml XML parsing — 40 module XML files OK
git diff --check — changed implementation/test/documentation files only
```

The user's pre-existing modification to `docs/actual_batch_release_P0.md` is
excluded from this change, including its existing whitespace issues.

No Docker, SSH, server shell, database connection or Odoo instance was used.
Enterprise ORM tests were added/updated but **not executed**. Portable tests run
shipped Python methods against explicit fixtures. They do not exercise stock
valuation, accounting, Enterprise inheritance, record rules or PostgreSQL locking.
Concurrent-request tests model transaction serialization with a Python lock;
real concurrent PostgreSQL transactions remain a deployment acceptance check.

## Scenario coverage and remaining acceptance

| Scenarios | Tests / verification |
| --- | --- |
| T1 | New ORM full 3/3 after physical confirmation before QC; portable final boundary; existing full-release suite |
| T2, T3, T6 | New ORM required-route 2/3 without opening QC; portable physical quantity and sample preparation |
| T4, T5, T15 | Required-route ORM pending/FAIL/Alert; portable actual-batch and shared QC/admission gates |
| T7, T8 | Repeated QC initialization and physical confirmation preserve identity/sample/execution counts |
| T9 | Existing ORM repeated warehouse release; portable repeated/serialized public release |
| T10 | Existing ORM foreign-MO unit rejection; portable missing/duplicate/foreign physical sets |
| T11, T12 | Existing final-only and intermediate-QC route suites; portable predecessor gates; new ORM linear-dependency backorder |
| T13 | Existing ordinary native MO/WO regressions and portable native-flow tests |
| T14 | New ORM 2/3 remainder 1, required route retained and no original QUNIT/execution/completed QC |
| T16 | Portable serialized-request simulations only; real independent PostgreSQL requests NOT RUN |

Actual-batch release still rejects finished-product lot and serial tracking,
manual component consumption, byproducts and unsupported native backorder
policies. This preserves existing safe restrictions. Existing full lot-release
and single-serial full-release validation remain applicable; multi-serial batch
completion and partial tracked release are not enabled by this change.

## Staging acceptance

Update `quality_vataga` to 17.0.2.67 before testing the added field.

1. Open CAB/MO/05150. Verify plan 3, actual 2, the original two QUNIT, QC00379
   and their effective admissions. Leave required unit control enabled.
2. On the last WO use **Підтвердити обробку** if needed, not **Почати** again.
   Confirm quantity stays 2 and no duplicate execution or timer appears. A
   partial WO remaining `progress` until MO release is intentional.
3. Verify all mandatory QC/admissions are effective, no Alert remains open and
   all working intervals are closed. The physical facts alone must not grant
   warehouse permission.
4. Click **Випустити фактичну партію**. Verify original MO closes at 2, finished
   stock increases by 2, component consumption/valuation matches 2 products,
   and one native backorder is created for 1.
5. Verify the backorder retains required unit control and its own mapped QC
   requirements, with no original QUNIT, completed QC, admissions or execution
   facts. Verify both original QUNIT each have one stock fact and a second
   release cannot post anything.
6. On a separate disposable 3-unit MO set actual 2 before confirming work. Do
   not open QC first. Confirm manufacturing creates two pending samples and
   two QUNIT automatically; pending QC prevents release. Complete QC and verify
   the same 2/3 release. Repeat with one FAIL and an open Alert: release must fail.
7. Verify 3/3 PASS uses **Виготовити все** without a backorder, and intermediate
   mandatory QC still prevents starting a dependent operation until admission.
8. Test two sessions confirming/releasing the same disposable MO concurrently;
   require one set of physical facts, one warehouse posting and one backorder.
   A serialization/retry error is acceptable; duplicate stock or consumption is not.
