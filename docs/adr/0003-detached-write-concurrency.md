# ADR 0003: Detached saves and transactional read/modify/write

Status: accepted for v0.3.1.

`get` returns a detached validated value. `save` is an intentional complete
replacement/upsert, with last-writer-wins semantics. Reading the same version in
two processes and then saving both increments can leave one increment, although
each individual save is atomic and indexes/references remain consistent.

The existing `Database.atomic` callback is the safe read/modify/write interface:
read the record inside the callback, change that attempt's object, then save it
through the same transaction handle. Native TP restarts rerun the callback with a
fresh handle. A retry must not reuse a detached object captured before the
callback. Callbacks can run multiple times and must avoid external side effects.

Coordinated native process tests force both readers to see the same starting
state, commit one update, then release the other. Detached saves finish at one;
atomic increments finish at two. Existing forced-restart and multi-process tests
also check retry-local state and transaction-handle lifetime.

Optimistic revision checking could expose lost updates as explicit conflicts, but
would add a stored record revision, detached revision tokens, and policies for
nested ownership, root replacement, migrations, restarts, and older writers.
Adding a revision field without coordinating old clients would give a false
consistency guarantee. Defer that feature beyond v0.4. Fine-grained mutations
instead read their affected owner inside each transaction attempt and preserve
unrelated nodes. Same-field patches serialize with the later committed mutation
winning; arbitrary arithmetic still belongs in an atomic read/modify/write.
