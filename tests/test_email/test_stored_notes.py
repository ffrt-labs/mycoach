"""The one way pages tell stored v5 Coach notes, a legacy (pre-Coach-notes) insight and
the notes fallback apart (#249, #244)."""

import json

from mycoach.coaching.email_schemas import WeeklyRecapEmail
from mycoach.email.coach_notes import NotesKind, stored_notes
from tests.test_email.test_recap_notes import NOTES


def test_v5_content_reads_as_its_model() -> None:
    stored = stored_notes(json.dumps(NOTES), WeeklyRecapEmail)

    assert stored.kind is NotesKind.COACH_NOTES
    assert isinstance(stored.notes, WeeklyRecapEmail)
    assert stored.notes.headline.summary.startswith("Both lifts")
    assert not stored.legacy
    assert not stored.fell_back


def test_v4_content_is_legacy_with_nothing_to_show() -> None:
    """#244: a v4 insight is no longer rendered, only named as predating Coach notes."""
    v4 = {"week_summary": "A solid week.", "adherence_analysis": "3/3 done."}

    stored = stored_notes(json.dumps(v4), WeeklyRecapEmail)

    assert stored.kind is NotesKind.LEGACY
    assert stored.notes is None
    assert stored.legacy
    assert not stored.fell_back


def test_the_notes_fallback_is_its_own_state() -> None:
    for content in (None, "", "null"):
        stored = stored_notes(content, WeeklyRecapEmail)

        assert stored.kind is NotesKind.FALLBACK
        assert stored.notes is None
        assert stored.fell_back
        assert not stored.legacy


def test_content_that_is_not_a_json_object_is_the_fallback() -> None:
    for content in ("not json", "[1, 2]", '"text"'):
        assert stored_notes(content, WeeklyRecapEmail).kind is NotesKind.FALLBACK
