# Explicit mandatory unit route — 17.0.2.53

## Impact assessment before implementation

The pre-handoff matrix guard accepts complete PASS matrices. It does not require
QRUN/QUNIT or inspector admission. The transfer barrier starts only at the first
handoff. `unit_admission_role=required` is a default role within a QRUN and cannot
identify an opt-in production policy. No live staging database was available;
the four reported completed MOs are diagnostic examples, not migration targets.

All existing QCP and MO records retain standard policy on upgrade. No migration
updates QC, MO, identity, admission or stock business records. Existing guards
for matrices, Alert holds and transferred units remain authoritative.

## Activation and existing open orders

A quality manager explicitly enables `quality_unit_route_required` on a QCP
whose stage is `in_process`. It applies only when a NEW QC is created, including
new checks generated for an existing open MO. Editing a QCP does not rewrite
existing checks or activate their MOs. Enabling the QCP is therefore a prospective
operational decision: select points individually after reviewing their scope.
No QCP is enabled by installation or upgrade.

`scripts/quality_unit_route_impact.sql` provides the actual QCP/open-MO inventory
and the four reported historical MOs in an explicitly read-only transaction.
It works on 17.0.2.52 too; absent policy columns are displayed as false. Its
admission count means confirmed records, not certification of effectiveness.
Run with `psql --set ON_ERROR_STOP=on --file quality_unit_route_impact.sql` using
a staging read-only account. This report has not been run against staging here.

An existing open MO can instead be opted in individually through
`action_require_quality_unit_route` (quality manager, ordinary write access,
confirmation in the form). Closed/cancelled or already produced MOs are rejected.
Automatic activation from a new configured QC uses the same private server
method and the creating user's ordinary MO write access. It creates no identities
or approvals. The MO policy is sticky; field writes, context defaults, deleting
or reassigning QC and later QCP changes cannot disable it. Authorized native
Backorder children inherit it. The first-transfer flag retains its old meaning.

## Completion and stock

Before first handoff, individual WO execution remains subject to the existing
operation-scoped matrix guard: future QC does not block earlier WO completion.
At MO completion, Backorder splitting and stock posting, an opted-in MO is
rejected even if its matrix is PASS or identities/admissions already exist.
Direct state/produced-quantity writes, stock move completion/detachment and
stock line detachment and changes of already posted stock lines cannot bypass
this barrier. Pending reservation lines can still be prepared before the first
handoff: they do not post stock, and the move completion boundary remains
mandatory. The original transfer barrier is unchanged after handoff.

The only exception is the existing private, transaction-scoped native operation,
which already validates actual QUNIT, required QC/QRUN, admissions, linear route,
processed executions, accepted transfers and Alert holds before entering native
MRP/Stock. Native repair capabilities still prohibit inventory and Backorder.
No RPC context can mint either capability. The public native release action and
experimental 17.0.2.50 release engine remain CLOSED. Consequently an opted-in MO
has no public stock-release path until the separate Enterprise validation and
release decision. This patch deliberately adds no release authority.

## Validation and deployment

Portable contract tests exercise shipped methods with small call-boundary
fixtures; they do not replace Enterprise ORM tests. Enterprise scenarios cover
standard PASS, missing identities, missing admission, incomplete route, earlier
WO versus future QC, direct ORM/RPC boundaries, historic completion and existing
native repair/Alert/Backorder scenarios under the new policy. Run those only on
a disposable Enterprise database. Review QCP and open MO scope read-only before
individually enabling policy; do not repair or reopen CAB/MO/05115, /05114,
/05113 or /05110 as part of deployment.

Identification follows the existing workflow BEFORE QC completion. For open MOs
whose QC is already closed PASS without QRUN, review a real follow-up control
and identity mapping before opting in; this patch does not reopen old QC or
manufacture identities to make them eligible.

Local validation: 42 portable tests passed; all module Python/XML parsed;
`git diff --check` passed. Enterprise ORM and a staging database connection were
not available, so Enterprise scenarios and the read-only inventory were not run.
