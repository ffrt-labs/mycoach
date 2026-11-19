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

**Body measurement**:
A weight and/or waist reading the athlete enters by hand on the Body page: an Observed health fact with no source record behind it. The health side is its source of truth and issues its ID, the one internal ID the Health-data API returns, used only to name the reading back for a delete. Garmin Connect receives a one-way, downstream copy of the weight; Garmin weigh-ins are never imported back.
_Avoid_: Body data owned by MyCoach, Garmin weigh-in, weight metric

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
A Canonical activity whose expected source reconciliation has completed, whose reconciliation deadline has passed, or which expects no second source (a Garmin-only activity, settled on arrival). The health side publishes this state; MyCoach waits for it before producing final post-workout coaching.
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

**Coaching principles**:
The versioned habit rules (`prompts/principles/v1.md`, from the Huberman Lab Neural Network Digest Vol. 1) appended to every coaching operation's system message. Each principle names the data that triggers it; the coach applies one only when the athlete's data gives a reason. It never recommends a supplement or a dose.
_Avoid_: Protocols, Huberman rules, supplement advice

**Insight revision**:
An immutable version of a coaching insight. When a settled activity changes in a coaching-relevant way, MyCoach produces a new version and retains the earlier ones.
_Avoid_: Insight overwrite, duplicate insight

**Prescription fulfillment**:
The association MyCoach makes between a Planned session and the Canonical activity that fulfilled it. Each side stores the other's immutable external ID, keyed to the specific performed set; neither uses a cross-schema foreign key.
_Avoid_: Activity foreign key, title matching, date matching

**Completion**:
Whether a Planned session was done: a qualifying activity of its sport happened anywhere in its Mon–Sun week (ADR 0005). Any source counts, including a Logger session that picked no prescription, but a gym activity with no performed sets never does. Completion needs no Prescription fulfillment and is not an Adherence score.
_Avoid_: Adherence, compliance, done on the day

**On planned day**:
Whether the activity that completed a Planned session happened on the day it was planned for. It is informational only: it never lowers Completion and is never described as a session moved or missed.
_Avoid_: Moved, missed, late, rescheduled

**Session intent**:
The training purpose of a Planned session, one of `strength`, `aerobic_base`, `threshold`, `intervals`, `recovery`, `skill`: the stimulus another activity must deliver to stand in for it. The cardio plan sets it per session and gym sessions are `strength`. A session with no intent (planned before intents existed, or given an unknown one) is never substitutable.
_Avoid_: Session type, workout type, zone

**Substitution**:
A Planned session left without a same-sport activity is **Substituted** when an unplanned activity in its week delivered its Session intent (classified from time in HR zones and training effect, or performed sets for `strength`) and lasted at least 70% of its planned duration. It counts toward Completion and is reported separately ("4/5 done · 1 substituted"). Same-sport matches are taken first and one activity fills at most one session. An activity with no HR zone data can't be classified and never substitutes.
_Avoid_: Swap, replacement, equivalent session

**Extra activity**:
An activity in the plan's week that completed no Planned session, by sport or by Substitution. Extras never raise Completion.
_Avoid_: Bonus session, unplanned workout, missed match

**Life context episode**:
A stretch of the athlete's life that every Coaching run reads around, told to MyCoach rather than measured, so a Coaching artifact and never an Observed health fact. It has a `kind` (`illness`, `travel`), a start day, an end day (its last affected day; none while open), a note, and kind-specific fields (an illness's severity: `mild` above the neck, `sick` below it). Episodes may overlap; on each day the most restrictive effect wins.
_Avoid_: Event, status, absence

**Excused session**:
A Planned session left not done that a Life context episode excuses: each `sick` day, and each Travel day marked "no training possible", excuses at most one in its week, its own day's session first. It leaves Completion's denominator ("2/2 done · 3 excused (ill 3 days)"). A day excuses once however many episodes cover it, and a session done while ill is done, never excused. `mild` days excuse nothing.
_Avoid_: Skipped, missed, cancelled session

**Ramp-back**:
The days after an illness ends when training builds back: 3 after a `mild` illness, else the illness's length clamped to 3-7. Guidance starts near 50% of normal load on day 1 and rises linearly to about 90% on the last day. Recovery data (HRV below baseline, resting HR elevated) on the last day may only extend it, never shorten it.
_Avoid_: Recovery period, comeback, return-to-play

**Travel**:
A `travel` Life context episode: dates (the return optional; a trip may be entered in advance), the **Equipment access** it gives (`none`, `hotel_gym`, `full_gym`; editable mid-trip), an optional time-zone shift in hours, and a "no training possible" toggle. Sick beats travel; mild illness on a trip means one light session the trip allows. A shift of 3 h or more brings the jet-lag principle into the briefing. Weekly plans read trips entered for their week; nothing regenerates mid-week.
_Avoid_: Trip mode, holiday, away

**Adapted (travel)**:
A Planned session on a Travel day filled by a Substitution under relaxed rules: the default intent match, or for a `strength` slot at least 20 min of resistance work without performed sets (a Logger entry or a Garmin strength activity). It counts toward Completion like a done session ("2/3 done · 1 adapted (travel)").
_Avoid_: Travel substitute, modified session

**Not done (travelling)**:
A Planned session on a Travel day left not done. Unlike an Excused session it stays in Completion's denominator; the recap names the trip as the cause and comments on what was done.
_Avoid_: Excused, missed while away

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
A Langfuse score measuring how a Canonical activity compares with the Planned session it fulfils, sent on the weekly plan's trace at Prescription fulfillment: set completion, weight, reps and RPE deltas for gym, duration ratio for cardio. A score with nothing to measure is left out rather than sent as zero. Only an explicitly linked session is scored; Completion is a separate, week-scoped measure.
_Avoid_: Compliance, completion rate
