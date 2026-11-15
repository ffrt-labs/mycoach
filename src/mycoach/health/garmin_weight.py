"""Commands that copy the athlete's weight one-way to Garmin Connect (ticket #199).

The reading is already committed when these run (``record_body_measurement``), so a Garmin
outage can only leave a row ``pending``/``failed``, never lose or block it. A row records
its push state; the Garmin sync retries what is left, up to ``MAX_PUSH_ATTEMPTS``. Every
Garmin call is blocking and runs in a thread. Nothing here raises on a Garmin failure.

Garmin usually answers the push with no body, so its id for the weigh-in comes from the
day's list, which can lag. A row pushed without an id says so in ``garmin_push_error``; the
sync looks the id up again (same attempt cap), and a delete looks it up once more, so the
copy can still be deleted. A Garmin delete that fails is logged, not retried: the local row
is already gone, so nothing is left to retry from (that would need a tombstone table), and
the log names the weigh-in to delete in Garmin Connect by hand.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from mycoach.config import get_settings
from mycoach.health.runtime import health_session
from mycoach.models.body_measurement import BodyMeasurement, GarminPushStatus
from mycoach.sources.garmin.client import GarminClient
from mycoach.sources.garmin.weigh_ins import GarminWeighIns

if TYPE_CHECKING:
    from mycoach.health.commands import BodyMeasurementRecord

logger = logging.getLogger(__name__)

MAX_PUSH_ATTEMPTS = 5
PENDING_GRACE = timedelta(minutes=10)
"""A ``pending`` row this young is left to the save flow's own push, so the sync doesn't
push it a second time while that push is still in flight."""

UNPUSHED = (GarminPushStatus.PENDING, GarminPushStatus.FAILED)
ID_NOT_FOUND = "Garmin took the weigh-in but its id was not found"


def _status(row: BodyMeasurement) -> GarminPushStatus | None:
    status = row.garmin_push_status
    return None if status is None else GarminPushStatus(status)


def _configured() -> bool:
    settings = get_settings()
    return bool(settings.garmin_email and settings.garmin_password)


def _weigh_ins_for(client: GarminClient) -> GarminWeighIns:
    return GarminWeighIns(client, get_settings().timezone)


def _connect() -> GarminWeighIns | None:
    client = GarminClient()
    return _weigh_ins_for(client) if client.connect() else None


async def _record_attempt(
    measurement_id: int, weigh_in_id: str | None, error: str | None
) -> GarminPushStatus | None:
    """Store how an attempt went; return the row's new status (None if it is gone)."""
    async with health_session() as session:
        row = await session.get(BodyMeasurement, measurement_id)
        if row is None:  # deleted while the push was in flight
            return None
        row.garmin_push_attempts = (row.garmin_push_attempts or 0) + 1
        if error is None:
            row.garmin_push_status = GarminPushStatus.PUSHED
            row.garmin_weigh_in_id = weigh_in_id
            row.garmin_push_error = None if weigh_in_id is not None else ID_NOT_FOUND
        else:
            row.garmin_push_status = GarminPushStatus.FAILED
            row.garmin_push_error = error[:500]
        await session.commit()
        return _status(row)


async def push_weight_to_garmin(
    athlete_id: int, measurement_id: int, client: GarminClient | None = None
) -> GarminPushStatus | None:
    """Push one weight reading to Garmin; return its push status afterwards.

    A no-op for a waist-only, already pushed, given-up or missing row. Without
    ``client`` (a connected one, the sync's) it connects its own (the save flow); with no
    Garmin credentials configured it does nothing and the row stays ``pending``.
    """
    weigh_ins = _weigh_ins_for(client) if client is not None else None
    async with health_session() as session:
        row = await session.get(BodyMeasurement, measurement_id)
        if (
            row is None
            or row.athlete_id != athlete_id
            or row.weight_kg is None
            or row.garmin_push_status not in UNPUSHED
            or (row.garmin_push_attempts or 0) >= MAX_PUSH_ATTEMPTS
        ):
            return _status(row) if row is not None else None
        weight_kg, measured_at = row.weight_kg, row.measured_at

    if weigh_ins is None:
        if not _configured():
            return GarminPushStatus.PENDING
        try:
            weigh_ins = await asyncio.to_thread(_connect)
        except Exception as e:  # noqa: BLE001 - a Garmin outage must not escape
            logger.warning("Garmin connect for the weight push failed: %s", e)
            weigh_ins = None
        if weigh_ins is None:
            return await _record_attempt(measurement_id, None, "Garmin authentication failed")

    try:
        weigh_in_id = await asyncio.to_thread(weigh_ins.add, weight_kg, measured_at)
    except Exception as e:  # noqa: BLE001 - recorded on the row, retried by the sync
        logger.warning("Garmin weight push for measurement %d failed: %s", measurement_id, e)
        return await _record_attempt(measurement_id, None, f"{type(e).__name__}: {e}")

    status = await _record_attempt(measurement_id, weigh_in_id, None)
    if weigh_in_id is None:
        logger.warning(
            "Garmin weigh-in for measurement %d: %s; the sync looks it up again",
            measurement_id,
            ID_NOT_FOUND,
        )
    if status is None and weigh_in_id is not None:
        # The reading was deleted mid-push: take its copy back out of Garmin.
        await _delete(weigh_ins, weigh_in_id, measured_at)
    return status


async def retry_garmin_weight_pushes(
    athlete_id: int, client: GarminClient, now: datetime | None = None
) -> int:
    """Retry every unpushed weight reading under the attempt cap; return how many pushed.

    Called by the Garmin sync (the 06:00 job and every briefing poll) with its connected
    client. A ``pending`` row younger than ``PENDING_GRACE`` is skipped.
    """
    now = now or datetime.utcnow()
    async with health_session() as session:
        ids = (
            await session.scalars(
                select(BodyMeasurement.id)
                .where(
                    BodyMeasurement.athlete_id == athlete_id,
                    BodyMeasurement.weight_kg.is_not(None),
                    BodyMeasurement.garmin_push_attempts < MAX_PUSH_ATTEMPTS,
                    or_(
                        BodyMeasurement.garmin_push_status == GarminPushStatus.FAILED,
                        (BodyMeasurement.garmin_push_status == GarminPushStatus.PENDING)
                        & (BodyMeasurement.created_at <= now - PENDING_GRACE),
                    ),
                )
                .order_by(BodyMeasurement.measured_at)
            )
        ).all()
    pushed = 0
    for measurement_id in ids:
        if (
            await push_weight_to_garmin(athlete_id, measurement_id, client)
            == GarminPushStatus.PUSHED
        ):
            pushed += 1
    await _look_up_missing_ids(athlete_id, _weigh_ins_for(client))
    return pushed


async def _look_up_missing_ids(athlete_id: int, weigh_ins: GarminWeighIns) -> None:
    """Find Garmin's id for each pushed weight that has none yet, under the attempt cap."""
    async with health_session() as session:
        rows = (
            await session.execute(
                select(BodyMeasurement.id, BodyMeasurement.weight_kg, BodyMeasurement.measured_at)
                .where(
                    BodyMeasurement.athlete_id == athlete_id,
                    BodyMeasurement.weight_kg.is_not(None),
                    BodyMeasurement.garmin_push_status == GarminPushStatus.PUSHED,
                    BodyMeasurement.garmin_weigh_in_id.is_(None),
                    BodyMeasurement.garmin_push_attempts < MAX_PUSH_ATTEMPTS,
                )
                .order_by(BodyMeasurement.measured_at)
            )
        ).all()
    for measurement_id, weight_kg, measured_at in rows:
        try:
            weigh_in_id = await asyncio.to_thread(weigh_ins.find, weight_kg, measured_at)
        except Exception as e:  # noqa: BLE001 - tried again on the next sync
            logger.warning("Garmin id lookup for measurement %d failed: %s", measurement_id, e)
            weigh_in_id = None
        async with health_session() as session:
            row = await session.get(BodyMeasurement, measurement_id)
            if row is None:
                continue
            row.garmin_push_attempts = (row.garmin_push_attempts or 0) + 1
            if weigh_in_id is not None:
                row.garmin_weigh_in_id = weigh_in_id
                row.garmin_push_error = None
            else:
                row.garmin_push_error = (
                    f"{ID_NOT_FOUND} (looked up {row.garmin_push_attempts} times)"
                )
            await session.commit()


async def _delete(weigh_ins: GarminWeighIns, weigh_in_id: str, measured_at: datetime) -> bool:
    try:
        await asyncio.to_thread(weigh_ins.delete, weigh_in_id, measured_at)
        return True
    except Exception as e:  # noqa: BLE001 - the local delete already happened
        logger.warning(
            "Garmin weigh-in %s (%s) delete failed, delete it there by hand: %s",
            weigh_in_id,
            f"{measured_at:%Y-%m-%d %H:%M} UTC",
            e,
        )
        return False


async def delete_weight_from_garmin(deleted: "BodyMeasurementRecord") -> bool:
    """Delete the Garmin copy of a reading ``delete_body_measurement`` removed; return whether
    Garmin deleted it. A pushed reading whose Garmin id was never found is looked up by its
    date and timestamp first. Never raises: the local delete has already happened."""
    if deleted.garmin_push_status != GarminPushStatus.PUSHED or deleted.weight_kg is None:
        return False
    if not _configured():
        return False
    try:
        weigh_ins = await asyncio.to_thread(_connect)
    except Exception as e:  # noqa: BLE001
        logger.warning("Garmin connect for a weigh-in delete failed: %s", e)
        return False
    if weigh_ins is None:
        logger.warning("Garmin weigh-in of %s not deleted: authentication failed", _when(deleted))
        return False
    weigh_in_id = deleted.garmin_weigh_in_id
    if weigh_in_id is None:
        try:
            weigh_in_id = await asyncio.to_thread(
                weigh_ins.find, deleted.weight_kg, deleted.measured_at
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Garmin id lookup for a weigh-in delete failed: %s", e)
        if weigh_in_id is None:
            logger.warning(
                "Garmin weigh-in of %s (%.1f kg) not deleted: its id was not found; "
                "delete it there by hand",
                _when(deleted),
                deleted.weight_kg,
            )
            return False
    return await _delete(weigh_ins, weigh_in_id, deleted.measured_at)


def _when(deleted: "BodyMeasurementRecord") -> str:
    return f"{deleted.measured_at:%Y-%m-%d %H:%M} UTC"
