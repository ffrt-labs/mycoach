# MyCoach

MyCoach turns an athlete's health and training data into an evolving plan, then learns from what the athlete actually performs.

## System boundaries

**Athlete**:
The person whose observed record and coaching artifacts the applications manage. The health-data application issues the Athlete ID carried by every record and event; MyCoach maps its local user to that identity.
_Avoid_: Default user, tenant

**Observed health fact**:
A record of the athlete's state or performance, independent of any coaching decision. A separate health-data application owns health snapshots, activities, and performed gym sets; MyCoach consumes them.
_Avoid_: Coaching data, health metric for an activity or performed set

**Observed athlete record**:
The complete collection of Observed health facts. One health-data application owns this record, with daily health and activities as parts of the same domain.
_Avoid_: MyCoach database, source-specific store

**Canonical activity**:
The single observed activity that represents a session after reconciling source records from Garmin, the logger, or an import. The health-data application owns ingestion, deduplication, and reconciliation.
_Avoid_: Garmin activity, logger activity, merged workout

**Source record**:
An immutable copy of data received from Garmin, the Logger, or an import. It is evidence used to build the Observed athlete record, not canonical truth or an event log.
_Avoid_: Canonical activity, Observed-health event

**Settled activity**:
A Canonical activity whose expected source reconciliation has completed or whose reconciliation deadline has passed. The health-data application publishes this state; MyCoach waits for it before producing final post-workout coaching.
_Avoid_: Fulfilled session, completed workout

**Health-data API**:
The versioned HTTP contract through which operational clients read or record Observed health facts. Writes and commands are idempotent; MyCoach and the Logger never share the health-data application's database.
_Avoid_: Shared schema

**Analytics view**:
A stable, read-only SQL projection of the Observed athlete record published for Grafana. Grafana reaches these views through a restricted database account and cannot access operational tables.
_Avoid_: Operational table, Health-data API response

**Logger**:
The gym-floor application that gives its browser client one stable interface. Its adapters fetch prescriptions from MyCoach and read or record observed facts through the Health-data API.
_Avoid_: MyCoach logger, static frontend

**Observed-health event**:
A durable fact published by the health-data application after it canonicalizes part of the Observed athlete record. Delivery is at least once; each event has a stable ID so consumers can handle duplicates idempotently.
_Avoid_: Coaching event, PlannedSessionFulfilled

**Coaching artifact**:
A decision or interpretation produced by MyCoach, including routines, planned sessions, prompts, and insights. MyCoach owns coaching artifacts even when they refer to observed health facts held elsewhere.
_Avoid_: Health fact, health metric

**Coaching context**:
The selection and interpretation of Observed health facts and Coaching artifacts prepared for a coaching decision. MyCoach builds it from factual Health-data API queries; the health-data application never returns prompt-shaped projections.
_Avoid_: Health-data projection, prompt-ready health response

**Insight revision**:
An immutable version of a coaching insight. When a settled activity changes in a coaching-relevant way, MyCoach produces a new version and retains the earlier ones.
_Avoid_: Insight overwrite, duplicate insight

**Prescription fulfillment**:
The association MyCoach makes between a Planned session and the Canonical activity that fulfilled it. Each application stores the other's immutable external ID; neither uses a cross-database foreign key.
_Avoid_: Activity foreign key, title matching, date matching

## Gym coaching

**Exercise**:
A gym movement whose identity stays stable even when its displayed wording changes.
_Avoid_: Movement, lift

**Exercise ID**:
The stable identity of a catalogue exercise. The health-data application owns the catalogue and its IDs; MyCoach references them in prescriptions.
_Avoid_: Exercise name, exercise title

**Exercise title**:
The human-readable label shown for an exercise and retained with a workout as display history; it is never identity.
_Avoid_: Exercise name

**Catalogue exercise**:
An exercise with an Exercise ID that can participate in prescriptions, history comparison, and progressive-overload reasoning.

**Local catalogue exercise**:
A Catalogue exercise owned by MyCoach because the upstream catalogue does not represent the athlete's movement precisely enough.

**Custom exercise**:
A freely entered exercise with no Exercise ID. It remains visible in workout history but is excluded from cross-session reasoning until promoted or mapped.
