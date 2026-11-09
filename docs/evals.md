# Prompt experiments

Before a prompt change is promoted (by moving the `production` label in
Langfuse), run it against a dataset of real coaching inputs and compare the
run with a `production` run of the same dataset. There are two ways to run
one, and both use the same datasets:

- the harness, `python -m mycoach.evals run`, which calls the LLM through
  MyCoach's own client and response parser and scores each item with the
  production code checks;
- a prompt experiment in the Langfuse UI, through an LLM connection set up
  once in the `mycoach` project.

## Datasets

### Item input is the prompt's variable map

A dataset item's `input` is exactly the variable map of the prompt it is for:
a JSON object with one string per variable. The prompt may leave some of those
variables out (an experiment dropping context is fine), but it must not use a
name the map lacks. This is what each coaching generation records as its
input, so **Add to dataset** on a generation in a trace gives an item that
works in both the harness and the UI. Add it from the generation (named after
the operation, e.g. `gym_adjustment`), not from the trace's root span.

Put nothing else in `input`. Notes about an item go in its `metadata`, and a
reference answer, if any, in `expected_output`. The harness checks every item
against the prompt before its first LLM call and stops if one does not fit.

### Names

Name a dataset after its prompt, without or with the `mycoach/` prefix,
then optionally `-` or `/` and a suffix: `gym-adjustment-golden`,
`daily-briefing`, `mycoach/weekly-recap/edge-cases`. The harness reads the
operation from the name. The prompts are `daily-briefing`, `gym-adjustment`,
`cardio-plan`, `post-workout` and `weekly-recap`.

### Golden datasets stay small

Keep a golden dataset at or under about 30 items. A UI experiment runs every
item of the dataset and has no cap, and each item spends the free provider's
daily quota, which daily coaching needs too. The harness refuses to run more
than 30 items at a time (`LIMIT_CAP` in `src/mycoach/evals/cli.py`). Archive
items you no longer want rather than letting a dataset grow; archived items
are skipped.

## The harness

```sh
docker compose exec mycoach python -m mycoach.evals run \
  --dataset gym-adjustment-golden --label staging --limit 10
```

- `--dataset`: the dataset to run.
- `--label`: the prompt label to run, e.g. `staging`. Run `--label production`
  on the same dataset to get the baseline to compare against.
- `--limit`: how many active items to run, 1 to 30. It is required. Each item
  costs one LLM call, or two when the first answer doesn't parse.

It needs the app's settings: the three `LANGFUSE_*` variables and the LLM
provider's settings. **It refuses to run when `MYCOACH_LLM_PROVIDER` is
`claude_code`**, so an experiment can never use up the Claude Pro window that
daily coaching relies on. Run it with the free provider configured instead.
**It also refuses a provider billed per call (`anthropic`) unless you pass
`--allow-paid`**, so an experiment never spends money by accident.
`--allow-paid` never lifts the `claude_code` refusal.

For each item it compiles the labelled prompt with the item's variables, using
the same strict compile production uses, then calls the LLM with the
operation's production model tier and token budget, parses the answer with
the operation's response model and retries once on a parse failure, as
production does. Each item's trace holds the generations, linked to the prompt
version, and these scores:

| Score | Meaning |
|---|---|
| `parse_ok` | the answer parsed (after the retry, if there was one) |
| `needed_retry` | the first answer did not parse |
| `truncated` | an answer hit the token limit |
| `fallback_used` | `gym-adjustment` only: production would have kept the routine |

These are the code checks production scores coaching runs with, so a run
compares directly with a `production` run in the dataset's runs view. The
run is named `<label>-v<version>-<UTC time>`. A provider error still scores
every check: `parse_ok`, `needed_retry` and `truncated` are 0, and
`fallback_used` is 1. The command prints a summary and exits
non-zero if any item failed.

## Langfuse UI experiments

### One-time setup: the LLM connection

UI experiments call the model through an LLM connection in the `mycoach`
Langfuse project. The key lives in Langfuse, never in MyCoach.

1. In Langfuse, open the `mycoach` project, then **Settings → LLM
   Connections → Add LLM connection**.
2. Provider (adapter): **OpenAI**. Every free provider MyCoach considers
   (#165) has an OpenAI-compatible endpoint.
3. API key: the free provider's key.
4. Turn on a custom base URL and enter the provider's OpenAI-compatible
   endpoint, e.g. for Workers AI
   `https://api.cloudflare.com/client/v4/accounts/<account id>/ai/v1`.
5. Custom models: add the model ID MyCoach's free provider uses, so it can be
   picked in the playground and in experiments.
6. Save, then try the connection once in the playground with a short prompt.

Claude Code can't be a UI connection: it is a CLI on the Pro subscription,
not an API.

### Running one

1. Open the dataset, then **Run experiment** (prompt experiment).
2. Pick the prompt (e.g. `mycoach/gym-adjustment`) and the version labelled
   `staging`, the LLM connection and its model.
3. Leave the model parameters as production's: `max_tokens` 8192 for
   `gym-adjustment`, `cardio-plan` and `weekly-recap`, 4096 otherwise.
4. Run it, then run the `production` version the same way to compare.

Langfuse fills the prompt's variables from each item's input, which is why
the input must be the variable map. A UI run records the raw answers only:
MyCoach's parser and code checks don't run, so use the harness for
`parse_ok`, `needed_retry` and `truncated`.
