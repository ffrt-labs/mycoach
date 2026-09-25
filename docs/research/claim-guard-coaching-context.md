# Does the claim guard change what the coaching context reads?

Issue #137, under map #132. Sources are the code on `origin/main` (8fcf66e) and the decisions on #133, #134, #135.

## Answer

Yes, the reader needs a change. The guard fixes the input for the readers that trust `PlannedSession.completed`. It does not fix `get_plan_adherence_for_week` (`coaching/context.py:146-220`), which reads `completed` at line 194 and then overrides it.

After the loop's `is_done = s.completed`, line 195 checks `sport_budget[s.sport] > 0` and forces `is_done = True`. `sport_budget` is a count of every `Activity` of that sport in the week. It never looks at the prescription link (`activity_id`), the `completed` flag, or whether the activity has performed sets.

Two consequences:

1. **An empty stored activity still marks a planned session DONE.** Decision #133 stores an empty activity but refuses its claim, so `completed` stays false. But the empty gym activity still adds 1 to `sport_budget["gym"]`, so any unclaimed gym prescription that week reads DONE and counts toward `completed_sessions` and `adherence_pct`. The guard leaves the accidental-empty-session bug (#123) alive in this reader. The weekly recap prompt shows it to the LLM as `DONE` (`prompt_builder.py:786-791`).
2. **Unlink does not undo it.** Decision #133 says unlink clears `activity_id` and `completed` and leaves the activity. The activity is still in `activities`, so the reader still counts it. Unlinking a wrongly claimed session leaves this reader reporting DONE.

The sport-budget logic exists on purpose (commit d8ed523: "a session shifted to another day was still done"). It is the only path that credits Garmin or Hevy activities, which carry no prescription reference. So the fix is not to delete it. The needed change is that the budget counts only activities that can fulfil a prescription (for gym, activities with performed sets), and an activity linked to a specific planned session should be consumed by that session rather than by the sport pool. The exact shape is for the implementation plan (see "New decisions").

## Every reader of `completed` and the fulfillment link

| Reader | Location | Input | Effect of guard | Effect of unlink |
|---|---|---|---|---|
| Weekly-recap adherence | `coaching/context.py:194` (`get_plan_adherence_for_week`) | `completed` OR any same-sport activity in the week | **Misbehaves.** Empty activity still forces DONE (sport budget). | **Misbehaves.** Cleared `completed` is overridden by the surviving activity. |
| Recent plan summaries | `coaching/context.py:858` (`get_recent_plan_summaries`) | `completed` only | Fixed by the guard. | Correct (flag cleared). No caller in `src/`; tests only. |
| Post-workout planned-session dict | `coaching/context.py:314` (`_planned_session_to_dict`, `"completed"`) | `completed` | Fixed. Nothing in `src/` reads the key (`prompt_builder.py:791` reads a different dict), so it is inert. | Correct. |
| Explicit link lookup | `coaching/context.py:299` (`_planned_session_linked_to`, by `activity_id`) | `activity_id` | Guard keeps `activity_id` unset for an empty claim, so no link is found. | After unlink `activity_id` is null, so the lookup falls through to the day and sport fallback (see below). |
| Day/sport fallback | `coaching/context.py:318-390` (`find_matching_planned_session`) | day_of_week + sport | Not affected. Can match an empty or unlinked activity to a prescription and hand it to the post-workout prompt as context. | Unlink makes the fallback the only path, so it can re-match the same session. |
| Post-workout link write | `engine.py:724` → `plan_linking.link_activity_to_planned_session` | writes `completed`, `activity_id` | Covered by #135 (checks DB for sets, quiet no-op). Ordering matters: it runs after `find_matching_planned_session`, so an empty activity still gets a matched planned session in its prompt; the write is just skipped. | An unlinked empty activity re-run through post-workout is re-matched by day/sport but not re-linked, thanks to #135. |
| Plan adherence API | `api/routes/plans.py:178,193` | `completed`, `activity_id` | Fixed. | Correct. |
| Plan page | `api/pages/plan.py:77`, `templates/plan.html:22,112` | `completed` | Fixed. | Correct. |
| Dashboard | `templates/dashboard.html:175` | `completed` | Fixed. | Correct. |
| Logger "This week" | `api/routes/logger.py:186` (`done=ps.completed`) | `completed` | Fixed. | Correct, and this is the surface where the unlink action lands. |
| Manual complete | `api/routes/plans.py:123-149` (PATCH) | writes `completed = True` and optional `activity_id` | **Bypasses the guard.** No sets check, no ownership check on `activity_id`. | A manual complete without `activity_id` has no activity to unlink from, but unlink (clear both fields) still works. |

## Empty stored activities, per reader

- `get_activity_with_details` (`context.py:245-290`): for an empty gym activity returns an empty `gym_details` list. No error. Post-workout will analyse a workout with zero sets, but this is outside the completed/link question and #135 already keeps the insight being produced.
- `get_similar_activities` and the weekly and daily activity lists (`context.py:73, 239, 396, 516, 622`): include the empty activity as a normal session (duration, sport). It shows up as a real workout in trend context. Not a `completed` reader, but the same root cause: "stored but empty" is visible to coaching.
- The no-op cases in #135 and the refusal in #133 both leave `completed=False`, so every reader that trusts the flag (rows marked "Fixed" above) behaves correctly.

## Sources

- `src/mycoach/coaching/context.py` lines 146-220, 245-290, 299, 314, 318-390, 831-870 (origin/main)
- `src/mycoach/plan_linking.py`, `src/mycoach/sources/importer.py:68-110`, `src/mycoach/api/routes/plans.py:123-193`, `src/mycoach/api/routes/logger.py:186`, `src/mycoach/api/pages/plan.py:77`, `src/mycoach/coaching/prompt_builder.py:775-795`, `src/mycoach/coaching/engine.py:646,724`
- `CONTEXT.md`: Prescription fulfillment, Observed health fact, Performed set
- Decisions: #133 (rules, unlink), #134 (no late sets), #135 (second link path), #136 (re-post never fills sets)
- Commit d8ed523 (why adherence matches by sport within the week)

## New decisions surfaced (for the implementation plan, not created as tickets)

1. What should `get_plan_adherence_for_week` count as fulfilling a prescription: only activities with performed sets (gym) plus non-gym activities as before, and should a linked activity be consumed by its own planned session first?
2. Should the manual PATCH `mark_session_completed` obey the same no-performed-sets rule (or be removed in favour of unlink plus claim)?
3. Should `find_matching_planned_session`'s day/sport fallback skip empty activities, so the post-workout prompt is not told an empty session answered a prescription?
