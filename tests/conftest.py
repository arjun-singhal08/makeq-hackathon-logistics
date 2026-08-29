from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

import pytest
from flask import Flask
from flask_migrate import Migrate, upgrade

from models import (
    Event,
    InventoryMovement,
    InventoryMovementType,
    MealOption,
    MealService,
    MealServiceStatus,
    PickupWindow,
    Queue,
    db,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


@dataclass(frozen=True)
class FoodSetup:
    event_id: int
    queue_id: int
    meal_service_id: int
    meal_option_id: int
    pickup_window_id: int


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "makeq-test.sqlite"


@pytest.fixture
def database_uri(db_path):
    return f"sqlite:///{db_path.as_posix()}"


@pytest.fixture
def app(database_uri):
    test_app = Flask("makeq-food-foundation-tests")
    test_app.config.update(
        TESTING=True,
        SECRET_KEY="makeq-test-only-secret",
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"timeout": 10}},
    )
    db.init_app(test_app)
    Migrate(
        test_app,
        db,
        directory=str(MIGRATIONS_DIR),
        compare_type=True,
        render_as_batch=True,
    )

    with test_app.app_context():
        upgrade(directory=str(MIGRATIONS_DIR))
        yield test_app
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def food_setup(app):
    sequence = 0

    def create_setup(*, option_quantity=20, window_capacity=20, option_name="Vegetarian"):
        nonlocal sequence
        sequence += 1
        suffix = f"{sequence}"

        event = Event(name=f"Test Hackathon {suffix}", active=True)
        db.session.add(event)
        db.session.flush()

        queue = Queue(
            event_id=event.id,
            name=f"{option_name} {suffix}",
            description=f"{option_name} meal collection",
            active=True,
            paused=False,
        )
        db.session.add(queue)
        db.session.flush()

        meal = MealService(
            event_id=event.id,
            name=f"Dinner {suffix}",
            service_date=date(2026, 8, 29),
            start_time=time(18, 30),
            end_time=time(20, 0),
            location="Main Hall",
            status=MealServiceStatus.OPEN,
        )
        db.session.add(meal)
        db.session.flush()

        option = MealOption(
            meal_service_id=meal.id,
            queue_id=queue.id,
            name=option_name,
            description=f"{option_name} dinner",
            planned_quantity=option_quantity,
            received_quantity=option_quantity,
            available_quantity=option_quantity,
            active=True,
        )
        db.session.add(option)
        db.session.flush()

        window = PickupWindow(
            meal_service_id=meal.id,
            start_time=time(18, 30),
            end_time=time(18, 45),
            capacity=window_capacity,
            active=True,
        )
        db.session.add(window)
        db.session.flush()

        if option_quantity:
            db.session.add(
                InventoryMovement(
                    meal_option_id=option.id,
                    movement_type=InventoryMovementType.RECEIVED,
                    quantity=option_quantity,
                    reason="Test inventory",
                )
            )
        db.session.commit()

        return FoodSetup(
            event_id=event.id,
            queue_id=queue.id,
            meal_service_id=meal.id,
            meal_option_id=option.id,
            pickup_window_id=window.id,
        )

    return create_setup
