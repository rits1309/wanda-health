# Field-level data classifications

The human-readable half of `classifications.py` (decision:, revised /
). Vocabulary — 's three classes: **`clinical`** (PHI) · **`personal`**
(PII) · **`internal`**. Service-level classification binds the log-safety rules at the
writer; this catalog makes sensitivity travel with the **data**, because reads are open
across domains — any service, human, or AI session touching a field looks it up here (or via
`classification_for(table, column)`) and applies the class's treatment (no logging of
clinical values, no SQL echo around clinical/personal, identifiers-only logging, etc. — the
rules live in).

**Adding a column or table?** Classify it in `classifications.py` in the same PR — the
completeness test fails the build otherwise. Explicit per column on purpose: nothing inherits
a default silently. Then run `inv classification-register`: the **per-field audit view**,
`DATA_CLASSIFICATION_REGISTER.md`, is generated from the registry and drift-gated in CI —
never edit it by hand (this file stays the judgement record; the register is the listing).

## Per-table summary

| Classification | Tables |
|---|---|
| **clinical** | `raw_readings`, `reading_queue`, `weight_readings`, `blood_pressure_readings`, `quarantined_readings`, `readings_outbox_events` — device-reading payloads and their pipeline (verbatim clinical values or refs/errors that may embed them); **`programme_assignments`** and the direct member↔programme encodings **`appointments.programme_id`**, **`outbox_events.payload`**, **`notifications.payload`** — membership of a condition-named programme reveals health information (the inference rule, owner 17-07-2026). The **clinical domain** (`member_diagnoses`, `member_procedures`, `member_medications`, `member_demographics` —, built): a member's health profile, every column `clinical` including ids and timestamps — the row's existence links a person to a condition, procedure, or medication. |
| **personal** | `user_profiles`, `user_roles`, `user_languages`, `user_external_ids`, and the booking domain's member/coach-linked tables (`appointments` (bar `programme_id`), `slots`, `availability_*`, `unavailability_*`, `reminders`, `reschedule_*`, `reassignment_events`, `suggested_alternatives`, `outbox_events`/`notifications` (bar `payload`), `call_records`) — facts about identifiable people and their schedules/events. |
| **internal** | The catalogues and configuration: `roles`, `languages`, `user_external_id_types`, `programmes`, `cancellation_policies`, and the public reference datasets (`diagnoses`, `procedures`, `products`, `active_ingredients`, `packagings`, `product_identifiers`). |

## Rules of thumb applied

- A table whose rows are **about an identifiable person** (or join a person to anything) is
  at least `personal` — including its `id`/timestamp columns: the row's existence is itself
  the fact.
- **Payload-bearing pipeline tables take their payload's class**: the readings queue/outbox
  are `clinical` even where a given column is a reference — `last_error` and event payloads
  can embed clinical values.
- **Condition-implying linkage is clinical — inference counts** (owner, 17-07-2026): a row
  need not carry a diagnosis code to reveal health information; linking a member to a
  condition-named programme is health information. Applied to **direct encodings** of the
  member↔programme relationship (the assignment table; the appointment's programme column;
  event payloads embedding both). Join-chain inference (a reminder → its appointment → the
  programme) is noted but not chased transitively — chase it and everything becomes
  clinical, which destroys the vocabulary's signal; the *service-level* classification of
  the writer (strata.booking = clinical) is what covers the joined whole.
- **Closed catalogues and configuration** with no member data are `internal`.
- Derived data (counts, aggregates) is NOT covered by the catalog — deriving from classified
  fields keeps the source's class unless deliberately de-identified; that judgement stays
  human.

## Judgement calls — arbitrated by the owner 17-07-2026

1. `reading_queue` / `readings_outbox_events` / `quarantined_readings` → **clinical** —
   ratified (refs + errors + event payloads may embed values).
2. `slots` / `availability_*` / `unavailability_*` → **personal** — ratified (a coach's
   schedule is a fact about the coach).
3. `outbox_events` / `notifications` → bookkeeping columns **personal**; `payload` columns
   **clinical** (they embed member↔programme encodings — the inference rule superseded the
   original "keep clinical values out of events" posture for programme linkage, which IS
   the event's content).
4. `user_languages` → **personal** — ratified (a fact about a person; languages do not imply
   a condition). `programme_assignments` → **clinical** — the owner's hypertension question:
   enrolment in a condition-named programme implies the condition. Whole table takes the max
   (coach assignments ride along — work allocation, but column-level classification cannot
   split rows).
