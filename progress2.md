# LLM Data Pipeline Refactor

## Context

The `refactor_llm_data.md` spec defines ideal metrics for the 4 coaching features. This document tracks the gap between current implementation and the spec, organized as a TODO list.

Gaps fall into 3 categories:
1. **Not extracted** — Garmin API already called, response saved in `raw_data`, but fields not mapped to model columns
2. **Not formatted** — data exists in DB but `_format_health()` / `_format_activity_detail()` skip it
3. **Not fetched** — would need new API calls or model changes

---

## 1. Health Snapshot Gaps

### Data not extracted (already in raw_data from existing API calls)

- [ ] **Recovery Time (hours)** — `get_training_status()` response contains `recoveryTime` or `recoveryTimeInMinutes`. Add `recovery_time_hours: float | None` to `DailyHealthSnapshot`.
  - Files: `models/health.py`, `sources/garmin/mappers.py` (`map_health_snapshot`), `_UPDATABLE_FIELDS`
  - Used by: Daily Briefing, Post-Workout Analysis

- [ ] **Load Focus** — `get_training_status()` response contains `trainingLoadBalance` (low aerobic / high aerobic / anaerobic percentages). Add `load_focus: str | None` (JSON) to `DailyHealthSnapshot`.
  - Files: `models/health.py`, `sources/garmin/mappers.py`
  - Used by: Weekly Plan, Weekly Recap

- [ ] **Body Battery morning value** — `get_body_battery()` returns a timeline list. Currently we extract `max(charged)` and `min(drained)`. Add `body_battery_morning: int | None` — the first Body Battery reading of the day.
  - Files: `models/health.py`, `sources/garmin/mappers.py`
  - Used by: Daily Briefing (current energy reserve)

- [ ] **HRV status text** — `get_hrv_data()` response likely has `status` or `baselineStatus` field (e.g., "BALANCED", "UNBALANCED", "LOW"). Add `hrv_status_text: str | None` to `DailyHealthSnapshot`.
  - Files: `models/health.py`, `sources/garmin/mappers.py`
  - Used by: Daily Briefing (autonomic recovery indicator)

### Data in DB but not sent to LLM

- [x] **Sleep stages** — `sleep_deep_minutes`, `sleep_light_minutes`, `sleep_rem_minutes`, `sleep_awake_minutes` all exist in DB but `_format_health()` (prompt_builder.py:34-57) does NOT include them. Only `sleep_score` + `sleep_duration_minutes` are sent.
  - Fix: Add to `field_labels` dict in `_format_health()`
  - Used by: Daily Briefing, Weekly Recap (spec requires full stage breakdown)

- [x] **Body Battery low** — `body_battery_low` exists in DB but not in `_format_health()`. Only `body_battery_high` is sent.
  - Fix: Add to `field_labels` dict in `_format_health()`
  - Used by: Post-Workout (body battery drain = high minus low)

- [x] **Max HR (daily)** — `max_hr` exists in DB, not sent via `_format_health()`.
  - Fix: Add to `field_labels` dict

### DB migration needed

- [ ] Create Alembic migration adding 4 new columns to `daily_health_snapshots`:
  - `recovery_time_hours FLOAT`
  - `load_focus TEXT` (JSON string)
  - `body_battery_morning INTEGER`
  - `hrv_status_text VARCHAR(50)`

---

## 2. Activity Gaps

### Data not extracted (likely in raw activity data)

- [ ] **EPOC** — Activity summary from `get_activities_by_date()` may contain EPOC value. Add `epoc: float | None` to `Activity` model.
  - Files: `models/activity.py`, `sources/garmin/mappers.py` (`map_activity`)
  - Used by: Post-Workout Analysis

- [ ] **Recovery Time post-activity** — Activity summary may contain recovery time recommendation. Add `recovery_time_minutes: int | None` to `Activity` model.
  - Files: `models/activity.py`, `sources/garmin/mappers.py`
  - Used by: Post-Workout Analysis

- [ ] **Avg Cadence** — Activity summary contains `averageRunningCadenceInStepsPerMinute` (running) or `averageSwimCadenceInStrokesPerMinute` (swimming). Add `avg_cadence: int | None` to `Activity` model.
  - Files: `models/activity.py`, `sources/garmin/mappers.py`
  - Used by: Post-Workout Analysis (running/swimming specific)

- [ ] **Avg SWOLF** — Activity summary contains `averageSwolf` for swimming. Add `avg_swolf: float | None` to `Activity` model.
  - Files: `models/activity.py`, `sources/garmin/mappers.py`
  - Used by: Post-Workout Analysis (swimming specific)

### Formatting gaps (data exists, sent poorly)

- [x] **HR Zones formatting** — `hr_zones` stored as raw JSON string, sent as-is to LLM. Create `_format_hr_zones()` to parse into readable format: "Zone 1: X min, Zone 2: Y min, ...".
  - File: `coaching/prompt_builder.py`
  - Used by: Post-Workout Analysis, Weekly Recap

- [x] **Activity summary too sparse** — `_format_activities()` (prompt_builder.py:71-82) only sends date, title, sport, duration. Missing: avg_hr, calories, distance, training_effect that are all available in the dict.
  - Fix: Enrich `_format_activities()` with optional detail fields
  - Used by: Daily Briefing (recent activities), Weekly Recap (all activities)

### DB migration needed

- [ ] Create Alembic migration adding 4 new columns to `activities`:
  - `epoc FLOAT`
  - `recovery_time_minutes INTEGER`
  - `avg_cadence INTEGER`
  - `avg_swolf FLOAT`

---

## 3. Prompt Builder Updates

### `_format_health()` (prompt_builder.py:34-57)

Currently sends 13 of 23+ available fields. After refactor should send:

- [ ] Add to `field_labels` dict:
  ```
  "sleep_deep_minutes": "Deep sleep (min)"
  "sleep_light_minutes": "Light sleep (min)"
  "sleep_rem_minutes": "REM sleep (min)"
  "sleep_awake_minutes": "Awake time (min)"
  "body_battery_low": "Body Battery low"
  "body_battery_morning": "Body Battery (morning)"
  "max_hr": "Max HR"
  "recovery_time_hours": "Recovery time (hours)"
  "load_focus": "Load Focus"
  "hrv_status_text": "HRV Status"
  ```

### `_format_activity_detail()` (prompt_builder.py:204-227)

- [ ] Add new activity fields to `field_labels` dict:
  ```
  "epoc": "EPOC"
  "recovery_time_minutes": "Recovery time (min)"
  "avg_cadence": "Avg cadence"
  "avg_swolf": "Avg SWOLF"
  ```

### `_format_activities()` (prompt_builder.py:71-82)

- [ ] Enrich activity summary lines with HR, distance, calories, training effect when available. Example:
  ```
  Before: "- 2024-06-10: Morning Swim [swimming] (60 min)"
  After:  "- 2024-06-10: Morning Swim [swimming] (60 min, 2.4km, avg HR 142, 450 cal, TE 3.2)"
  ```

### New function: `_format_hr_zones()`

- [ ] Create helper to parse JSON hr_zones into readable format. Use it in `_format_activity_detail()` instead of raw JSON dump.

### `snapshot_to_dict()` (prompt_builder.py:456-488)

- [ ] Add new health fields to the `fields` list:
  ```
  "recovery_time_hours"
  "load_focus"
  "body_battery_morning"
  "hrv_status_text"
  ```

### `activity_to_dict()` (prompt_builder.py:491-508)

- [ ] Add new activity fields to the returned dict:
  ```
  "epoc": activity.epoc
  "recovery_time_minutes": activity.recovery_time_minutes
  "avg_cadence": activity.avg_cadence
  "avg_swolf": activity.avg_swolf
  ```

---

## 4. Schema Updates

- [ ] `schemas/health.py` — Add 4 new fields to `DailyHealthSnapshotBase`
- [ ] `schemas/activity.py` — Add 4 new fields to `ActivityBase` / `ActivityRead`

---

## 5. Garmin Mapper Updates

### `map_health_snapshot()` (mappers.py:72-223)

- [ ] Extract `recovery_time_hours` from `training_status` dict (look for `recoveryTime`, `recoveryTimeInMinutes`)
- [ ] Extract `load_focus` from `training_status` dict (look for `trainingLoadBalance`)
- [ ] Extract `body_battery_morning` from `body_battery` list (first chronological reading)
- [ ] Extract `hrv_status_text` from `hrv` dict (look for `status`, `baselineStatus`, `currentStatus`)
- [ ] Add all 4 new fields to `_UPDATABLE_FIELDS` list

### `map_activity()` (mappers.py:226-264)

- [ ] Extract `epoc` from raw activity dict
- [ ] Extract `recovery_time_minutes` from raw activity dict
- [ ] Extract `avg_cadence` — `averageRunningCadenceInStepsPerMinute` or `averageSwimCadenceInStrokesPerMinute`
- [ ] Extract `avg_swolf` — `averageSwolf`

---

## 6. Test Updates

- [ ] `tests/test_sources/test_garmin.py` — Update test fixtures with new fields, verify mapper extracts them
- [ ] `tests/test_coaching/test_prompt_builder.py` — Verify `_format_health()` includes sleep stages and new fields
- [ ] `tests/test_coaching/test_prompt_builder.py` — Verify `_format_activities()` enriched output
- [ ] `tests/test_schemas.py` — Verify new schema fields

---

## 7. Pre-implementation: Investigate Raw Data

Before writing code, confirm exact Garmin API field names by inspecting existing `raw_data` blobs in the DB:

- [ ] Write script to query a sample `DailyHealthSnapshot.raw_data` and pretty-print the `training_status` and `hrv` sub-dicts
- [ ] Write script to query a sample `Activity.raw_data` for a running and swimming activity, look for EPOC, cadence, SWOLF, recovery time field names
- [ ] Document confirmed field name mappings

---

## Out of Scope

These are in the spec but deprioritized:
- **HR Recovery** (post-exercise HR drop) — needs per-activity detail API call (`get_activity(id)`)
- **Running dynamics** (ground contact time, vertical oscillation, stride length) — needs detailed endpoint
- **Performance Condition** — needs detailed endpoint
- **Power data** — only applicable with power meter
- **Weight / Body Composition** — separate Garmin API, separate feature
- **Muscle group mapping** for exercises — needs exercise→muscle lookup table
- **Unified Weekly Plan** — current gym+cardio split works well, no change needed

---

## Execution Order

1. Investigate raw data (Phase 7) — confirm field names
2. DB migration (Phase 1 + 2 migrations combined)
3. Garmin mappers (Phase 5)
4. Schemas (Phase 4)
5. Prompt builder (Phase 3)
6. Tests (Phase 6)
7. Manual verification: sync, trigger features, compare prompt logs
