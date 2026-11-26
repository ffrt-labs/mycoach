# Spec: Action-first coaching emails with structured LLM output

**Status:** Draft 2026-11-19
**Design:** [MyCoach email redesign canvas](https://claude.ai/artifact/8VHy8nVNu22xyeGegCzpLv): 5 email artboards, a foundations board, a status-component sheet and the schema contract
**Builds on:** PR #223 (#214–#221), ADR 0005 (completion per week), the CONTEXT.md glossary
**Decided:** an illness can be logged for past days (backdated), so the recap can ask what happened (see "Backdated illness")

## Artboards

Exported from the canvas into `assets/2026-11-19-email-redesign/`. Each artboard has:

- a PNG at 2x
- a static `.html` render that opens in any browser
- the canvas source in `source/` (`*.dc.html` + `canvas.json`, only editable on the canvas)

| Artboard | Image | Static HTML |
|---|---|---|
| Foundations: principles, spacing, components | [png](assets/2026-11-19-email-redesign/00-foundations.png) | [html](assets/2026-11-19-email-redesign/00-foundations.html) |
| Daily briefing | [png](assets/2026-11-19-email-redesign/01-daily-briefing.png) | [html](assets/2026-11-19-email-redesign/01-daily-briefing.html) |
| Post-workout | [png](assets/2026-11-19-email-redesign/02-post-workout.png) | [html](assets/2026-11-19-email-redesign/02-post-workout.html) |
| Weekly plan | [png](assets/2026-11-19-email-redesign/03-weekly-plan.png) | [html](assets/2026-11-19-email-redesign/03-weekly-plan.html) |
| Weekly recap: completion first | [png](assets/2026-11-19-email-redesign/04-weekly-recap.png) | [html](assets/2026-11-19-email-redesign/04-weekly-recap.html) |
| Weekly recap: nothing logged (cause quick replies) | [png](assets/2026-11-19-email-redesign/05-weekly-recap-nothing-logged.png) | [html](assets/2026-11-19-email-redesign/05-weekly-recap-nothing-logged.html) |
| Life context & status components | [png](assets/2026-11-19-email-redesign/06-status-components.png) | [html](assets/2026-11-19-email-redesign/06-status-components.html) |
| Structured-output contract | [png](assets/2026-11-19-email-redesign/07-structured-output-contract.png) | [html](assets/2026-11-19-email-redesign/07-structured-output-contract.html) |

The mockups use flex and grid to show the design. The email templates implement them with tables (see "Layout system"). Exported 2026-11-19; the canvas is the source of truth if the two drift.

## Problem

The four coaching emails (daily briefing, post-workout, weekly plan, weekly recap) show what the model wrote as paragraphs. Every LLM field is a free `str`, so the templates can only print text blocks. The important parts are not highlighted, and the reader can't tell what to do.

What the current emails show:

- **The LLM writes numbers.** Pace, zone minutes, HRV, totals. The 2026-11-16 plan email said "3 sessions, ~130 min" for a week of 5 sessions and 270 min.
- **Advice is split up.** It is spread across "Work on", "Next session", "Coach recommendations" and "Next week", with no order.
- **Conditions are buried in prose.** For example: "If HRV <68 ms or sleep score <80 that morning → drop to 6x100".
- **App problems show up as coaching.** Examples: "Fix plan generation for swim/run" and "Check routine: 36 sets".
- **Layout:** the email is 420 px wide. Sections have 20 px above and 2 px below, so the gaps are uneven. Only some sections have a divider line.

PR #223 also added things the emails don't show yet: Completion per week, Session intent, Open this week, Substitution, and Life context episodes (illness, travel, ramp-back).

## Goals

1. You can read an email in about 10 seconds: a verdict, one sentence, then at most 3 actions.
2. **Code renders every observed or derived number, count and status** (pace, HRV, zone minutes, totals, completion). The LLM writes judgments, including prescribed numbers (targets, durations, rest, recovery hours), into typed slots, and each slot has a length limit.
3. Every PR #223 concept is a code-rendered component that uses the glossary's labels.
4. One layout system for all emails: 600 px wide, a fixed spacing scale, and every section separated by a divider line.

## Non-goals

- Redesigning the dashboard. It only gains a "Full analysis" view for `analysis_md`, plus the backdated-illness form fields.
- Changing coaching logic, completion matching or ramp-back maths.
- Changing the emails that aren't coaching (garmin_auth_failed, no_availability, briefing_unavailable). They only get the new base layout.

## Principles

1. **Verdict and actions first.** Everything below the action card is supporting detail.
2. **Observed numbers come from code.** This extends the pattern that #196 (steps), #197 (HRV) and #198 (body) already follow. Prescribed numbers (a set's reps and target, a session's duration, rest seconds, `ready_in_hours`) are Coach notes in typed fields.
3. **Every Coach note has a slot and a limit.** The long write-up goes to `analysis_md`, which only the dashboard shows.
4. **Conditions are Guardrails, not prose.** A Guardrail renders as a `when → then` row.
5. **App problems are not coaching.** Data gaps, failed plans and routine errors render as a Notice with a link to fix them.
6. **Week-scoped, glossary words only.** Completion is per Mon–Sun week. Use "done", "not done", "excused", "substituted", "adapted (travel)" and "ramp-back". Never write "missed", "moved", "late" or "skipped", and never call a Substitution a "swap". An equipment change on a trip (#221) is a "hotel alternative", not a swap.

## Layout system (`email/templates/base.html`)

| Token | Old | New |
|---|---|---|
| Email width | `max-width:420px`, `width="420"` | `width="600"` table, `max-width:600px; width:100%` |
| Side padding | 24 px | 40 px; 20 px at ≤ 480 px (media query) |
| Section padding | 20 / 2 px (top / bottom) | 32 / 32 px; first section after the hero: 0 / 32 |
| Section divider | some sections | `border-top:1px solid #e7e5e4` on every section except the block inside the hero (the first after it, e.g. Do today, or one marked so: the recap's quick replies and Next week), which has none |
| Section label → content | 8–10 px | 16 px (12 px when the content is a table header) |
| List / table row | 5 + 5 px | 12 px between rows, 1 px `#f4f4f5` rule |
| Tile grid gap | — | 8 px |
| Body text | 14 / 1.7 | 15 / 1.6, `#3f3f46` |
| Lead sentence | 15 / 1.55 | 16 / 1.5 |
| H1 verdict | 46 / 30 / 38 px (varies) | 40 / 1.1, 700, −0.02em (the recap's completion line is 44 px mono) |
| Section label | 11 px caps | 12 px, 600, 0.12em, caps, `#71717a` |
| Data values | 17 px mono | 22–24 px IBM Plex Mono 600; units 12–13 px `#71717a` |
| CTA | 15 px padding | 15 px padding, radius 10, full width |

Tones: these are the only colours that carry meaning. The LLM picks a tone label and the template maps it to colours.

| Tone | Background | Text | Use |
|---|---|---|---|
| good | `#ecfdf5` | `#047857` | on target, improved, done |
| neutral | `#f4f4f5` | `#52525b` | context, excused, nothing to do |
| watch | `#fffbeb` | `#b45309` | drifting, not done, adjust |
| alert | `#fef2f2` | `#b91c1c` | act now (sick banner, failed job). Never used for "not done" |

Orange `#ea6d1f` stays the brand accent: the action card, numbered markers, the CTA and the quality-intent chip. Orange text is `#c2410c` (≥ 4.5:1 on white). Keep the existing dark-mode block and extend it with these tokens.

Email-client constraints:

- Keep the table layout of the current `base.html`.
- The canvas mockups use flex and grid only to show the design. Implement grids as `<table>` rows of 2–3 cells.
- Put layout-critical styles (width, padding) inline, as today. Keep classes for colour and dark mode.

## Component catalogue

Each component is a Jinja partial under `email/templates/components/`. Each takes a view dict built in `email/sender.py`; templates do no arithmetic.

| Partial | Source | Input | Used in |
|---|---|---|---|
| `_headline.html` | LLM (tone, summary; verdict only in PW) + code (eyebrow; briefing verdict from `readiness`; recap completion) | `Headline` or `Lead` | all |
| `_actions.html` | LLM | `list[Action]`, max 3, numbered | PW, recap |
| `_options.html` | LLM + code | `list[SessionOption]` resolved against open sessions | briefing |
| `_guardrail.html` | LLM | `Guardrail` (`when`, `then`) | all |
| `_stat_grid.html` | code | tiles: label, value, unit, note, tone | briefing, PW, recap |
| `_zone_bar.html` | code (+ LLM `zone_note`) | zone minutes, % | PW |
| `_trend_table.html` | code | metric × today / last / best | PW |
| `_block_table.html` | LLM | `list[Block]` | PW, plan |
| `_exercise_table.html` | code (routine + gym adjustment) | exercise, sets × reps, RPE, load | plan |
| `_week_strip.html` | code | 7 days: sport, minutes, intent, quality outline, trip marker | plan |
| `_completion.html` | code: `completion_line()` | the week's Completion | recap |
| `_session_status.html` | code (+ LLM `session_notes`) | the week's Completion: `sessions`, `extras` | recap |
| `_open_this_week.html` | code: `get_open_this_week()` | `OpenThisWeek` + the briefing's pick | briefing, PW (compact) |
| `_life_context.html` | code | `LifeContext`, open illness, current trip | briefing, plan, PW, recap |
| `_cause_replies.html` | code | recap week, condition below | recap |
| `_notice.html` | code | kind, text, link | all |

### Status, intent and life context mapping

The labels come from `STATUS_LABEL` / `EXCUSED_LABEL` (`coaching/life_context.py`). Never re-type them in templates.

| Session status | Chip tone | Row detail |
|---|---|---|
| `done` | good | when `on_planned_day` is false: "Done {actual_day}" in muted text |
| `substituted` | good | "← {substitute.title} {day} · {duration} min · avg HR {avg_hr} · aerobic TE {te}", then the LLM `session_notes` line. `SubstituteActivity` gains `day` (it has none today) |
| `adapted_travel` | blue (`#eff6ff` / `#1d4ed8`) | substitute line as above |
| `excused_ill`, `excused_travel` | neutral | title struck through; no action asked |
| `not_done_travelling` | neutral, outlined | — |
| `not_done` | watch | — |
| extra | outlined muted, in the "Extras · training load only, not counted" list | — |

| Session intent | Chip |
|---|---|
| `strength` | outline `#fed7aa` / `#9a3412` |
| `aerobic_base` | outline `#bfdbfe` / `#1d4ed8` |
| `threshold`, `intervals` | solid `#ea6d1f` / white. These are the quality intents; their day is outlined on the week strip |
| `recovery` | outline `#a7f3d0` / `#047857` |
| `skill` | outline `#ddd6fe` / `#6d28d9` |
| `None` | no chip |

Show intent labels with spaces ("aerobic base"). In the weekly plan, the old "Quality" badge is replaced by the intent chip.

| Life context state | Banner | Link |
|---|---|---|
| open illness, `sick` | alert: "Ill since {started_on} · day {n}" + "Rest today. Nothing to make up — sick days are excused." | Feeling better → dashboard `#life-context` |
| open illness, `mild` | watch: "Mild cold · day {n}" + "Light and short at most, or rest." | Update → |
| ramp-back | good + a progress bar of `length` segments: "Returning from illness · day {day}/{length} · ~{load_pct}% of normal load" | — |
| ramp-back, last day, `extend_suggested` | same + "{signals} still below your range: consider one more easy day" | — |
| travelling | blue: "{equipment label} · {abs(tz)} h {ahead of / behind} home · back {ended_on}" (+ "jet-lag principle applies today" if ≥ 3 h) | Edit trip → |
| travelling, `no_training` | neutral: "No training possible today" + "Rest. Today can excuse one not-done session; nothing to make up." | Back home → |

Reuse the dashboard's wording from `templates/_life_context.html` (for example the `tz_text` macro). If an illness and a trip overlap, show the most restrictive effect only (`RESTRICTIVENESS`).

## Per-email specification

The order of sections below is the order in the email. "Code" means the value comes from the DB or computation, never from the LLM. "The week's Completion" is what `get_plan_adherence_for_week` returns today; the #223 follow-up renames it and its `PlanAdherence`/`SessionAdherence` types to the Completion vocabulary, and this spec uses the new name so it stays right after the rename.

### Daily briefing

1. Header: "Daily briefing · {weekday dd Mon}".
2. **Life context banner** (code), if `LifeContext` isn't empty.
3. **Headline:** the verdict, mapped by code from `readiness` (`go_hard` → "Go hard", …), the LLM's tone chip and summary (`Lead`).
4. **Do today · pick one:** 1–2 `SessionOption`s. When `open_session_id` is set, code renders the title, sport, intent, minutes and planned day ("Open since Mon · gym · strength"). The LLM writes the steps and why. Show the "Recommended" chip on at most one option. Footer line: "Don't stack both." when there are 2 options.
5. **Open this week** (code): "{n} left · {days_remaining} days remaining". One row per `OpenSession` with day, title and "sport · intent · minutes" (`OpenSession` gains `intent`; it has none today). Status chip:
   - The briefing's recommended `open_session_id` → "Today's pick" (orange).
   - `overdue` → "Planned day passed" (watch).
   - `today` → "Today".
   - otherwise the planned weekday.

   Show the Overload notice when `len(sessions) > days_remaining`. Empty state: "Every planned session is done." (good).
6. **Guardrail · tomorrow:** at most 1 `Guardrail`.
7. **Last night** (code): tiles for sleep score, sleep duration and resting HR; a sleep-stage bar; the HRV line (#197); then the LLM's `sleep_note`.
8. **Tonight:** up to 3 `Tip`s (bedtime, screens off, fuel, or a principle the data triggered, such as jet lag).
9. Steps strip (#196) and Body block (#198), unchanged except for spacing.
10. CTA "Open dashboard →". Footer: "Feeling unwell or travelling? Tell the coach →" links to `{dashboard}#life-context`.

`send_daily_briefing` gains: `open_this_week: OpenThisWeek | None`, `life: LifeContext | None`, `open_illness`, `current_trip`. Build them in `_code_rendered_numbers()` the same way `api/pages/dashboard.py` does. Best-effort like the other numbers: `None` leaves the block out.

### Post-workout

1. **Headline:** eyebrow "{sport} · {activity title}", verdict + tone, summary.
   - Below it (code): the activity's status in its week, from `get_plan_adherence_for_week` computed at send time:
     - completes a session (Prescription fulfillment or same-sport match): "[Done] Completes Tue · Aerobic base swim + technique [aerobic base]"
     - in a session's `substitute`: "[Substituted] Substitutes Mon · Squat Day"; on a Travel day: "[Adapted (travel)] …"
     - in `extras`: "Extra · training load only, not counted"
     - no plan this week: omit the line.
   - Life context chip next to the verdict on a ramp-back or travel day ("Ramp-back day 2/5", "Travelling · hotel gym").
2. **Stat grid** (code), 3 × 2. Swim: distance (of planned), moving time (of planned), pace /100 m (Δ vs last), SWOLF (Δ), strokes/length (Δ), training effect + load. Run: distance, time, pace, avg HR, cadence, TE. Gym: sets done/planned, top set, total volume, avg RPE, duration, TE. "/planned" only with a Prescription fulfillment; TE only from Garmin. A tile with no value is left out, never shown as "—" (the same applies to every sport).
3. **Next time:** 1–3 `Action`s (orange card).
4. **Heart-rate zones** (code bar + legend in minutes) + the LLM `zone_note`. Show the target label from the planned session's intent: aerobic_base → "Target: Z2".
5. **Trend · last 6** (code): today / last / best per metric. Then the LLM `trend_note`.
6. **Next session · repeat:** `_block_table` from `next_session` + up to 2 `Guardrail`s + the `week_addition` line ("+1").
7. **Recovery:** a good chip "Ready again in < {ready_in_hours} h" + `recovery`.
8. **Still open this week** (code, compact chips).
9. CTA "Full analysis →".

`send_post_workout(content, activity, settings)` must receive the activity dict, the similar-activity history (the prompt builder already loads the last 5 of the sport), the fulfilled `PlannedSession` and `get_open_this_week()`.

### Weekly plan

1. Hero: "{n} sessions" + "{total h min}", both computed from `PlannedSession`s. Then the LLM's `focus`.
2. **Week strip** (code): 7 cells with sport, minutes and intent. Quality intents are outlined orange. Travel days are marked from episodes in the week.
3. **Notices:**
   - life context (a trip or an open illness touching the week, e.g. "Trip Wed–Fri · hotel gym · 6 h ahead"). Facts only: any reasoning about how the plan handles the trip goes in the LLM's `focus` or `guardrails`
   - default schedule (the existing text)
   - routine error (code check: any routine set count > 10, as bounded in #187: "Friday lists Deadlift at 36 sets. Planned as 3. Fix routine →")
4. **Guardrails this week:** up to 3 `Guardrail`s.
5. **Day cards:**
   - header: day + title + intent chip + sport chip + minutes
   - `focus` line on a light orange background
   - gym: `_exercise_table` (from routine + gym adjustment; Load column "log" until a target exists)
   - cardio: `_block_table` from `blocks`
   - `foot` line
6. CTA "Open this week →".

`jobs._weekly_plan` must add `"intent": s.intent` to `session_dicts` (it is currently dropped) and pass `life_context_for(next_monday, next_monday + 6)`.

### Weekly recap

1. Hero: eyebrow "Week of {d Mon} · completion".
   - H1 is the first segment of `completion_line()` ("4/5 done") in 44 px mono. The rest of the line goes beside it ("· 1 substituted · 3 excused (ill 3 days)").
   - Then the LLM's `headline.summary`.
   - Under it, in muted 12 px: "On planned day {x}/{y} · informational". Count only sessions where `on_planned_day` is not None.
   - When every session is excused (`total_sessions == 0`, `excused_sessions > 0`), the H1 is "{n} excused" and the reason goes beside it ("ill 5 days"), never "0/0 done". `completion_line()` itself is unchanged.
   - With no plan, `completion_line()` is None: show "No plan this week" instead.
2. **Cause quick replies** (code), when shown (see the condition below).
3. **Next week:** 1–3 `Action`s + at most 1 `Guardrail`.
4. **Sessions** (code): one `_session_status` row per planned session, in planned-day order, with the LLM's `session_notes` joined by `session_id`. Then the Extras list.
5. **Life context** (code): episodes touching the week ("Ill 11–13 Nov · sick · ramp-back to 18 Nov"; "Trip 9–12 Nov · hotel gym"). If there are none: "No illness or travel logged this week. Something going on? Log it →" (prefill link for the week).
6. **Recovery** (code tiles, data range shown) + the LLM's `recovery_note`.
7. **Strength** (code: top set per catalogue exercise this week) + the LLM's `lift_notes` (trend text + tone).
8. **Load focus** (code values + LLM `load_note`). Only show a bar if a target range exists in the data; otherwise show values only.
9. **Setup & system** notices: no mesocycle → link to `/mesocycles`; failed plan generation (job runs) → link to the job log.
10. CTA "Full recap →".

Never put "missed", "moved" or a second completion figure in code-rendered text. The prompt rules from #217 still apply to the LLM's text.

#### Cause quick replies

Show the block when all of these are true for the recapped week:

- `completed_sessions < total_sessions`
- no Life context episode touches the week
- at least one of: `completed_sessions == 0`, or no health snapshot for ≥ 2 days of the week

Buttons. Every link is a GET that only prefills a form; nothing changes until the user submits it, because mail scanners prefetch links.

| Button | Link |
|---|---|
| I was ill | `{base}/?log=illness&from={first}&to={min(last, today-1)}#life-context` |
| I was travelling | `{base}/?log=travel&from={first}&to={last}#life-context` |
| Watch didn't sync | `{base}/` (the Garmin sync button). Show only when health data is missing for ≥ 2 days. When the last sync failed with an auth error, the label is "Reconnect Garmin" (decided by code at render time). The link never triggers a sync or re-login itself |

`first` and `last` are the planned days of the first and last `not_done` session in the week: the narrowest range that explains the gap, so accepting the default doesn't excuse sessions or start a ramp-back the user didn't mean. The user can widen it in the form.

Sub-copy: "Opens the dashboard with the dates filled in. Sick days are excused and leave the count; next week's plan reads it."

## Backdated illness (backend change)

Decided 2026-11-19. Travel already accepts `started_on`/`ended_on`; illness doesn't.

- **`IllnessCreate`** gains `started_on: date | None` and `ended_on: date | None`. Validation:
  - `started_on ≤ today`
  - `ended_on ≥ started_on`
  - `ended_on ≤ today − 1` (a past illness is closed; today's illness stays open)
- **`life_context.open_illness`** takes `started_on` (default today) and `ended_on`.
  - `ended_on is None` → an open illness, under the existing one-open-illness rule (409). A past `started_on` with no `ended_on` is allowed ("ill since Thursday, still ill").
  - `ended_on` = yesterday matches what "Feeling better" (`close()`) already stores.
  - Reject an illness that overlaps another illness episode with 409 "An illness already covers {dates}", so "Ill since" stays unambiguous. Illnesses that only touch (one ends the 12th, the next starts the 13th) are allowed and are not merged. Overlap with travel is allowed (sick wins).
- **Ramp-back:** no change. `ramp_back_on()` already derives it from `ended_on` and `length_days`. A backdated illness that ended long ago yields no ramp-back.
- **Completion:** `get_plan_adherence_for_week` computes on read, so the plan page, dashboard and Logger update right away. The recap already sent is not regenerated or re-sent. The next weekly plan reads the episode through `life_context_for`.
- **Known limit:** the Langfuse completion score for that week (#221) was sent at recap time and isn't updated. Note it in the API docstring; no backfill.
- **Dashboard (`templates/_life_context.html`):**
  - The "Feeling unwell" form gets a collapsed "It was earlier" control with two date inputs.
  - With `?log=illness&from&to`, open the `<details>` with the dates filled in, scroll to `#life-context`, and require a click on "Tell the coach".
  - `?log=travel` does the same for the travelling form.
  - Show a one-line confirmation after saving: "Logged ill 9–15 Nov · 3 sessions excused" (a count from the recomputed Completion).

Tests (extend `tests/test_api/test_life_context.py`, `tests/test_coaching/test_life_context.py`, `tests/test_weekly_recap/test_excused_ill.py`):

- backdated closed illness → those days excuse sessions in that week
- `ended_on ≥ today` → 422
- overlapping illness → 409
- backdated illness with `ended_on` = yesterday → ramp-back day 1 today
- the GET prefill renders the form open and changes nothing

## Structured output (prompt v5)

Put the schemas in `coaching/response_parser.py` (or a new `coaching/email_schemas.py`), next to the v4 models. Keep the v4 models and parser until stored v4 insights age out (`prompt_version` on `CoachingInsight`).

```python
Tone = Literal["good", "neutral", "watch", "alert"]
Short = Annotated[str, Field(max_length=40)]

class Lead(BaseModel):                            # briefing, recap: code supplies the verdict
    tone: Tone
    summary: str = Field(max_length=140)          # one sentence

class Headline(Lead):                             # post-workout only
    verdict: str = Field(max_length=28)

class Action(BaseModel):
    do: str = Field(max_length=70)                # imperative, starts with a verb
    why: str | None = Field(None, max_length=90)

class Guardrail(BaseModel):
    when: str = Field(max_length=60)
    then: str = Field(max_length=60)

class Tip(BaseModel):
    tone: Tone
    text: str = Field(max_length=90)

class Block(BaseModel):
    phase: Literal["warmup", "drill", "main", "pull", "kick", "strides", "cooldown"]
    set: str = Field(max_length=24)               # "5×200"
    cue: str | None = Field(None, max_length=48)
    target: str | None = Field(None, max_length=24)
    rest_s: int | None = None

# Daily briefing
class SessionOption(BaseModel):
    open_session_id: int | None = None            # an OpenSession.id from the prompt
    title: str | None = Field(None, max_length=32)  # only when open_session_id is None
    steps: list[Short] = Field(min_length=1, max_length=3)
    why: str = Field(max_length=70)
    recommended: bool

class DailyBriefingEmail(BaseModel):
    readiness: Literal["go_hard", "moderate", "active_recovery", "rest"]
    headline: Lead
    options: list[SessionOption] = Field(min_length=1, max_length=2)
    guardrail: Guardrail | None = None
    sleep_note: str = Field(max_length=60)
    tonight: list[Tip] = Field(max_length=3)
    key_metrics: DailyBriefingKeyMetrics          # kept for the prompt log; email renders from DB
    analysis_md: str

# Post-workout
class PostWorkoutEmail(BaseModel):
    headline: Headline
    actions: list[Action] = Field(min_length=1, max_length=3)
    zone_note: Tip | None = None
    trend_note: str = Field(max_length=100)
    next_session: list[Block] = Field(max_length=6)
    guardrails: list[Guardrail] = Field(max_length=2)
    week_addition: str | None = Field(None, max_length=60)
    ready_in_hours: int = Field(ge=0, le=96)
    recovery: str = Field(max_length=70)
    analysis_md: str

# Weekly plan (cardio plan output; gym rows stay GymAdjustmentResponse)
class CardioSessionV5(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    sport: str
    intent: SessionIntent | None = None           # keep #215's unknown-intent → None validator
    title: str = Field(max_length=40)
    duration_minutes: int
    focus: str = Field(max_length=100)
    blocks: list[Block] = Field(min_length=1, max_length=6)   # replaces details["description"]
    foot: str | None = Field(None, max_length=100)

class CardioPlanV5(BaseModel):
    sessions: list[CardioSessionV5] = Field(min_length=1)
    focus: str = Field(max_length=120)            # the plan email's lead sentence
    guardrails: list[Guardrail] = Field(max_length=3)
    goal_assessment: str                          # dashboard only

# Weekly recap
class SessionNote(BaseModel):
    session_id: int                               # PlannedSession.id from the Completion block
    text: str = Field(max_length=80)

class LiftNote(BaseModel):
    exercise_id: str                              # Exercise ID; custom exercises get none
    tone: Tone
    trend: str = Field(max_length=24)             # "flat 3 weeks"

class WeeklyRecapEmail(BaseModel):
    headline: Lead                                # code shows completion as the verdict
    actions: list[Action] = Field(min_length=1, max_length=3)
    guardrail: Guardrail | None = None
    session_notes: list[SessionNote] = Field(max_length=5)
    recovery_note: str = Field(max_length=100)
    lift_notes: list[LiftNote] = Field(max_length=6)
    load_note: str = Field(max_length=100)
    block_suggestion: str | None = Field(None, max_length=110)
    analysis_md: str
```

Contract rules:

- **IDs must exist.** Code drops an `open_session_id`, `session_id` or `exercise_id` that isn't in the prompt's data, and logs a warning (same pattern as #215's intent validator). An option left with neither an id nor a title is dropped. If no options are left, render the briefing without the options card.
- **The prompt must list the IDs.** v5 prompts show `id` on each Open This Week row, each session in the Completion block and each catalogue exercise. Add to the system text: "Never write an observed or derived number, count or status that the context does not contain; never compute a completion figure. Prescribed targets go only in their typed fields."
- **Provider.**
  - OpenRouter: send `response_format={"type": "json_schema", "json_schema": {"name": ..., "strict": true, "schema": ...}}` for models whose `supported_parameters` include structured outputs. Strict schemas need every property listed in `required` (optional fields as `anyOf [T, null]`) and `additionalProperties: false`. Generate this from the Pydantic model with a small helper; don't use `model_json_schema()` as is.
  - Not every model enforces `maxLength`, so Pydantic stays the gate.
  - `claude_code` provider: keep embedding the schema in the prompt.
- **Retry and fallback.** On `ValidationError`, retry once with the error text appended (the existing `needed_retry` code check). If it still fails, send the email with the code-rendered blocks only, plus a neutral Notice "Coach notes unavailable today", and record a new `notes_fallback` code check (`fallback_used` stays scoped to gym slots, per CONTEXT.md).
- **`analysis_md`** is stored with the insight and rendered on a new read-only dashboard page `/insights/{id}`, which the "Full analysis →" CTA opens. `{id}` is the exact Insight revision the email rendered. Revisions are immutable, so the link never changes under an email already sent. When a newer revision exists, the page shows "A newer analysis exists →".
- **Prompts** stay in Langfuse (ADR 0003). Bundle them as `prompts/v5/` and seed them the way commit 9b40f93 bundled v4. Update the golden prompts in `tests/test_coaching/golden_prompts/`.

## Implementation phases

Each phase can ship on its own and should be a separate issue (`ready-for-agent` once agreed).

1. **Layout foundation:** new `base.html` tokens, the `components/` partials skeleton, tone/intent/status maps in `sender.py`. Existing content renders in the new layout. Tests: render each template with fixtures; assert 600 px width and no 420.
2. **Code-rendered PR #223 blocks on v4 content:**
   - recap: `_completion` hero + `_session_status` + extras + life context
   - briefing: `_open_this_week` + `_life_context` banner
   - plan: intent chips + week strip + trip notice, with `intent` added to `session_dicts`
   - post-workout: compact open-this-week + fulfilled-session line

   Tests in `tests/test_email/test_sender.py`: one per status label, an excused/adapted completion line, the overdue chip, the overload notice, each banner state.
3. **Backdated illness + cause quick replies:** API, dashboard prefill, recap block + condition. Tests as listed above.
4. **Structured output v5:** schemas, provider `response_format`, retry/fallback, v5 prompts and golden files, the templates switched to slots, `/insights/{id}`. Do the post-workout email first, then briefing, recap and plan. An email is rendered once and never again, so each email template becomes v5-only (its paragraph sections deleted) in the same change that moves that email to v5.
5. **Cleanup (dashboard only):** the dashboard keeps rendering stored v4 insights, keyed on `prompt_version`. Remove that branch once no v4 insight is shown.

## Acceptance criteria

- [ ] Every email is 600 px wide on desktop, with 20 px side padding at ≤ 480 px, and 32 / 32 px section padding with a divider.
- [ ] No observed or derived number written by the LLM appears in a stat tile, table, count, completion line or status chip. Prescribed numbers (block sets and targets, durations, `ready_in_hours`) are the only LLM numbers rendered.
- [ ] Each email shows at most 3 actions, above the fold on a 390 × 844 phone, after the headline (and the banner when present).
- [ ] The recap hero is `completion_line()`. No "missed"/"moved" in code-rendered text. On planned day appears only as informational text.
- [ ] Every session status, intent and life-context state on the status sheet renders with the mapped tone and label.
- [ ] The briefing's options link to open sessions by id. An unknown id is dropped without failing the email.
- [ ] Recap quick replies prefill the dashboard and change nothing on GET. A backdated illness excuses that week's sessions on the plan page.
- [ ] A validation failure after the retry still sends an email, with the code blocks and the "Coach notes unavailable" notice.


## Sample data on the canvas

The briefing, post-workout and plan artboards use the real emails of 15–18 Nov. The open-this-week rows are illustrative: Monday's Squat Day is shown as open. The full recap artboard uses the `weekly_recap_2026_11_02` golden-prompt fixture. The "nothing logged" recap uses the real 9–15 Nov week.
