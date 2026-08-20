# Benchmark Configs

Configs select existing canonical inputs; they do not contain copied robot
packages or generated evidence.

The minimum planned files are:

```text
benchmark.json   # protocol version, all 14 robots, common runtime, and price snapshot
b1.json          # seven Producers, two generation conditions, R, attempts, and budgets
b2.json          # formal methods, each B_a or fixed identity, tasks, seeds, and episodes
```

Every robot entry points to one package under
`../../autoadapter/libraries/robots/`. Exact provider model identifiers,
decoding settings, method versions, and prices are pinned before formal runs.
