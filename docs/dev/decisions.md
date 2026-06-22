# Design decisions

The real architectural choices are recorded as short **ADRs** (Architecture Decision
Records). Each one captures the context, the decision, its consequences, and the
alternatives that were weighed — so the "why" isn't lost.

| # | Decision |
| --- | --- |
| 0001 | [Data ingestion](../decisions/0001-data-ingestion.md) — assembled Level 2 volumes from NOAA's public S3, dedupe on scan time |
| 0002 | [Backend rendering](../decisions/0002-backend-rendering.md) — render server-side to georeferenced images |
| 0003 | [Storage model](../decisions/0003-storage-model.md) — raw volumes on disk are truth; SQLite indexes them |
| 0004 | [Frontend stack](../decisions/0004-frontend-stack.md) — MapLibre + light vanilla JS, no heavy framework |
| 0005 | [Radar site selection](../decisions/0005-radar-site-selection.md) — nearest covering site from a bundled table |
| 0006 | [Configuration](../decisions/0006-configuration.md) — one config source, env-driven |
| 0007 | [Rendering geometry](../decisions/0007-rendering-geometry.md) — gate placement, projection, the dBZ palette |
| 0008 | [Mutable locations in SQLite](../decisions/0008-mutable-locations-sqlite.md) — locations become persisted state, env seeds only |
| 0009 | [Retention & pruning](../decisions/0009-retention-pruning.md) — bound the archive by age and/or size |
| 0010 | [Web-triggered backfill](../decisions/0010-web-triggered-backfill-job.md) — one-click backfill as an in-process async job; two-writer safety via WAL |
| 0011 | [Live-chunks frame](../decisions/0011-live-chunks-frame.md) — near-real-time frame from the chunks bucket, reconciled to assembled |
| 0012 | [Intra-volume SAILS cuts](../decisions/0012-intra-volume-sails-cuts.md) — surface each 0.5° surveillance cut as its own frame |
| 0013 | [Runtime retention](../decisions/0013-runtime-retention.md) — retention becomes DB-backed runtime state; env seeds only |

New architectural decisions should be added here as the next numbered file — see
[Contributing](contributing.md).
