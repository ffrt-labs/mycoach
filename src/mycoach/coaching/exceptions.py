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
