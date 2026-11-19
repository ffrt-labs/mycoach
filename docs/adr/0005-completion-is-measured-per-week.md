# Completion is measured per Mon–Sun week, and the planned day is only a soft preference

The weekly recap for 2026-11-02 reported 2/5 sessions ("40% on paper") although both planned gym sessions were done, only on other days (#214). Matching was already week-scoped by sport, but Logger activities with no `planned_session_id` were kept out of the pool (#146), so a squat logged on Wednesday without picking the plan never filled Monday's squat slot.

A Planned session is complete if a qualifying activity of its sport happens anywhere in its Mon–Sun week. The day it was planned for is a soft preference. An activity on a session's own day goes to that session first, and whether a session was done on its planned day is reported for information only ("on planned day: 2/4"). It never lowers completion and is never described as "moved" or "missed".

Unlinked Logger gym sessions with performed sets count toward Completion, in the same pool as Garmin and Hevy. #146's reasoning ("didn't link = chose not to follow") still holds for prescription fidelity, not for whether the session was done. Adherence scores (set completion, weight, reps and RPE deltas) still need an explicit Prescription fulfillment, because without the link there is no prescription to compare against.

## Consequences

- `get_plan_adherence_for_week` reports `on_planned_day` and `actual_day` for each session. Both are `None` when no activity completed the session (it was missed, or ticked by hand).
- The claim guard (#129) is unchanged: a gym activity with no performed sets never completes a session.
- Post-workout matching (`find_matching_planned_session`) still gives an unlinked Logger activity no day-and-sport guess. That decides which prescription an activity answers, which is a question of fidelity.
- The briefing, dashboard and plan pages still look a session up by day. That assumption is reversed by the "open this week" ticket (#218), not here.
