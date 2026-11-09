# Langfuse is the source of truth for coaching prompts, and optional at runtime

The five coaching prompts (`mycoach/daily-briefing`, `mycoach/gym-adjustment`, `mycoach/cardio-plan`, `mycoach/post-workout`, `mycoach/weekly-recap`) live in Langfuse as self-contained chat prompts: a system and a user message, with the shared system prompt copied into each one rather than referenced, so one prompt version fully determines what the model saw. MyCoach serves the `production` label. `staging` holds candidates, and promotion or rollback is a manual label move in the Langfuse UI, with no deploy and no gate. Langfuse is also where coaching traces and Langfuse scores go (#172, #175).

Langfuse stays optional. Without the three `LANGFUSE_*` variables, or with Langfuse down and nothing cached, MyCoach serves the bundled `prompts/v2` files through the same compile step as before (`compile_prompt`), so behaviour is unchanged. The bundled files are the fallback and the seed (`python -m mycoach.tools.seed_langfuse_prompts` creates version 1 of each prompt from them and skips any prompt that already exists). They are not an error path: a coaching run always gets a prompt.

## Serving rules

- Each prompt is cached in memory for `LANGFUSE_PROMPT_CACHE_TTL` seconds (default 300). A failed refresh keeps the cached version.
- A fetch is a single attempt with a 2-second timeout. After a failure it isn't retried for 60 seconds. The SDK's default of 3 attempts at 5 seconds each, with no negative cache, would add about 15 seconds to every coaching call while Langfuse was down.
- If Langfuse returns 404 (for example, because the `production` label was removed), the cached copy is dropped and the bundled prompt is served. Unlike an outage, a 404 is a deliberate change.
- The compile is strict, which the SDK's isn't: the prompt must be exactly `[system, user]`, and every `{{var}}` name it uses must be in the operation's variable map. A version may leave variables out (an experiment dropping context), which is logged at info level and served. A version that fails these checks is ignored with a warning, and the bundled prompt is served. Otherwise schema drift between code and a Langfuse version would silently send a literal `{{name}}` to the model.

## Recording

Each generation is linked to the prompt's name and version and carries `prompt_source: langfuse | bundled`. Artifacts outlive the 90-day trace retention, so they record their own provenance as `lf:<n>` or `bundled:v2`: `CoachingInsight.prompt_version`, and `WeeklyPlan.gym_prompt_version` and `cardio_prompt_version`. `WeeklyPlan.prompt_version` keeps the bare template version.

## Consequences

- An edit in the Langfuse UI reaches production within one TTL, with no review. That's acceptable for one user and one operator. The trace link and the artifact's version say which text produced what.
- A Langfuse version can only rephrase. It can't add a variable, because the code builds the variable map. Adding one means a code change and a new bundled version.
- The bundled files and the seeded version 1 must stay byte-identical once compiled. A test compiles both for every golden case and compares the results.
