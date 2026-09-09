# AA1 local repository integration

At the user's request, AA1 is a normal directory of the local
`auto_adapter2.0` repository. Future batches are committed there, with paths
prefixed by `AA1/`. The original AA1 remote is not a push destination.

Imported source snapshot: `de3bf28`, including these checked batches:

- `d6e0c62`: native quadruped actuator semantics.
- `d35c5f8`: Holistic model transport and real API preflight.
- `de3bf28`: Go2 retained policy and physical/inference checks.

The complete original nested Git metadata and commits are preserved locally
in the parent repository's `.git/aa1-original.git`. Existing uncommitted AA1
implementation work remains in place and is not part of the imported source
snapshot. Parent-repository thesis edits and historical-file deletions are
untouched and excluded from this import commit.

Before this instruction was clarified, `d6e0c62` and `d35c5f8` were pushed
to a new branch named `aa1/capability-v2-native-control` on the original
AA1 remote. No further pushes to that repository are intended.
