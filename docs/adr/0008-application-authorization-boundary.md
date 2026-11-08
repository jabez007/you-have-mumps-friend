# ADR 0008: Keep authorization at the application boundary

Status: accepted; resolves the design decision in
[issue 19](https://github.com/jabez007/you-have-mumps-friend/issues/19).

## Problem and existing mechanisms

Database exposes explicit CRUD, queries, transactions, maintenance, and raw addressing.
It has no authenticated principal, tenant context, or policy language. The inventory
service enforces business invariants, but accepts no caller identity. Its methods
therefore do not authorize callers. Adding a predicate to one query would leave get,
inverse reads, counts, nested mutations, and maintenance outside that rule.

M and direct YDBPython writers can access the same globals without invoking Database.
YottaDB's [security philosophy](https://docs.yottadb.com/AdminOpsGuide/securityph.html)
uses operating-system process identities and database-file permissions. It documents
shared-memory exceptions and recommends layered controls where finer isolation is
needed. A Python wrapper cannot impose record permissions on another process that
can access the underlying database.

## Decision

Keep authorization in an application service for now. Authenticate before calling it,
apply read and write policies there, and expose only that service to its clients.
Keep raw Database, Store, maintenance access, and deployment credentials inside the
trusted application boundary. Application policies must cover queries, direct reads,
reference traversal, writes, diagnostics, and exports according to that application's
requirements. YHMF makes no claim that such a service is already provided.

Database and raw_path are persistence interfaces, not security boundaries. Their
schema, transaction, ownership, and reference checks protect storage invariants;
they do not authorize principals or hide information from database-file users.
An M writer must apply the application's rules itself or be restricted by a separate
trusted service and deployment controls. Schema metadata and triggers are not an
authorization barrier against a writer able to replace them.

## Alternatives and tradeoffs

A scoped Database handle could help cooperative Python applications, but would need
explicit principals and complete read/write/maintenance semantics. It would still
not constrain independent M writers. No current example supplies enough requirements
to define those policies, so do not introduce an incomplete access-rule API.

Isolation through separate database files or a trusted service changes deployment
and application architecture. The operator must assess those choices and YottaDB's
documented exceptions for the actual environment. This decision does not certify a
deployment or add a tenant isolation guarantee.

Revisit mapper-level policy hooks when a real application demonstrates repeated
authorization logic and defines its principal model, permitted writers, query
visibility, diagnostics, and maintenance privileges. Any implementation must then
test all public access paths and demonstrate that denials do not expose protected
data. No storage, schema identity, or public API changes accompany this decision.
