"""The one way pages tell stored v5 Coach notes, a v4 insight and the notes fallback
apart (#249)."""

import json

from mycoach.coaching.email_schemas import WeeklyRecapEmail
from mycoach.email.coach_notes import stored_notes
from tests.test_email.test_recap_notes import NOTES


def test_v5_content_reads_as_its_model() -> None:
    stored = stored_notes(json.dumps(NOTES), WeeklyRecapEmail)

    assert isinstance(stored.notes, WeeklyRecapEmail)
    assert stored.notes.headline.summary.startswith("Both lifts")
    assert stored.v4 is None
    assert not stored.unavailable


def test_v4_content_is_kept_as_its_dict() -> None:
    v4 = {"week_summary": "A solid week.", "adherence_analysis": "3/3 done."}

    stored = stored_notes(json.dumps(v4), WeeklyRecapEmail)

    assert stored.notes is None
    assert stored.v4 == v4
    assert not stored.unavailable


def test_the_notes_fallback_is_unavailable() -> None:
    for content in (None, "", "null"):
        stored = stored_notes(content, WeeklyRecapEmail)

        assert stored.notes is None
        assert stored.v4 is None
        assert stored.unavailable


def test_content_that_is_not_a_json_object_is_unavailable() -> None:
    for content in ("not json", "[1, 2]", '"text"'):
        assert stored_notes(content, WeeklyRecapEmail).unavailable
