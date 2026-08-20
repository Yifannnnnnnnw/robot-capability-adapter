# Focused Checks

Add only checks required by the benchmark path:

- the manifest resolves all 14 canonical robot packages;
- every B2 method adapter can enter and exit its declared public interface;
- B1 conditions receive the same fixed capability interface and validation suite;
- B2 methods receive the same fixed reference driver for each robot; and
- each observed false-success defect has one focused regression check.

Canonical robot, Harness, and driver-synthesis tests remain under
`../../autoadapter/tests/`.
