"""Exceptions for the coaching pipeline."""


class PipelineSkip(Exception):  # noqa: N818 - a skip is deliberately not an error
    """Raised when a coaching pipeline step has nothing to do.

    A skip is a deliberate no-op — an insight already exists for the period,
    there are no new activities to analyse, or no availability is configured.
    Every *other* exception raised out of a pipeline step means a real failure.
    Keeping skips in their own type stops genuine corruption (e.g. a malformed
    LLM response, whose ``json.JSONDecodeError`` is a ``ValueError`` subtype)
    from being mistaken for a routine skip.
    """


class NoAvailabilityConfigured(PipelineSkip):
    """Raised when a week has no declared availability and no standing default.

    Distinguished from a plain ``PipelineSkip`` so callers can react to it
    specifically (e.g. sending a "we couldn't plan your week" email) without
    parsing the skip message.
    """


class InsufficientHealthData(PipelineSkip):
    """Raised when the day has no recovery data to build a briefing from.

    A subtype of ``PipelineSkip`` because withholding the briefing is the
    correct no-op, not a fault — but distinguished from a plain skip because
    callers must react differently: the manual endpoint answers 422 (the day
    cannot support the request) rather than 409 (the briefing already exists),
    and the retry loop keeps retrying this outcome while a real failure burns
    the failure budget.
    """


class LLMProviderError(Exception):
    """Raised when an LLM provider fails to produce a response.

    Covers timeouts, crashes, non-zero exits and malformed output. The message
    is what lands in ``PromptLog.error`` / ``JobRun.error``, so it names the
    provider and, where there is one, the remedy.
    """


class LLMQuotaExceeded(LLMProviderError):  # noqa: N818 - the name the issue specified
    """Raised when the provider's usage limit or quota is exhausted.

    Its own type so a future fallback wrapper can catch it without the engine
    changing. Nothing falls back today: exhausting the subscription must not
    quietly start spending money on a paid API.
    """


class LLMAuthError(LLMProviderError):
    """Raised when the provider rejects MyCoach's credentials.

    Distinct from ``LLMQuotaExceeded``: waiting won't fix it, a new token will.
    """
