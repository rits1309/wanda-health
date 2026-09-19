# Data Classification Register

**Generated document — never edit by hand.** Rendered from the classification
registry (`classifications.py`), the canonical metadata, and the ownership
registry (`ownership.py`) by `inv classification-register`; the drift gate
(`tests/test_classification_register.py`) fails CI while this file does not
match the code. This is the per-field audit projection of rule 6
the registry stays the source of truth. Vocabulary and handling rules:
`CLASSIFICATIONS.md` and the suite's `Savanna_Data_Classification_Standard.md`.

| | |
|---|---|
| **Schema version (Alembic head)** | `fe4c1ea9a6e7` |
| **Tables / columns** | 40 / 346 |
| **clinical / personal / internal columns** | 100 / 166 / 80 |
| **Regenerated** | 31-07-2026 |

## `active_ingredients`

Domain `reference` · writer strata.terminology

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `BIGINT` | no | **internal** |
| `product_pk` | `BIGINT` | no | **internal** |
| `ord` | `SMALLINT` | no | **internal** |
| `name` | `TEXT` | no | **internal** |
| `strength` | `TEXT` | yes | **internal** |

## `appointments`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `slot_id` | `VARCHAR` | no | **personal** |
| `coach_id` | `VARCHAR` | no | **personal** |
| `member_id` | `VARCHAR` | no | **personal** |
| `programme_id` | `VARCHAR` | no | **clinical** |
| `status` | `VARCHAR` | no | **personal** |
| `created_by` | `VARCHAR` | no | **personal** |
| `acting_user_id` | `VARCHAR` | no | **personal** |
| `on_behalf_of_coach_id` | `VARCHAR` | yes | **personal** |
| `booking_language_matched` | `BOOLEAN` | no | **personal** |
| `booking_preferred_language` | `VARCHAR` | yes | **personal** |
| `original_appointment_id` | `VARCHAR` | yes | **personal** |
| `cancelled_by` | `VARCHAR` | yes | **personal** |
| `cancellation_reason` | `VARCHAR` | yes | **personal** |
| `no_show_detection` | `VARCHAR` | yes | **personal** |
| `no_show_detected_at` | `DATETIME` | yes | **personal** |
| `idempotency_key` | `VARCHAR` | yes | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `availability_pattern_versions`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `pattern_id` | `VARCHAR` | no | **personal** |
| `version` | `INTEGER` | no | **personal** |
| `snapshot` | `JSONB` | no | **personal** |
| `changed_by` | `VARCHAR` | no | **personal** |
| `on_behalf_of_coach_id` | `VARCHAR` | yes | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `availability_patterns`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `coach_id` | `VARCHAR` | no | **personal** |
| `days_of_week` | `ARRAY` | no | **personal** |
| `start_time_local` | `TIME` | no | **personal** |
| `end_time_local` | `TIME` | no | **personal** |
| `slot_duration_minutes` | `INTEGER` | no | **personal** |
| `timezone` | `VARCHAR` | no | **personal** |
| `active_from` | `DATE` | no | **personal** |
| `active_to` | `DATE` | no | **personal** |
| `version` | `INTEGER` | no | **personal** |
| `status` | `VARCHAR` | no | **personal** |
| `generation_watermark` | `DATE` | yes | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `blood_pressure_readings`

Domain `device_readings` · writer strata.connect

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **clinical** |
| `user_id` | `VARCHAR` | no | **clinical** |
| `device_id` | `VARCHAR` | no | **clinical** |
| `provider_reading_id` | `VARCHAR` | no | **clinical** |
| `recorded_at` | `DATETIME` | no | **clinical** |
| `received_at` | `DATETIME` | no | **clinical** |
| `systolic_mmhg` | `INTEGER` | no | **clinical** |
| `diastolic_mmhg` | `INTEGER` | no | **clinical** |
| `pulse_bpm` | `INTEGER` | no | **clinical** |
| `irregular` | `BOOLEAN` | no | **clinical** |
| `suspect` | `BOOLEAN` | no | **clinical** |
| `correlation_id` | `VARCHAR` | no | **clinical** |
| `raw_ref` | `VARCHAR` | no | **clinical** |
| `created_at` | `DATETIME` | no | **clinical** |
| `updated_at` | `DATETIME` | no | **clinical** |

## `call_records`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `coach_id` | `VARCHAR` | no | **personal** |
| `member_id` | `VARCHAR` | no | **personal** |
| `initiated_at_utc` | `DATETIME` | no | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `cancellation_policies`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **internal** |
| `scope` | `VARCHAR` | no | **internal** |
| `programme_id` | `VARCHAR` | no | **internal** |
| `coach_id` | `VARCHAR` | yes | **internal** |
| `cancellation_window_hours` | `INTEGER` | no | **internal** |
| `cancellation_allowed_by` | `VARCHAR` | no | **internal** |
| `version` | `INTEGER` | no | **internal** |
| `active` | `BOOLEAN` | no | **internal** |
| `created_at` | `DATETIME` | no | **internal** |
| `updated_at` | `DATETIME` | no | **internal** |

## `diagnoses`

Domain `reference` · writer strata.terminology

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `BIGINT` | no | **internal** |
| `code` | `TEXT` | no | **internal** |
| `short_title` | `TEXT` | no | **internal** |
| `long_title` | `TEXT` | no | **internal** |
| `order_number` | `INTEGER` | no | **internal** |
| `ingested_at` | `DATETIME` | no | **internal** |

## `languages`

Domain `kernel.people` · writer strata.engine.auth

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `code` | `VARCHAR` | no | **internal** |
| `created_at` | `DATETIME` | no | **internal** |
| `updated_at` | `DATETIME` | no | **internal** |

## `member_demographics`

Domain `clinical` · writer member-profile service (future)

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `member_user_id` | `VARCHAR` | no | **clinical** |
| `sex` | `VARCHAR` | yes | **clinical** |
| `date_of_birth` | `DATE` | yes | **clinical** |
| `ethnicity` | `VARCHAR` | yes | **clinical** |
| `marital_status` | `VARCHAR` | yes | **clinical** |
| `height_in` | `NUMERIC` | yes | **clinical** |
| `created_at` | `DATETIME` | no | **clinical** |
| `updated_at` | `DATETIME` | no | **clinical** |

## `member_diagnoses`

Domain `clinical` · writer member-profile service (future)

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **clinical** |
| `member_user_id` | `VARCHAR` | no | **clinical** |
| `code` | `VARCHAR` | no | **clinical** |
| `starts_on` | `DATE` | yes | **clinical** |
| `ends_on` | `DATE` | yes | **clinical** |
| `created_at` | `DATETIME` | no | **clinical** |
| `updated_at` | `DATETIME` | no | **clinical** |

## `member_medications`

Domain `clinical` · writer member-profile service (future)

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **clinical** |
| `member_user_id` | `VARCHAR` | no | **clinical** |
| `product_id` | `VARCHAR` | no | **clinical** |
| `note` | `TEXT` | yes | **clinical** |
| `starts_on` | `DATE` | yes | **clinical** |
| `ends_on` | `DATE` | yes | **clinical** |
| `created_at` | `DATETIME` | no | **clinical** |
| `updated_at` | `DATETIME` | no | **clinical** |

## `member_procedures`

Domain `clinical` · writer member-profile service (future)

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **clinical** |
| `member_user_id` | `VARCHAR` | no | **clinical** |
| `code` | `VARCHAR` | no | **clinical** |
| `starts_on` | `DATE` | yes | **clinical** |
| `ends_on` | `DATE` | yes | **clinical** |
| `status` | `VARCHAR` | yes | **clinical** |
| `created_at` | `DATETIME` | no | **clinical** |
| `updated_at` | `DATETIME` | no | **clinical** |

## `notifications`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `channel` | `VARCHAR` | no | **personal** |
| `recipient_kind` | `VARCHAR` | no | **personal** |
| `recipient_id` | `VARCHAR` | no | **personal** |
| `notification_type` | `VARCHAR` | no | **personal** |
| `payload` | `JSONB` | no | **clinical** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `outbox_events`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `event_type` | `VARCHAR` | no | **personal** |
| `payload` | `JSONB` | no | **clinical** |
| `occurred_at` | `DATETIME` | no | **personal** |
| `published_at` | `DATETIME` | yes | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `packagings`

Domain `reference` · writer strata.terminology

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `BIGINT` | no | **internal** |
| `product_pk` | `BIGINT` | no | **internal** |
| `package_ndc` | `TEXT` | no | **internal** |
| `description` | `TEXT` | yes | **internal** |
| `marketing_start_date` | `DATE` | yes | **internal** |
| `marketing_end_date` | `DATE` | yes | **internal** |
| `sample` | `BOOLEAN` | yes | **internal** |

## `procedures`

Domain `reference` · writer strata.terminology

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `BIGINT` | no | **internal** |
| `code` | `TEXT` | no | **internal** |
| `short_title` | `TEXT` | no | **internal** |
| `long_title` | `TEXT` | no | **internal** |
| `order_number` | `INTEGER` | no | **internal** |
| `ingested_at` | `DATETIME` | no | **internal** |

## `product_identifiers`

Domain `reference` · writer strata.terminology

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `BIGINT` | no | **internal** |
| `product_pk` | `BIGINT` | no | **internal** |
| `system` | `TEXT` | no | **internal** |
| `value` | `TEXT` | no | **internal** |

## `products`

Domain `reference` · writer strata.terminology

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `BIGINT` | no | **internal** |
| `product_id` | `TEXT` | no | **internal** |
| `product_ndc` | `TEXT` | no | **internal** |
| `brand_name` | `TEXT` | yes | **internal** |
| `brand_name_base` | `TEXT` | yes | **internal** |
| `generic_name` | `TEXT` | yes | **internal** |
| `labeler_name` | `TEXT` | yes | **internal** |
| `dosage_form` | `TEXT` | yes | **internal** |
| `product_type` | `TEXT` | yes | **internal** |
| `marketing_category` | `TEXT` | yes | **internal** |
| `marketing_start_date` | `DATE` | yes | **internal** |
| `marketing_end_date` | `DATE` | yes | **internal** |
| `listing_expiration_date` | `DATE` | yes | **internal** |
| `dea_schedule` | `TEXT` | yes | **internal** |
| `application_number` | `TEXT` | yes | **internal** |
| `spl_id` | `TEXT` | yes | **internal** |
| `finished` | `BOOLEAN` | yes | **internal** |
| `routes` | `ARRAY` | yes | **internal** |
| `pharm_classes` | `JSONB` | yes | **internal** |
| `raw` | `JSONB` | no | **internal** |
| `ingested_at` | `DATETIME` | no | **internal** |

## `programme_assignments`

Domain `kernel.programmes` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **clinical** |
| `programme_id` | `VARCHAR` | no | **clinical** |
| `user_id` | `VARCHAR` | no | **clinical** |
| `role_id` | `VARCHAR` | no | **clinical** |
| `created_at` | `DATETIME` | no | **clinical** |
| `updated_at` | `DATETIME` | no | **clinical** |

## `programmes`

Domain `kernel.programmes` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **internal** |
| `name` | `VARCHAR` | no | **internal** |
| `suggestion_window_hours` | `INTEGER` | no | **internal** |
| `reassignment_notification_enabled` | `BOOLEAN` | no | **internal** |
| `no_show_grace_minutes` | `INTEGER` | no | **internal** |
| `reminder_lead_hours` | `INTEGER` | no | **internal** |
| `slot_horizon_weeks` | `INTEGER` | no | **internal** |
| `reschedule_link_ttl_days` | `INTEGER` | no | **internal** |
| `created_at` | `DATETIME` | no | **internal** |
| `updated_at` | `DATETIME` | no | **internal** |

## `quarantined_readings`

Domain `device_readings` · writer strata.connect

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **clinical** |
| `correlation_id` | `VARCHAR` | no | **clinical** |
| `raw_ref` | `VARCHAR` | no | **clinical** |
| `device_id` | `VARCHAR` | no | **clinical** |
| `reading_type` | `VARCHAR` | no | **clinical** |
| `state` | `VARCHAR` | no | **clinical** |
| `created_at` | `DATETIME` | no | **clinical** |
| `updated_at` | `DATETIME` | no | **clinical** |

## `raw_readings`

Domain `device_readings` · writer strata.connect

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **clinical** |
| `payload` | `JSONB` | no | **clinical** |
| `correlation_id` | `VARCHAR` | no | **clinical** |
| `received_at` | `DATETIME` | no | **clinical** |
| `created_at` | `DATETIME` | no | **clinical** |
| `updated_at` | `DATETIME` | no | **clinical** |

## `reading_queue`

Domain `device_readings` · writer strata.connect

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **clinical** |
| `raw_ref` | `VARCHAR` | no | **clinical** |
| `correlation_id` | `VARCHAR` | no | **clinical** |
| `state` | `VARCHAR` | no | **clinical** |
| `receive_count` | `INTEGER` | no | **clinical** |
| `last_error` | `VARCHAR` | yes | **clinical** |
| `visible_at` | `DATETIME` | yes | **clinical** |
| `created_at` | `DATETIME` | no | **clinical** |
| `updated_at` | `DATETIME` | no | **clinical** |

## `readings_outbox_events`

Domain `device_readings` · writer strata.connect

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **clinical** |
| `event_type` | `VARCHAR` | no | **clinical** |
| `payload` | `JSONB` | no | **clinical** |
| `occurred_at` | `DATETIME` | no | **clinical** |
| `published_at` | `DATETIME` | yes | **clinical** |
| `created_at` | `DATETIME` | no | **clinical** |
| `updated_at` | `DATETIME` | no | **clinical** |

## `reassignment_events`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `appointment_id` | `VARCHAR` | no | **personal** |
| `from_coach_id` | `VARCHAR` | no | **personal** |
| `to_coach_id` | `VARCHAR` | no | **personal** |
| `from_slot_id` | `VARCHAR` | no | **personal** |
| `to_slot_id` | `VARCHAR` | no | **personal** |
| `initiating_user_id` | `VARCHAR` | no | **personal** |
| `on_behalf_of_coach_id` | `VARCHAR` | yes | **personal** |
| `occurred_at` | `DATETIME` | no | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `reminders`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `appointment_id` | `VARCHAR` | no | **personal** |
| `recipient_kind` | `VARCHAR` | no | **personal** |
| `recipient_id` | `VARCHAR` | no | **personal** |
| `due_at_utc` | `DATETIME` | no | **personal** |
| `status` | `VARCHAR` | no | **personal** |
| `sent_at` | `DATETIME` | yes | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `reschedule_events`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `from_appointment_id` | `VARCHAR` | no | **personal** |
| `to_appointment_id` | `VARCHAR` | no | **personal** |
| `initiated_by` | `VARCHAR` | no | **personal** |
| `acting_user_id` | `VARCHAR` | no | **personal** |
| `from_slot_id` | `VARCHAR` | no | **personal** |
| `to_slot_id` | `VARCHAR` | no | **personal** |
| `occurred_at` | `DATETIME` | no | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `reschedule_tokens`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `appointment_id` | `VARCHAR` | no | **personal** |
| `token_hash` | `VARCHAR` | no | **personal** |
| `audience` | `VARCHAR` | no | **personal** |
| `expires_at` | `DATETIME` | no | **personal** |
| `used_at` | `DATETIME` | yes | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `roles`

Domain `kernel.people` · writer strata.engine.auth

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **internal** |
| `name` | `VARCHAR` | no | **internal** |
| `created_at` | `DATETIME` | no | **internal** |
| `updated_at` | `DATETIME` | no | **internal** |

## `slots`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `coach_id` | `VARCHAR` | no | **personal** |
| `pattern_id` | `VARCHAR` | yes | **personal** |
| `pattern_version` | `INTEGER` | yes | **personal** |
| `start_utc` | `DATETIME` | no | **personal** |
| `end_utc` | `DATETIME` | no | **personal** |
| `duration_minutes` | `INTEGER` | no | **personal** |
| `status` | `VARCHAR` | no | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `suggested_alternatives`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `cancelled_appointment_id` | `VARCHAR` | no | **personal** |
| `rank` | `INTEGER` | no | **personal** |
| `kind` | `VARCHAR` | no | **personal** |
| `slot_id` | `VARCHAR` | no | **personal** |
| `token_hash` | `VARCHAR` | no | **personal** |
| `status` | `VARCHAR` | no | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `unavailability_blocks`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `coach_id` | `VARCHAR` | no | **personal** |
| `date` | `DATE` | no | **personal** |
| `start_time_local` | `TIME` | no | **personal** |
| `end_time_local` | `TIME` | no | **personal** |
| `timezone` | `VARCHAR` | no | **personal** |
| `created_by` | `VARCHAR` | no | **personal** |
| `on_behalf_of_coach_id` | `VARCHAR` | yes | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `unavailability_patterns`

Domain `booking` · writer strata.booking

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `coach_id` | `VARCHAR` | no | **personal** |
| `days_of_week` | `ARRAY` | no | **personal** |
| `start_time_local` | `TIME` | no | **personal** |
| `end_time_local` | `TIME` | no | **personal** |
| `timezone` | `VARCHAR` | no | **personal** |
| `active_from` | `DATE` | no | **personal** |
| `active_to` | `DATE` | no | **personal** |
| `status` | `VARCHAR` | no | **personal** |
| `created_by` | `VARCHAR` | no | **personal** |
| `on_behalf_of_coach_id` | `VARCHAR` | yes | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `user_external_id_types`

Domain `kernel.identifiers` · writer strata.engine.auth

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **internal** |
| `name` | `VARCHAR` | no | **internal** |
| `created_at` | `DATETIME` | no | **internal** |
| `updated_at` | `DATETIME` | no | **internal** |

## `user_external_ids`

Domain `kernel.identifiers` · writer strata.engine.auth

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `user_id` | `VARCHAR` | no | **personal** |
| `type_id` | `VARCHAR` | no | **personal** |
| `external_id` | `VARCHAR` | no | **personal** |
| `registered_at` | `DATETIME` | no | **personal** |
| `ended_at` | `DATETIME` | yes | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `user_languages`

Domain `kernel.people` · writer strata.engine.auth

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `user_id` | `VARCHAR` | no | **personal** |
| `language_code` | `VARCHAR` | no | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `user_profiles`

Domain `kernel.people` · writer strata.engine.auth

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `email` | `VARCHAR` | yes | **personal** |
| `first_name` | `VARCHAR` | no | **personal** |
| `last_name` | `VARCHAR` | no | **personal** |
| `display_name` | `VARCHAR` | yes | **personal** |
| `timezone` | `VARCHAR` | no | **personal** |
| `preferred_language_code` | `VARCHAR` | yes | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `user_roles`

Domain `kernel.people` · writer strata.engine.auth

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **personal** |
| `user_id` | `VARCHAR` | no | **personal** |
| `role_id` | `VARCHAR` | no | **personal** |
| `created_at` | `DATETIME` | no | **personal** |
| `updated_at` | `DATETIME` | no | **personal** |

## `weight_readings`

Domain `device_readings` · writer strata.connect

| Column | Type | Nullable | Classification |
|---|---|---|---|
| `id` | `VARCHAR` | no | **clinical** |
| `user_id` | `VARCHAR` | no | **clinical** |
| `device_id` | `VARCHAR` | no | **clinical** |
| `provider_reading_id` | `VARCHAR` | no | **clinical** |
| `recorded_at` | `DATETIME` | no | **clinical** |
| `received_at` | `DATETIME` | no | **clinical** |
| `weight_kg` | `NUMERIC` | yes | **clinical** |
| `tare_kg` | `NUMERIC` | yes | **clinical** |
| `weight_lbs` | `NUMERIC` | yes | **clinical** |
| `tare_lbs` | `NUMERIC` | yes | **clinical** |
| `suspect` | `BOOLEAN` | no | **clinical** |
| `correlation_id` | `VARCHAR` | no | **clinical** |
| `raw_ref` | `VARCHAR` | no | **clinical** |
| `created_at` | `DATETIME` | no | **clinical** |
| `updated_at` | `DATETIME` | no | **clinical** |
