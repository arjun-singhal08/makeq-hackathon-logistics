"""Top-level multiprocessing workers used by persistence and contention tests."""

from flask import Flask


def _worker_app(database_uri):
    from models import db

    app = Flask("makeq-food-worker")
    app.config.update(
        TESTING=True,
        SECRET_KEY="makeq-worker-test-secret",
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"timeout": 10}},
    )
    db.init_app(app)
    return app


def create_ticket_worker(database_uri, setup, quantity, output_queue):
    from food_service import FoodServiceError, create_food_ticket
    from models import db

    app = _worker_app(database_uri)
    with app.app_context():
        try:
            result = create_food_ticket(
                event_id=setup["event_id"],
                meal_service_id=setup["meal_service_id"],
                meal_option_id=setup["meal_option_id"],
                pickup_window_id=setup["pickup_window_id"],
                participant_name="Restart Test Team",
                ticket_type="TEAM",
                quantity=quantity,
            )
            output_queue.put(
                {
                    "kind": "ok",
                    "public_id": result.public_id,
                    "claim_token": result.claim_token,
                }
            )
        except FoodServiceError as error:
            output_queue.put({"kind": "error", "code": error.code})
        except Exception as error:  # pragma: no cover - diagnostic path
            output_queue.put(
                {"kind": "unexpected", "type": type(error).__name__, "message": str(error)}
            )
        finally:
            db.session.remove()


def read_ticket_worker(database_uri, public_id, output_queue):
    from models import InventoryMovement, MealOption, PickupWindow, Ticket, db

    app = _worker_app(database_uri)
    with app.app_context():
        ticket = db.session.execute(
            db.select(Ticket).where(Ticket.public_id == public_id)
        ).scalar_one_or_none()
        if ticket is None:
            output_queue.put({"kind": "missing"})
        else:
            option = db.session.get(MealOption, ticket.meal_option_id)
            window = db.session.get(PickupWindow, ticket.pickup_window_id)
            movement_count = db.session.scalar(
                db.select(db.func.count(InventoryMovement.id)).where(
                    InventoryMovement.ticket_id == ticket.id
                )
            )
            output_queue.put(
                {
                    "kind": "ok",
                    "public_id": ticket.public_id,
                    "quantity": ticket.quantity,
                    "status": ticket.status.value,
                    "event_id": ticket.event_id,
                    "meal_service_id": ticket.meal_service_id,
                    "meal_option_id": ticket.meal_option_id,
                    "pickup_window_id": ticket.pickup_window_id,
                    "option_allocated": option.allocated_quantity,
                    "window_reserved": window.reserved_quantity,
                    "movement_count": movement_count,
                }
            )
        db.session.remove()


def concurrent_reservation_worker(
    database_uri,
    setup,
    quantity,
    ready_queue,
    start_event,
    output_queue,
    participant_name,
):
    from food_service import FoodServiceError, create_food_ticket
    from models import db

    app = _worker_app(database_uri)
    with app.app_context():
        ready_queue.put(True)
        if not start_event.wait(timeout=15):
            output_queue.put({"kind": "unexpected", "message": "start timeout"})
            return
        try:
            result = create_food_ticket(
                event_id=setup["event_id"],
                meal_service_id=setup["meal_service_id"],
                meal_option_id=setup["meal_option_id"],
                pickup_window_id=setup["pickup_window_id"],
                participant_name=participant_name,
                ticket_type="TEAM",
                quantity=quantity,
            )
            output_queue.put({"kind": "ok", "public_id": result.public_id})
        except FoodServiceError as error:
            output_queue.put({"kind": "error", "code": error.code})
        except Exception as error:  # pragma: no cover - diagnostic path
            output_queue.put(
                {"kind": "unexpected", "type": type(error).__name__, "message": str(error)}
            )
        finally:
            db.session.remove()
