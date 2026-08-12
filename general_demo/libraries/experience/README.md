# Experimental Experience Library

This directory is the small experiment-grade Experience route required by the
Authority.  The on-disk format is explicitly `experimental-1`; it is not a
claim that the open Experience schemas are frozen.

`autoadapter2.libraries.experience` accepts a candidate and a separate
declassification report only through exact file references and content hashes.
An approval must be supplied explicitly with `decision: HUMAN_APPROVED`,
`reviewer_kind: HUMAN`, a reviewer identity, a note, and a review time.  A
rejected review is stored under `experience_review_artifacts/`, outside this
Library, and never creates a record.

Approved records are written once under:

```text
general_demo/libraries/experience/records/<recipient>/<record_id>/<version>/
```

The record, review, and provenance files are immutable.  Corrections require a
new version and a new review.  `build_experience_snapshot` selects only exact
recipient/applicability matches and freezes a deterministic list of record
versions before a future run.  Its recipient projection is limited to
`experience_id`, `guidance`, `applicability`, and `provenance`; review material,
declassification reports, and raw evidence are not projected.

The review API keeps the review object closed.  Because the API must also let
the caller choose the immutable record identity, `record_id` and `version` are
keyword-only arguments and are required for approval.  They are never inferred
or generated.
