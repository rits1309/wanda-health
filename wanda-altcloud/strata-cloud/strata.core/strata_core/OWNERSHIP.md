# Ownership registry

One writing service per domain. Any service may **read** any
canonical table; only the owner **writes**; cross-service writes go through the
owner's API. The machine-readable half is `strata_core/ownership.py` — the test
suite asserts it covers every table on the canonical metadata.

| Domain module | Tables | Writing service | Notes |
|---|---|---|---|
| `domains/kernel` (people) | `user_profiles`, `roles`, `user_roles`, `languages`, `user_languages` | **strata.engine.auth** | Provisioning, enriched `/me`, role assign/revoke (via the `strata.identity` seam library, which dual-writes Cognito groups) |
| `domains/kernel` (programmes) | `programmes`, `programme_assignments` | **strata.booking** | Programme configuration and membership; writer active from |
| `domains/kernel` (identifiers) | `user_external_id_types`, `user_external_ids` | **strata.engine.auth** | Typed external identities: `Cognito Sub`, the `Legacy Summit` family (Django User ID / Patient ID / Coach ID), device identifiers — the identity write path; moved from `domains/device_readings` at Identity Mapping (readings resolved); Strata.Connect's registration writes route through the identity-owned seam (Identity) |
| `domains/booking` | `appointments`, `reschedule_events`, `reassignment_events`, `availability_patterns`, `availability_pattern_versions`, `unavailability_blocks`, `unavailability_patterns`, `slots`, `reminders`, `reschedule_tokens`, `suggested_alternatives`, `outbox_events`, `notifications`, `call_records`, `cancellation_policies` | **strata.booking** | The booking service's domain (ported at); people columns reference the kernel's `user_profiles` |
| `domains/reference` | `diagnoses`, `procedures`, `products`, `active_ingredients`, `packagings`, `product_identifiers` | **strata.terminology** | Ingest pipelines (CMS order files, FDA NDC); writer extracted from strata.engine at |
| `domains/device_readings` | `raw_readings`, `reading_queue`, `weight_readings`, `blood_pressure_readings`, `quarantined_readings`, `readings_outbox_events` | **strata.connect** | Device readings pipeline (Connect); the external-IDs lookup moved to `domains/kernel` (identifiers) at Identity Mapping; people columns reference the kernel's `user_profiles` |
| `domains/clinical` | `member_diagnoses`, `member_procedures`, `member_medications`, `member_demographics` | **member-profile service** (future —; canonical name) | A member's health profile (design plan Part B). The domain lands ahead of its writer ( option-5 separability); until it ships, only fixtures and the imports write here. Terminology linkage by natural key (ICD-10 / FDA product_id) — deliberately no cross-domain FK; every column classified `clinical` |

Key conventions: string UUIDs for the kernel and all new tables; the
reference tables keep the BigInteger identity keys they shipped with (ported
shape-for-shape, SC-2: the ids are internal — `/v1` responses expose the natural
keys, NDC and ICD-10 codes — but the ingest pipelines rely on server-generated
identity keys, and the ~100k-row search tables are better served by compact
integer keys and FKs than by string UUIDs).
Single `public` schema; provenance lives in this registry and the module
layout, not in Postgres namespaces.
