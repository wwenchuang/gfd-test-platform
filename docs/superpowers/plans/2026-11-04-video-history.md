# Video History Management Implementation Plan

**Goal:** Make personal video records manageable without breaking generated drafts or running analysis.

**Architecture:** Store an atomic owner- and app-scoped history removal marker on terminal records, preserving source media and linked drafts; filter marked records before the existing listing limit. Offer row and selected-record removal in the existing task list, with explicit confirmation and server-enforced active-job protection. Improve hierarchy and responsive presentation of that same list.

**Verification:** Exercise ownership, all-or-nothing active protection, retained evidence and listing limits using real temporary stores; run existing UI and static suites, and inspect the page on a running server. Do not interrupt ongoing live video generation for deployment.

- [ ] Add failing store and HTTP tests for history removal, authorization and concurrency guards.
- [ ] Implement the minimum authenticated atomic operation and listing behavior.
- [ ] Add selection and individual removal, clear preservation notice and responsive layout; cover interaction and partial failure.
- [ ] Run focused checks and mandatory frontend checks; update CODEX_STATE.md; deploy and verify only when active jobs permit.
