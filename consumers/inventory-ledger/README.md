# Independent inventory ledger

This is a separate Python distribution. Copy this directory anywhere, build it, and
install it beside the explicitly selected YHMF artifact. It does not import the YHMF
checkout, examples, codecs, migration internals or test helpers. Both packages use the
mccann_hub namespace. The dependency pins mccann-hub-yhmf==0.5.0.

```sh
uv build --wheel --project /path/to/inventory-ledger --out-dir /tmp/ledger-dist
python -m venv /tmp/ledger-evaluation
/tmp/ledger-evaluation/bin/pip install /path/to/mccann_hub_yhmf-0.5.0-py3-none-any.whl /tmp/ledger-dist/*.whl
cd /tmp
/tmp/ledger-evaluation/bin/python -I -m mccann_hub.inventory_ledger fresh
```

The qualification command creates a warehouse, two independently rooted locations,
a product and a lot. It receives 20, transfers seven and adjusts by -0.5. Expected
balances are 13 and 6.5. It checks product/location/lot queries, historical
entries, duplicate-ID rollback, overspend rollback, reference restrictions, indexes
and query plans. Native mode also exports and executes an M Product writer, then
checks Python queries and the exact tagged global value.

## Domain choices

Warehouse, Product and Lot identities are independent. Locations reference Warehouse
and own keyed balances with references to Product and Lot. Root ownership is local to
one location, avoiding a warehouse-wide stock budget. History stores location
identity snapshots, so history does not retain operational location objects forever.
This example's ledger service creates history in the same transaction as stock.
Generic framework writes can bypass these application business rules.

stock accepts exactly one product, location or lot filter. Product/lot queries return
matching location aggregates, including their other balances. limit/after bound root
pages; they do not count individual balances. Each location retains framework node
and child limits. history has its own bounded root-key page. The example intentionally
uses string timestamps and ordinary indexes so its historical binding can run on 0.4.0.

## Select future candidates

From the YHMF qualification checkout, run scripts/qualify_consumer.py with the exact
artifact path. The runner copies this project outside the checkout, builds its wheel,
creates a new environment, installs both artifacts, runs isolated Python and checks
installed dependencies. Change the committed exact dependency pin when selecting a
future version. A pin/artifact mismatch must fail resolution, not silently test main.

For native qualification, use the disposable scripts/test-native.sh harness with
YHMF_ARTIFACT_DIR and YHMF_CONSUMER=1. It creates separate databases for fresh and upgrade
cases, on each engine/journaling configuration. Do not run this evaluator on live data.
The native runtime is installed/configured independently of these Python packages.

## Historical upgrade evidence

The upgrade runner builds YHMF 0.4.0 from immutable commit
51e8456ae6762ac2ebc8db1a74b914c2cc3c280c. It builds a legacy consumer artifact pinning
that version, verifies dependency consistency, and persists stock and history. Then it
installs the selected current framework and consumer artifacts into the same environment.
Reopening the old binding must preserve raw data/schema/index/reference bytes. A new
Location revision then explicitly adds an indexed description through the public
migration planner and restartable job lifecycle. Stock workflows run afterward.

Offline mode carries public Store nodes through a JSON fixture between processes.
That fixture proves encoding/metadata compatibility, not engine concurrency or native
persistence. Native mode reopens the same actual database after dependency installation
and executes generated M after migration. No published historical artifacts exist;
this is source-built milestone evidence, not a promise about arbitrary old installations.
Other historical versions are covered by framework fixtures, not this artifact matrix.

## Observed developer experience

- Model declarations and atomic callbacks require no private framework access.
- Tuple-owned reads return Model statically. The legacy-compatible service needs a
  typing cast; current db.path offers a typed read selector. Mutations retain tuple
  paths. This is an ergonomic limitation, not a reason to add another query language.
- Queries return matching roots, not projected matching child rows. The application
  must describe its page units and filter balances deliberately when it needs rows.
- A dependency upgrade does not perform an application schema migration. Reopening
  legacy data and explicitly migrating a changed model are separate tested steps.
- Duplicate operation IDs reject the transaction and restore stock. They are not
  automatic idempotency tokens that accept different requests under the same ID.
- M source export needs package data templates. Isolated execution checks their actual
  presence; a successful import alone would not establish interoperability.
- Native setup, privileges and region sizes are operational requirements, not pip tasks.

No additional framework API was needed. The confirmed adoption blocker found in this
milestone was source artifact contamination by local agent state, fixed by the source
whitelist and artifact audit. Original consumer code is GPL-3.0-or-later. Review the
exact native driver/engine notices for an application's distribution and deployment.
