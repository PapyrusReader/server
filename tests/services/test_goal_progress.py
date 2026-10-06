"""Projection fixtures shared byte-for-byte with the Dart client."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from papyrus.schemas.tracking import Activity, GoalDefinition
from papyrus.services.goal_progress import project_goal

FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures/tracking_projections.json").read_text())


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda value: value["name"])
def test_matching_client_projection(fixture):
    goal = GoalDefinition.model_validate(fixture["goal"])
    ledger = [Activity.model_validate(value) for value in fixture["activities"]]
    result = project_goal(goal, ledger, datetime.fromisoformat(fixture["now"]))

    for key, value in fixture["expected"].items():
        assert result[key] == pytest.approx(value)
