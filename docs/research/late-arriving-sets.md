# Late-arriving sets after a refused claim (issue #134)

Question: if a claim on a `PlannedSession` is refused because the activity has no performed sets, can sets arrive later, and is `claim_planned_session` re-run when they do?

Source: MyCoach code at `main` (function names cited, not line numbers). No source was changed.

## Answer

**No path adds sets to an existing activity, and nothing re-runs `claim_planned_session`.** Sets are written in exactly one place, at the moment the activity is created. So a claim refused for "no sets" can never become valid through late-arriving sets under the current code. A retry mechanism would have no trigger to hang off.

## Findings

1. **`GymWorkoutDetail` rows are created only in `sources/importer.py::import_workouts`.** A repo-wide grep for `GymWorkoutDetail(` outside models finds that one constructor. Every other reference (routes/activities, routes/logger, pages/history, coaching/context) only reads. There is no update or append endpoint for sets. `/activities` exposes only GET and `POST /{id}/analyze`.

2. **`import_workouts` is create-only per workout.** `_workout_exists` dedups on `(user_id, data_source in {source, "merged"}, external_id)` or, without an external id, `(title, start_time)`. A duplicate is counted as `activities_skipped` and `continue`d. So a later re-post or re-import of the same workout with sets does not add them to the earlier empty activity. The docstring says so for links too: "Deduplicated workouts are not re-linked".
   - Consequence for the decided rule: an empty session is stored, its claim is refused, and a re-post of the same workout with sets is skipped. The workout stays empty and unlinked permanently. (Unless the re-post changes external_id, or title and start_time, in which case it becomes a second activity.)

3. **Claim ordering inside the importer.** `import_workouts` flushes the `Activity`, calls `claim_planned_session`, and only then adds the `GymWorkoutDetail` rows from `workout.sets`. Today the claim never sees the sets of the workout being imported. The "refuse when no performed sets" check must therefore look at `workout.sets` (the input), or the loop must be reordered. Checking the DB for sets at claim time would wrongly refuse every claim.

4. **The Garmin merge cannot supply sets.** `sources/merger.py::merge_garmin_hevy` copies only HR, calories, HR zones, training effect, `garmin_activity_id` and (if missing) duration onto the gym-source activity, sets `data_source="merged"`, and deletes the Garmin row. It never reads or writes `GymWorkoutDetail`. Garmin activities (`sources/garmin/mappers.py`) are created with no sets at all. The merge also never touches `PlannedSession`.
   - The merge is currently disabled anyway. Every call site (`/import/workouts`, `/import/hevy`, `/sync/garmin`, scheduler Garmin sync) uses `merge_result = MergeResult()` with a comment referencing #109/#116/#110, and `POST /merge` returns 503.

5. **Later imports and scheduled jobs.**
   - Hevy CSV: manual upload only (`/import/hevy`), same `import_workouts` path, same dedup.
   - Logger: pushes via `/import/workouts` when back online, same path. This is the "offline phone" case: the whole batch, sets included, arrives together, so an offline logger does not produce a claim-then-sets gap.
   - Garmin sync (`scheduler/jobs.py`, lookback window): `_activity_exists` skips known activities; it imports activity rows only, no sets.
   - No reconciliation job exists that revisits stored activities and planned sessions.

6. **`claim_planned_session` has one caller.** Only `import_workouts` calls it, once per newly created workout carrying a `planned_session_id`. It has no scheduler, event, or merge hook.

7. **A second linking path exists and is separate.** `coaching/engine.py::generate_post_workout_analysis` finds a plan match by `find_matching_planned_session` (by activity date and sport, not by id) and calls `plan_linking.link_activity_to_planned_session`. That function has no "performed sets" check and no refusal. It runs from `/activities/{id}/analyze` and from `job_post_workout_analysis`, which considers activities from the last 2 days that lack a post-workout insight. So an activity whose claim was refused for empty sets can still be linked to a `PlannedSession` (and marked `completed`) by post-workout analysis. If the "empty session cannot claim" rule is meant to be global, this path bypasses it.
   - The analysis job also never re-runs once an insight exists (`PipelineSkip` unless `force`), so it is not a general retry hook either.

## Implications for planning

- Retry on late sets is not needed by any current flow, because no flow adds sets late. Recording the refusal in `ImportResult.errors` (as other declined claims are) is sufficient today.
- Two follow-ups worth deciding (not done here):
  - Whether the empty-sets rule should also apply to `link_activity_to_planned_session` (finding 7).
  - Whether re-posting an already-imported empty workout should be able to fill in its sets. This would be a deliberate new behaviour (an upsert path in `import_workouts`), and it is the only realistic future source of late sets. If added, it becomes the natural place to re-run the claim, with the same `planned_session_id` the client resends.
- If a future feature adds sets to existing activities (e.g. editing a session in the web UI, or the canonical-activity migration #109 reintroducing merging), the claim retry should be triggered there. Re-running `claim_planned_session` is already safe and idempotent for the same activity id: it returns `None` when `planned.activity_id` is unset or equal to the activity.
