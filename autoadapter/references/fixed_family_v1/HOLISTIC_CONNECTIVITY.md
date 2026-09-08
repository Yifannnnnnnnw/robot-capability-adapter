# Holistic connectivity after credential update — 2026-09-08

The user supplied a replacement company API credential. It was written only to
the ignored local `.env.company-api` file; no credential is recorded here or in
run evidence. The existing configured long-request gateway and `X-Api-Key`
authentication were retained.

| Check | Actual result | Evidence |
| --- | --- | --- |
| Request `deepseek-v4-flash` | HTTP 400: unknown model; the prior HTTP 401 credential error is resolved | [Flash probe](../../runs/diagnostic/holistic-connectivity-20260908/probe_flash_new_key_20260908T113454Z.json) |
| Read `/v1/models` | HTTP 200; `deepseek.v3.2` is listed, V4 Flash is absent | [Model list](../../runs/diagnostic/holistic-connectivity-20260908/models_20260908T113524Z.json) |
| Request `deepseek.v3.2` | HTTP 200; response `OK.`; 9 input and 3 output tokens | [Generation probe](../../runs/diagnostic/holistic-connectivity-20260908/probe_v32_20260908T113644Z.json) |

The generation request asked only for an OK reply with a 16-token output limit;
no robot materials were sent. This confirms minimal generation connectivity, not
the full synthesis tool-use path or a robot capability result. Experimental
provider/model settings were not changed by these probes. The V4 Flash interrupted
runs and their original HTTP 402 records remain intact. The user subsequently
authorized using a cheap model for the remaining robots; the next six robot runs
will use V3.2 as a separate model condition, never reported as V4 Flash.
