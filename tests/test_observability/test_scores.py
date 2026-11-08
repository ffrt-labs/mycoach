"""The ScoreSink port: deterministic score IDs and the Langfuse adapter."""

import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from mycoach.observability.scores import (
    LangfuseScoreSink,
    NoopScoreSink,
    Score,
    get_score_sink,
    score_sink_for,
)
from mycoach.observability.tracing import NoopTracer, set_tracer
from tests.test_observability.conftest import Captured

TRACE = "ef2156ef5a01aa2158bfb9e1b4a8fb29"
AT = datetime(2024, 6, 10, tzinfo=UTC)


class _RecordingClient:
    """Stands in for ``langfuse.Langfuse``: records ``create_score`` calls."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error = error

    def create_score(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error


class TestScoreId:
    def test_id_is_trace_id_and_name(self) -> None:
        score = Score.boolean(TRACE, "parse_ok", True, timestamp=AT)
        assert score.id == "ef2156ef5a01aa2158bfb9e1b4a8fb29:parse_ok"

    def test_qualifier_is_appended(self) -> None:
        score = Score.boolean(TRACE, "parse_ok", True, timestamp=AT, qualifier="cardio_plan")
        assert score.id == "ef2156ef5a01aa2158bfb9e1b4a8fb29:parse_ok:cardio_plan"

    def test_re_emitting_the_same_score_gives_the_same_id(self) -> None:
        first = Score.numeric(TRACE, "adherence_reps_delta", -1.5, timestamp=AT, qualifier="42")
        again = Score.numeric(TRACE, "adherence_reps_delta", 0.5, timestamp=AT, qualifier="42")
        assert first.id == again.id == "ef2156ef5a01aa2158bfb9e1b4a8fb29:adherence_reps_delta:42"


class TestLangfuseScoreSink:
    def test_sends_the_score_with_its_id_and_stable_timestamp(self) -> None:
        client = _RecordingClient()
        sink = LangfuseScoreSink(client)  # type: ignore[arg-type]

        sink.emit(
            Score.boolean(
                TRACE,
                "fallback_used",
                True,
                timestamp=AT,
                qualifier="gym_adjustment:0",
                observation_id="0123456789abcdef",
                metadata={"day_of_week": 0},
            )
        )

        assert client.calls == [
            {
                "score_id": "ef2156ef5a01aa2158bfb9e1b4a8fb29:fallback_used:gym_adjustment:0",
                "trace_id": TRACE,
                "observation_id": "0123456789abcdef",
                "name": "fallback_used",
                "value": 1.0,
                "data_type": "BOOLEAN",
                "timestamp": AT,
                "comment": None,
                "metadata": {"day_of_week": 0},
            }
        ]

    def test_numeric_score(self) -> None:
        client = _RecordingClient()
        LangfuseScoreSink(client).emit(  # type: ignore[arg-type]
            Score.numeric(TRACE, "adherence_duration_ratio", 0.9, timestamp=AT)
        )
        [call] = client.calls
        assert call["value"] == 0.9
        assert call["data_type"] == "NUMERIC"
        assert call["observation_id"] is None
        assert call["metadata"] is None

    def test_client_failure_is_logged_at_warn_and_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _RecordingClient(error=RuntimeError("SDK bug"))
        sink = LangfuseScoreSink(client)  # type: ignore[arg-type]

        with caplog.at_level(logging.WARNING, logger="mycoach.observability.scores"):
            sink.emit(Score.boolean(TRACE, "parse_ok", False, timestamp=AT))

        assert len(client.calls) == 1
        [record] = [r for r in caplog.records if r.name == "mycoach.observability.scores"]
        assert record.levelno == logging.WARNING
        assert "parse_ok" in record.getMessage()


class TestScoreSinkFor:
    def test_langfuse_off_gives_a_noop_sink(self) -> None:
        assert isinstance(score_sink_for(NoopTracer()), NoopScoreSink)

    def test_process_sink_follows_the_process_tracer(self) -> None:
        set_tracer(NoopTracer())
        assert isinstance(get_score_sink(), NoopScoreSink)

    def test_langfuse_on_sends_through_the_tracers_client(self, captured: Captured) -> None:
        score_sink_for(captured.tracer).emit(Score.boolean(TRACE, "parse_ok", True, timestamp=AT))

        # ``tracer_with`` records the client's create_score calls.
        [call] = captured.tracer.client.score_calls  # type: ignore[attr-defined]
        assert call["score_id"] == "ef2156ef5a01aa2158bfb9e1b4a8fb29:parse_ok"
