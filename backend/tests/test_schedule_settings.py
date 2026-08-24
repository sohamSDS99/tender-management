"""The sweep schedule as an operator-editable value.

A human sets the times from the dashboard; that person is the authorisation, and
the value has to survive a container restart. See docs/DECISIONS.md D14.
"""

from __future__ import annotations

import pytest

from app.models import KEY_RUN_HOURS, KEY_SCHEDULER_ENABLED, AppSetting
from app.services import schedule_settings as sched
from app.services.schedule_settings import InvalidSchedule, InvalidTriggerState

# --- validation ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([0, 12], [0, 12]),
        ("0,12", [0, 12]),
        ("6, 18", [6, 18]),
        ([12, 0], [0, 12]),  # sorted
        ([12, 0, 12], [0, 12]),  # de-duplicated
        ([0], [0]),  # midnight only
        ([23], [23]),
        (["7", 19], [7, 19]),  # mixed strings and ints
    ],
)
def test_valid_schedules_are_normalised(raw, expected) -> None:
    assert sched.parse_hours(raw) == expected


@pytest.mark.parametrize(
    ("raw", "because"),
    [
        ([], "at least one"),
        ("", "at least one"),
        ([24], "between 0 and 23"),
        ([-1], "between 0 and 23"),
        (["noon"], "between 0 and 23"),
        ([1.5], "between 0 and 23"),
        (5.5, "as a list"),
        (None, "as a list"),
        ([0, 2, 4, 6, 8, 10, 12], "at most 6"),
    ],
)
def test_invalid_schedules_are_refused_with_a_readable_reason(raw, because) -> None:
    with pytest.raises(InvalidSchedule) as caught:
        sched.parse_hours(raw)
    assert because in str(caught.value)


def test_a_bad_value_is_never_silently_repaired() -> None:
    """Dropping the bad entry would leave the operator trusting a wrong schedule."""
    with pytest.raises(InvalidSchedule):
        sched.parse_hours([0, 99, 12])


# --- persistence -----------------------------------------------------------


def test_the_environment_default_applies_until_someone_changes_it(db_session, settings) -> None:
    assert sched.is_customised(db_session) is False
    assert sched.get_run_hours(db_session, settings) == [0, 12]


def test_a_stored_schedule_wins_over_the_environment(db_session, settings) -> None:
    sched.set_run_hours(db_session, [7, 19], settings)
    assert sched.is_customised(db_session) is True
    assert sched.get_run_hours(db_session, settings) == [7, 19]


def test_changing_it_twice_updates_rather_than_duplicating(db_session, settings) -> None:
    sched.set_run_hours(db_session, [7], settings)
    sched.set_run_hours(db_session, [8, 20], settings)
    assert db_session.query(AppSetting).filter_by(key=KEY_RUN_HOURS).count() == 1
    assert sched.get_run_hours(db_session, settings) == [8, 20]


def test_resetting_hands_the_schedule_back_to_the_environment(db_session, settings) -> None:
    sched.set_run_hours(db_session, [3], settings)
    sched.reset_run_hours(db_session)
    assert sched.is_customised(db_session) is False
    assert sched.get_run_hours(db_session, settings) == [0, 12]


def test_a_corrupt_stored_row_falls_back_instead_of_breaking_the_sweep(db_session, settings) -> None:
    """A hand-edited row must never stop the scheduler from starting."""
    db_session.add(AppSetting(key=KEY_RUN_HOURS, value="banana"))
    db_session.commit()
    assert sched.get_run_hours(db_session, settings) == [0, 12]


def test_an_invalid_change_is_refused_and_leaves_the_old_value_intact(db_session, settings) -> None:
    sched.set_run_hours(db_session, [9, 21], settings)
    with pytest.raises(InvalidSchedule):
        sched.set_run_hours(db_session, [99], settings)
    assert sched.get_run_hours(db_session, settings) == [9, 21]


# --- whether the sweep runs at all (docs/DECISIONS.md D21) -----------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        (" on ", True),
        ("1", True),
        ("false", False),
        ("off", False),
        ("0", False),
        ("disabled", False),
    ],
)
def test_valid_trigger_states_are_parsed(raw, expected) -> None:
    assert sched.parse_enabled(raw) is expected


@pytest.mark.parametrize("raw", ["maybe", "", None, 2, [], 1.0])
def test_an_unrecognised_trigger_state_is_refused_rather_than_assumed(raw) -> None:
    """Coercing junk to False would pause every sweep and report success."""
    with pytest.raises(InvalidTriggerState) as caught:
        sched.parse_enabled(raw)
    assert "pause" in str(caught.value)


def test_the_environment_default_decides_until_someone_changes_it(db_session, settings) -> None:
    assert sched.enabled_is_customised(db_session) is False
    assert sched.get_enabled(db_session, settings) is False  # fixture has it off
    assert sched.enabled_changed_at(db_session) is None


def test_switching_sweeps_on_overrides_an_environment_default_of_off(db_session, settings) -> None:
    sched.set_enabled(db_session, True, settings)
    assert sched.get_enabled(db_session, settings) is True
    assert sched.enabled_is_customised(db_session) is True
    assert sched.enabled_changed_at(db_session) is not None


def test_pausing_is_stored_and_survives_a_read(db_session, settings) -> None:
    on = settings.model_copy(update={"enable_scheduler": True})
    assert sched.get_enabled(db_session, on) is True
    sched.set_enabled(db_session, False, on)
    assert sched.get_enabled(db_session, on) is False


def test_toggling_twice_updates_rather_than_duplicating(db_session, settings) -> None:
    sched.set_enabled(db_session, True, settings)
    sched.set_enabled(db_session, False, settings)
    assert db_session.query(AppSetting).filter_by(key=KEY_SCHEDULER_ENABLED).count() == 1
    assert sched.get_enabled(db_session, settings) is False


def test_a_corrupt_stored_trigger_row_falls_back_to_the_environment(db_session, settings) -> None:
    """A hand-edited row must not silently decide whether sweeps happen."""
    db_session.add(AppSetting(key=KEY_SCHEDULER_ENABLED, value="banana"))
    db_session.commit()
    on = settings.model_copy(update={"enable_scheduler": True})
    assert sched.get_enabled(db_session, on) is True
    assert sched.get_enabled(db_session, settings) is False


def test_resetting_hands_the_decision_back_to_the_environment(db_session, settings) -> None:
    sched.set_enabled(db_session, True, settings)
    sched.reset_enabled(db_session)
    assert sched.enabled_is_customised(db_session) is False
    assert sched.get_enabled(db_session, settings) is False


def test_the_two_settings_are_independent(db_session, settings) -> None:
    """Pausing must not disturb the hours, so resuming restores what was chosen."""
    sched.set_run_hours(db_session, [7, 19], settings)
    sched.set_enabled(db_session, False, settings)
    assert sched.get_run_hours(db_session, settings) == [7, 19]
    sched.set_enabled(db_session, True, settings)
    assert sched.get_run_hours(db_session, settings) == [7, 19]
    assert db_session.query(AppSetting).filter_by(key=KEY_RUN_HOURS).count() == 1
