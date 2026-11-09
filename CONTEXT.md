# MyCoach

MyCoach turns an athlete's health and training data into an evolving plan, then learns from what the athlete actually performs.

## System boundaries

**Athlete**:
The person whose observed record and coaching artifacts the applications manage. The health side issues the Athlete ID carried by every record and event; MyCoach maps its local user to that identity.
_Avoid_: Default user, tenant

**Observed health fact**:
A record of the athlete's state or performance, independent of any coaching decision. The health side owns health snapshots, activities, and performed sets, behind a module boundary; MyCoach's coaching code consumes them but never writes them. A separate health-data application remains the eventual owner — this boundary is designed to become that split, not to replace it.
_Avoid_: Coaching data, health metric for an activity or performed set

**Observed athlete record**:
The complete collection of Observed health facts. One boundary owns this record today, with daily health and activities as parts of the same domain; a separate health-data application is the eventual owner.
_Avoid_: MyCoach database, source-specific store

**Canonical activity**:
The single observed activity that represents a session after reconciling source records from Garmin, the logger, or an import. The health side owns ingestion, deduplication, and reconciliation.
_Avoid_: Garmin activity, logger activity, merged workout

**Performed set**:
One exercise/weight/reps/RPE record within a Canonical activity, built from its contributing gym source record.
_Avoid_: GymWorkoutDetail, workout detail

**Source record**:
An immutable copy of data received from Garmin, the Logger, or an import. It is evidence used to build the Observed athlete record, not canonical truth or an event log. A new source becomes new source records, never a new column or a new value on an existing one.
_Avoid_: Canonical activity, Observed-health event

**Settled activity**:
A Canonical activity whose expected source reconciliation has completed or whose reconciliation deadline has passed. The health side publishes this state; MyCoach waits for it before producing final post-workout coaching.
_Avoid_: Fulfilled session, completed workout

**Health-data API**:
The interface through which coaching and the Logger read or record Observed health facts, never direct queries against health's own storage. Today it is in-process for MyCoach's coaching code and HTTP for the Logger, already a separate deployable; writes and commands are idempotent. The physical split moves the in-process half onto HTTP too — the split swaps transport, not the interface callers already use.
_Avoid_: Shared schema, direct health query

**Analytics views**:
Stable, read-only SQL projections of the Observed athlete record published for Grafana, versioned additive-only so a breaking change ships as a new view rather than mutating an existing one. Grafana reaches these through a restricted, read-only account and cannot access operational tables.
_Avoid_: Operational table, Health-data API response, Analytics view (singular)

**Logger**:
The gym-floor application that gives its browser client one stable interface. Its adapters fetch prescriptions from MyCoach and read or record observed facts through the Health-data API.
_Avoid_: MyCoach logger, static frontend

**Observed-health event**:
A typed fact raised when the health side canonicalizes part of the Observed athlete record, with a stable, deterministic ID so consumers can dedupe. Raised in-process today, with no delivery guarantee beyond the current process; durable, at-least-once delivery through a broker is a property the physical split adds, not one that exists yet. Canonical instances: `CanonicalActivityRecorded`, `CanonicalActivitySettled`, `CanonicalActivityRevised`.
_Avoid_: Coaching event, PlannedSessionFulfilled

**Coaching artifact**:
A decision or interpretation produced by MyCoach, including routines, planned sessions, prompts, and insights. MyCoach owns coaching artifacts even when they refer to observed health facts held elsewhere.
_Avoid_: Health fact, health metric

**Coaching context**:
The selection and interpretation of Observed health facts and Coaching artifacts prepared for a coaching decision. MyCoach builds it from factual Health-data API queries; the health side never returns prompt-shaped projections.
_Avoid_: Health-data projection, prompt-ready health response

**Insight revision**:
An immutable version of a coaching insight. When a settled activity changes in a coaching-relevant way, MyCoach produces a new version and retains the earlier ones.
_Avoid_: Insight overwrite, duplicate insight

**Prescription fulfillment**:
The association MyCoach makes between a Planned session and the Canonical activity that fulfilled it. Each side stores the other's immutable external ID, keyed to the specific performed set; neither uses a cross-schema foreign key.
_Avoid_: Activity foreign key, title matching, date matching

## Gym coaching

**Exercise**:
A gym movement whose identity stays stable even when its displayed wording changes.
_Avoid_: Movement, lift

**Exercise ID**:
The stable identity of a catalogue exercise. The health side owns the catalogue and its IDs; MyCoach references them in prescriptions.
_Avoid_: Exercise name, exercise title

**Exercise title**:
The human-readable label shown for an exercise and retained with a workout as display history; it is never identity.
_Avoid_: Exercise name

**Catalogue exercise**:
An exercise with an Exercise ID that can participate in prescriptions, history comparison, and progressive-overload reasoning.

**Custom exercise**:
A freely entered exercise with no Exercise ID. It remains visible in workout history but is excluded from cross-session reasoning. No promotion or mapping path into the catalogue exists yet.

## Coaching observability

**Coaching run**:
One execution of a coaching operation for an athlete: a daily briefing, a weekly plan, a post-workout analysis, or a weekly recap. Each run is one Langfuse trace with a deterministic ID seeded from what the run is about, and one generation per LLM attempt inside it. A forced regeneration is a new run that names the trace it supersedes.
_Avoid_: Job, request, LLM call

**Week session**:
The Langfuse session grouping every Coaching run about one athlete's training week, from its plan to its recap. A run joins the week its subject falls in (the recapped week, the activity's day), not the week it ran.
_Avoid_: User session, login session

**Langfuse score**:
A named value attached to a Coaching run's trace, with a deterministic ID so re-sending it overwrites rather than duplicates. Scores observe a run; they never steer it.
_Avoid_: Metric, eval result, rating

**Code check**:
A Langfuse score computed by code from what a generation already produced: `parse_ok`, `needed_retry`, `truncated`, and `fallback_used` for a gym slot. Every generation emits them, a provider failure included, and the evals harness emits the same ones.
_Avoid_: Validation, LLM judge

**Adherence score**:
A Langfuse score measuring how a Canonical activity compares with the Planned session it fulfils, sent on the weekly plan's trace at Prescription fulfillment: set completion, weight, reps and RPE deltas for gym, duration ratio for cardio. A score with nothing to measure is left out rather than sent as zero.
_Avoid_: Compliance, completion rate
