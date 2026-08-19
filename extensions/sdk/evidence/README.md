# SDK extension evidence

The files under `legacy/` are historical General Demo artifacts copied from
the former SDK-route runs. No current runtime consumes them. They are not
inputs to the current route-check CLIs or to the environment definitions.

The Go2 directory preserves the complete legacy
`go2-formal-readiness-01` JSON set unchanged. Its historical claims remain
limited to the named Unitree SDK2, Go2 Translation, `unitree-go2-stock-12dof`
configuration, Linux/amd64 runtime, and the pinned MuJoCo scene route.

The SO directory preserves only the three files that exist in the source
`so-dgx-real-readiness` directory. Its top-level readiness report says `PASS`,
but the per-check payloads referenced by that report are not present here, so
the SO evidence closure is incomplete. The adjacent qualification manifest is
`DRAFT`; that DRAFT/PASS mismatch is preserved rather than resolved or
strengthened.

Historical JSON may contain old hashes and status labels. Do not recompute,
validate, extend, or consume those fields as a new readiness, status, hash, or
governance system. These artifacts do not establish hardware equivalence or
sim-to-real. Current SDK-extension results must identify the exact SDK,
robot-specific Translation, robot configuration, runtime, and MuJoCo route
actually exercised.
