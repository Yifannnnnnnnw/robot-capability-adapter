# Demo2: AutoAdapter 1.0 generation and validation with dynamic inputs

Demo2 keeps the AutoAdapter 1.0 direct-MuJoCo main line: the model studies the
complete MJCF, inspects the trusted skeleton, probes real MuJoCo, writes
`driver.py`, and is judged by the 1.0 direct-state validator.

Two inputs are dynamic: 2.0 Stage 1 seals the capabilities before generation,
and an implementation-blind Blue Line chooses the validator's cases and
thresholds. Evolution is a non-blocking sidecar. Demo2 does not use Validation
A or Validation B and makes no real-SDK/hardware claim.

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the exact architecture, three-robot
scope, commands, Repair feedback and acceptance boundary.
