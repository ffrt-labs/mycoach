# The coaching LLM provider must not train on the health context, so MyCoach pays for OpenRouter instead of using a free tier

Every Coaching context sends Observed health facts (sleep, HRV, workouts, RPE) to the LLM provider. Free tiers fund themselves with that data. Google's terms for the unpaid Gemini API let Google train on the prompts and have humans review them, and they tell users not to send personal information (#183). So a coaching provider must not train on or have humans review prompts, and should keep nothing. This rule ranks above price: Gemini's free tier was rejected even though it costs nothing.

MyCoach uses OpenRouter's pay-as-you-go API, with prepaid credits that stop at zero, at roughly $1–2 a month for one athlete. The rule is enforced in code, not only by an account setting. Every request carries `provider: {zdr: true, data_collection: "deny"}`, a test asserts it, and a request with no compliant endpoint fails rather than routing elsewhere. Several alternatives were considered:

- OpenCode Go is a subsidised subscription intended for coding-agent traffic, so a coaching app risks being flagged as abuse.
- Anthropic's API directly was close on price and terms, but it offers fewer models.
- DeepSeek's own API stores data in China and its policy allows training.

## Consequences

- When the credits run out, the provider raises `LLMQuotaExceeded` and the existing missing-briefing alert reports it. There is no automatic fallback to `claude_code`, which would quietly use up the Claude Pro window again (#165 is superseded). `claude_code` stays selectable by hand.
- The provider is `paid` in MyCoach's billing vocabulary, so evals need `--allow-paid`.
- A cheaper or free provider is acceptable only if its terms meet the same rule. Where a vendor can guarantee the rule only through its own request flags, it gets its own provider class, not a generic OpenAI-compatible one.
- Abuse-flagged content can still be kept by any provider. Only a zero-data-retention agreement rules that out, and none exists at this scale.
