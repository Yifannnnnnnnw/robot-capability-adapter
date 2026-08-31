# Experiments

There is currently no active experiment workspace in this repository. New experiments have not yet been defined and must be specified separately before any manifest, protocol, runner, or result denominator is created.

The formally compiled thesis rooted at `thesis/Main.tex` supplies the current research framing and experiment numbering. It is not, by itself, an executable experiment protocol.

Historical experiment code, fixed inputs, runs, frozen source, videos, and local evidence are preserved under [`archive/pre_thesis_realign_2026-08-31/`](archive/pre_thesis_realign_2026-08-31/README.md). That snapshot is retained for inspection only: its runners are not an active mainline, and its results do not enter any future experiment denominator. Older historical run migrations remain under [`archive/runs/`](archive/runs/).

The only active shared experiment support retained here is:

- `path_layout.py`, for resolving supported historical run layouts;
- `tests/test_path_layout.py`, for the corresponding focused regression checks.
