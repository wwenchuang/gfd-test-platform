# Private idle WAL anchor HTTP comparison

Same frozen source `92e70a0` for both arms; no repository edits/deployment. Eight workers, six-scenario mixed workload: two save slots, jobs history200, idle Runner poll, Runner heartbeat, recording heartbeat;1000 seeded jobs and YAML files. Independent real auth, sanitization, event writes/transactions and HTTP finalizers retained. Only fixture OperationStore initialization/close monkeypatched to open one idle activated anchor. No retained writer, batching, filtering or event suppression.

Each fresh fixture has12 warmup requests plus measured requests. Before timing, credentials are generated solely inside mode0700 fixture trees (credential file0600). Only loopback HTTP client connects; real server fixture denies all outbound socket connections. All fixture trees removed after each suite, retaining only sanitized aggregated results.

Connection durability/defaults verified: synchronous2(FULL), wal_autocheckpoint1000; anchor journal_mode=wal and in_transaction=False after fully fetched schema_version. Ordinary per-event connections are unchanged except one initial assertion of their FULL/default checkpoint settings. Fixture driver polls real active_requests until1 (the health request itself), proving the completed workload handlers/finalizers have drained before SIGTERM; then shutdown/server_close precedes explicit anchor close. This is a private externally drained lifecycle. Production ThreadingHTTPServer has daemon_threads=True and block_on_close=False, so server_close alone does NOT drain handlers; there is no production operation-store close/reset here. Safe integration requires an explicit shutdown drain and bounded close/config-reset ownership. Whole lifecycle CPU starts before server-side identity/recording setup and ends after graceful HTTP shutdown, snapshot and explicit anchor close. It excludes interpreter startup before script entry and later read-only integrity/count check, equally in both arms. Final close/checkpoint work is included.

|Requests per fixture|Round|Current lifecycle CPU s|Anchor lifecycle CPU s|Anchor/current|Current serving CPU s|Anchor serving CPU s|Current append ms|Anchor append ms|
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|480|1|2.8152|2.6330|0.9353|2.1390|1.9614|296.00|147.84|
|480|2|2.4684|2.1677|0.8782|2.2071|1.9030|310.78|144.55|
|480|3|2.3836|2.1914|0.9194|2.1246|1.9365|299.54|146.25|
|2400|1|12.1928|10.7165|0.8789|11.5058|9.9969|1676.26|779.01|
|2400|2|11.4325|10.3132|0.9021|11.1475|10.0170|1638.12|778.81|
|2400|3|11.4367|10.0828|0.8816|11.1582|9.7996|1601.49|762.01|
|4800|1|22.8522|20.8620|0.9129|22.1392|20.1341|3226.72|1559.28|
|4800|2|22.1917|20.2581|0.9129|21.9082|19.9590|3150.51|1554.61|
|4800|3|22.3265|20.1641|0.9031|22.0315|19.8743|3184.87|1557.68|

4800-request suite per-scenario p95 milliseconds:

|Scenario|Current r1/r2/r3|Anchor r1/r2/r3|
|---|---|---|
|save|63.879/64.829/83.766|65.635/64.637/107.091|
|jobs|12.462/13.38/14.673|13.362/11.977/19.765|
|runner_idle_poll|5.333/5.173/5.393|5.261/5.295/5.415|
|runner_heartbeat|6.555/6.604/6.813|6.579/6.523/6.512|
|recording_heartbeat|16.331/16.972/20.074|18.223/15.494/23.157|

Every measured HTTP response in all18 fixtures was200; every fixture has exactly requests+12 events and distinct event IDs (492,2412,4812 respectively), zero detail rows for this workload, zero spool files, quick_check=ok. Health housekeeping is not audited; final active request count including health was1 before shutdown. Detail preservation beyond zero-detail fixture is not tested.

|4800 fixture|Main DB before close bytes|WAL before close bytes|SHM before close bytes|Main DB after close bytes|WAL/SHM after close|Close+snapshot CPU ms|Peak RSS bytes|
|---|---:|---:|---:|---:|---|---:|---:|
|current_r1|5734400|0|0|5734400|0/0|0.144|195264512|
|anchor_r1|5677056|4338392|32768|5738496|0/0|2.185|195674112|
|anchor_r2|5681152|4486712|32768|5738496|0/0|1.815|123011072|
|current_r2|5742592|0|0|5742592|0/0|0.222|123404288|
|current_r3|5722112|0|0|5722112|0/0|0.144|122945536|
|anchor_r3|5640192|4861632|32768|5718016|0/0|2.975|122945536|

WAL remains near the default checkpoint scale across492→2412→4812 events while data grows, with automatic checkpointing enabled; no unbounded WAL growth hidden outside the measured window. Last-close removes WAL/SHM and all4812 rows survive reopen. CPU reduction repeats including close/checkpoint; scenario tails and resident memory must still be assessed before integration.

This demonstrates a repeatable experimental CPU benefit and justifies considering ONE bounded idle-anchor candidate for further design/validation. It does not yet justify production integration or satisfy no-regression acceptance:4800-request pair3 save p95 rises83.766→107.091ms (+27.8%), jobs14.673→19.765ms (+34.7%), recording20.074→23.157ms (+15.4%). Pair1 save/recording also worsen slightly. Those noisy-but-material tails remain unresolved; no claim of tail safety is supported. WAL/SHM adds4.34–4.86MB of live footprint, while measured RSS stays comparable within each warm/cold pair. Production lifecycle feasibility additionally requires shutdown drain/reset/path/fork controls, which expands scope beyond retaining a handle. Required lifecycle/path identity/config reset/pre-fork controls and failure/spool/kill recovery tests are absent in this private monkeypatch. The fixture has one fixed store/path, no live database replacement, no fork, no outage injection, no nonzero item_details workload and no descriptor telemetry. Preserve FULL/event semantics; do not transplant the monkeypatch into production. Overall recorder+audit performance gate remains open until merged candidate is tested against the actual comparison baseline.

Sanitized raw aggregates: `/tmp/audit-anchor-http-results-480.json`, `/tmp/audit-anchor-http-results-2400.json`, `/tmp/audit-anchor-http-results.json`. Private instrumentation: `/tmp/audit-anchor-http-probe.py`, `/tmp/audit-anchor-server.py`.
