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
python3 experiment/experiment1b_use/config/providers/validate_manifest.py
```

No new provider-connectivity request is part of the pre-formal gate. Existing
diagnostic reports retain their historical role outside the 210-episode
denominator; the next model requests are the deliberately launched formal
episodes. Returned identifiers are still checked against their pins, while the
gateway limitation on independently attesting upstream weights or revisions
remains explicit.
