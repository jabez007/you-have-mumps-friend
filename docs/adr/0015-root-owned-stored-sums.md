# ADR 0015: Root-owned stored Decimal sums

Status: accepted after [core native CI 42344716067](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42344716067)
and [inventory upgrade CI 42345725275](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42345725275).

## Problem and existing mechanisms

Virtual Pydantic fields already derive values from loaded objects. Stored rollups
need exact, indexed totals without decoding unrelated children. Existing partial
mutations already expose concrete before/after field occurrences, and secondary
transitions now union references and indexes for patches, groups, and policy deletes.
Named transforms already pin complete output and rebuild/verify secondary structures.

## Decision

Add a small builtin sum_of declaration on a root Decimal field. Compile a source
field identity through owned paths and require a child collection. Persist the path
and decimal-sum-v1 codec in the canonical contract. Keep scalar encodings/layout one.
Require nonnullable Decimal sources and root/ancestor local validation; preserve
full leaf validation. General callbacks/reference traversal/owned stored sums are
not part of this initial contract.

Bound source values to 18 significant and 18 fractional digits, magnitude below 1E18.
Use exact 1E-18 integer units with at most 40 aggregate unit digits. Reject larger
exponents/precision before expansion, independently of Decimal context. This avoids
rounding and unbounded coefficient/exponent alignment. Target constraints/index
limits still apply.

Whole writes derive totals after normal model validation and validate target adapters
without rerunning arbitrary leaf callbacks. Stored owner reads verify loaded inputs.
The shared secondary transition combines partial deltas once per surviving root,
reads one managed total, and adds its ordinary index changes to the same transaction.
Net-zero groups leave total/index nodes untouched; deleted roots have no total to
update. No sibling traversal or second indexing implementation is introduced.

## Compatibility and tradeoffs

Dependencies change only through an explicit named transform. Default-zero backfill
is unsafe for existing aggregates. Existing models without sums retain their schema
bytes. Full saves remain intentional whole-value replacement and last-writer-wins.
A stored value is updated at persistence boundaries; loaded objects are not tracked.

A shared total introduces real writer contention, even for separate children. Raw
writes can corrupt it; incremental operations trust a valid managed starting state
rather than scan siblings on every update. Full reads detect disagreement. Generated
M writers reject these models under ADR 0013, so the supported M path cannot change
rollup inputs. A broader writer or computation contract requires its own design.

## Qualification

Both supported engines passed the complete native suite and separate stress checks,
with journaling off and before-image journaling on. Coverage includes concurrent
child writers, total uniqueness contention, parent deletion races, forced restarts,
late rollback, and resumable warehouse-v1 transformation. The inventory example
compares virtual and stored totals, and its existing-balance transfers use one
net-zero patch group. [Native measurements](../inventory-performance.md) retain
raw samples and distinguish current warehouse-v2 workflows from the direct
quantity-only lower bound on the frozen warehouse-v1 model.

## Owned targets, issue 41

Extend the same decimal-sum-v1 contract to stored Decimal targets on owned objects.
Compile the ownership tree bottom up so each target refers to the authoritative
source FieldSpec. Require local validation on every ancestor, including a root
without its own sum. Sources cannot themselves be stored sums: there is no implicit
derived dependency graph or reference traversal.

Whole writes and child insertion derive each owned total after normal validation;
loaded owners recursively verify their totals. Partial changes group deltas by root
and concrete total path. An ownership declaration's physical depth identifies the
correct owner without reading siblings. Each surviving nonzero total contributes
one ordinary before/after field occurrence to shared index maintenance. Newly
inserted owners already contain initialized totals; removed owners release their
indexes without updating a deleted total. Surviving ancestors still receive deltas.
Net-zero groups avoid total/index I/O at every affected level.

Existing root-only metadata and bytes remain unchanged. Adding or changing an owned
sum requires a named transform; its zero default cannot backfill populated owners.
Each stored ancestor is an intentional shared conflict point. Adding location sums
does not remove contention from an existing warehouse total, and an index can add
its own shared blocks. Native comparisons must separate no totals, root-only totals,
and root plus location totals. Avoid declaring totals at every level by default.
