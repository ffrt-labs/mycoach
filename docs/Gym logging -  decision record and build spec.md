# Gym logging: decision record and build spec

Status: agreed
Date: 2026-08-24
Scope: gym workout logging for MyCoach, as part of the wider health hub

## 1. Decision

No third-party gym tracker is adopted. The existing `/logger` PWA inside MyCoach stays,
and gets a targeted set of UX fixes.

MyCoach owns the full loop: it generates the plan, the logger reads it from the same
database, and the actuals land back in the same database. There is no second system,
no API contract between systems, and no schema translation.

## 2. Why the alternatives lost

Fourteen or so candidates were reviewed. Three reached a serious shortlist.

| App | Why it lost |
|---|---|
| **wger** | Best integration surface in the field: full two-way REST API, official MCP server and API client, and a log model that stores prescribed next to performed. Its native Flutter app is an online REST client. Offline write queueing is an open ticket from 2021 (`wger-project/flutter#6`). The gym is not on the LAN, and running a VPN on the phone was rejected. |
| **openGym** | Best gym-floor UX in the field and imports Hevy CSV including RPE. Rejected as a dependency: one maintainer, roughly one year old, and the GitHub account was suspended so the repo now lives on Gitea and GitLab mirrors. Its synced flavor is also a PWA; the native APK has no server sync at all, so its two flavors cannot be combined. |
| **Workout Cool** | MIT, Postgres, ~7.1k stars, but a plan-builder first. Per-set RPE logging and any API were unverified. |
| **Liftosaur** | AGPL but not self-hostable. The author states the server is tied to the liftosaur.com domain. |
| **LiftLog** | Device-local, no server database. |
| **Hevy** | Cannot accept a pushed plan at any price. Logging and CSV export are free; only the API is behind Pro. |

The deciding constraint was offline-first. No app in the field is offline-capable,
self-hosted, and able to receive a prescribed session. Given that, building is cheaper
than adopting.

## 3. Architecture

`/logger` stays server-rendered with HTMX everywhere **except** the active-session view.

The active-session view becomes a self-contained client-side island: it holds its own
state (focus, current set, rest timer, dirty values), writes to IndexedDB, and syncs on
completion. This is the only screen that is genuinely an offline stateful app, and the
only one touched mid-workout. Drawing the boundary here avoids a rewrite.

Data flow:

```
gym_adjustment (LLM) -> PlannedSession -> /logger active session
                                              |
                                              v
                                       GymWorkoutDetail -> post_workout (LLM) -> Telegram
```

## 4. Data model

- **Exercise identity.** Adopt stable IDs from the `hasaneyldrm/exercises-dataset`
  open dataset. `exercise_title` becomes a display label, not a join key. Verify the
  dataset's license directly rather than trusting a downstream README.
- **Effort.** `GymWorkoutDetail.rpe` stays the canonical column. The UI asks RIR
  ("how many more reps could you have done?") with 0 / 1 / 2 / 3+ buttons, on the top
  set only. Convert at write time: `rpe = 10 - rir`.
- **Prescribed vs performed.** Each logged set stores the prescribed weight and reps
  alongside the performed values, so `post_workout` gets the comparison without
  heuristic matching.
- **Supersets.** `superset_id` already exists and carries ad-hoc pairing made on the
  gym floor. Plan-declared pairing is deferred.
- **History.** The existing Hevy history is disposable. This is what makes the
  exercise-ID migration free to do now, and it will not be free later.

## 5. Build list

In order. Item 5 is five lines and should go first anyway.

1. **Active-session island.** Inline inputs, no modal. Adding a set shows the input
   directly, the way Hevy does.
2. **Prefill.** MyCoach's prescribed weight and reps are the editable default value.
   Last session's actuals render in grey underneath as a reference row. The coach's
   decision is the default action; the grey line gives context to override it.
3. **Ad-hoc superset pairing** during a session.
4. **RIR prompt** on the top set of each exercise.
5. **Wake Lock.** Request on session start, release on finish.

## 6. Boundaries

- **MyCoach owns progressive overload.** No rule engine runs underneath the LLM.
  Two systems prescribing weights is how you end up debugging why the app said 82.5
  and the email said 80.
- **Telegram is outbound only.** Session finishes, n8n notices, MyCoach runs
  `post_workout`, the verdict arrives as a message. Uses the existing long-polling bot.
  No logging by chat.
- **openGym: ideas and data, not code.** It is AGPL-3.0 and MyCoach is all-rights-
  reserved. Its training logic lives in tested pure functions and is worth reading for
  the 1RM and warm-up edge cases. Read it, then write your own.

## 7. Acceptance

Done means one full mesocycle in which:

- MyCoach generates Sunday's plan
- it appears on the phone
- the session is lifted from it
- actuals land back in `GymWorkoutDetail` with no manual step
- no notes app is opened and no set is skipped

**Hevy veto, week two.** If it feels worse than Hevy did, the problem is the
architecture in build item 1, not a missing feature. More features will not rescue it.

## 8. Deliberately deferred

- Plan-declared supersets: needs `RoutineExercise` grouping, a migration, and a change
  to what the LLM emits.
- Warm-up set marking. Not discussed during the session, but its absence silently
  corrupts estimated 1RM, which the coaching prompts read. Next block.
- Whether `PlannedSession.details` stays free text or becomes structured. The prefill
  work in item 2 will force an answer.

## 9. References

- openGym mirrors: `gitea.com/DuarteSantos/openGym`, `gitlab.com/DuarteSantos8/opengym`,
  site and docs at `opengym.duarte-santos.ch`
- `alexpcosta/opengym` is a GitHub fork adding an optional AI coach that designs and
  revises plans from logged data, on your own server under your own provider account.
  Worth reading to gauge how invasive that integration is.
- wger routine and log API: `wger.readthedocs.io/en/latest/api/routines.html`
- wger offline ticket: `github.com/wger-project/flutter/issues/6`
