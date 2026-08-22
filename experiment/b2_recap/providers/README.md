# B2 provider pins and connectivity diagnostic

`manifest.json` pins the seven AA2-B2 backbone source configurations together
with one common ReCAP prompt, response schema, budget set, and provider-client
policy. The B2 policy overrides source-run differences: every backbone uses
temperature `0`, `4096` output tokens, a `120 s` call timeout, native message
history, the same `80000`-character history bound, JSON-object transport, local
strict ReCAP validation, and the same bounded HTTP retry behavior.

Validate the tracked pins without making a provider request:

```bash
PYTHONPATH=autoadapter/src \
python3 experiment/b2_recap/providers/validate_manifest.py
```

Run one structured-output connectivity request for every M1–M7 level. The
runner reads credentials only into the parent process and writes exact
secret-free request/response message bodies to the ignored diagnostic report:

```bash
SSL_CERT_FILE=/private/etc/ssl/cert.pem \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=autoadapter/src \
python3 experiment/b2_recap/providers/run_connectivity.py \
  --env-file .env \
  --env-file .env.company-api \
  --output experiment/b2_recap/runs/provider-connectivity/report.json
```

`SSL_CERT_FILE` selects the macOS system CA bundle used by this workspace's
Python installation; it can be omitted when Python already has a working CA
bundle.

This does not run a robot, create a formal episode, clear the other Section 7
prerequisites, or enter the 210-episode denominator. A returned model identifier
is checked against its pin, but the gateway cannot independently attest the
upstream weights or revision; the report retains that limitation explicitly.
