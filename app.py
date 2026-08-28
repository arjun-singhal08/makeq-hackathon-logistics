import os
from datetime import datetime
from functools import wraps
from itertools import count
import re

from flask import Flask, flash, redirect, render_template, request, session, url_for


app = Flask(__name__)
# A random fallback keeps local development runnable without committing a key.
# Set LUNCHLINE_SECRET_KEY in deployed environments for stable sessions.
app.secret_key = os.getenv("LUNCHLINE_SECRET_KEY") or os.urandom(32)

DIETARY_OPTIONS = ("Normal", "Halal", "Vegetarian", "Vegan")
TIME_SLOTS = (
    "Now / As Soon As Possible",
    "11:00 AM - 11:30 AM",
    "11:30 AM - 12:00 PM",
)
ADMIN_USERNAME = os.getenv("LUNCHLINE_ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("LUNCHLINE_ADMIN_PASSWORD")

DEMO_GROUP_NAMES = (
    "Alpha", "Beta", "Gamma", "Delta", "Epsilon",
    "Zeta", "Eta", "Theta", "Iota", "Kappa",
    "Lambda", "Mu", "Nu", "Xi", "Omicron",
    "Pi", "Rho", "Sigma", "Tau", "Upsilon",
)
DEMO_SOLO_NAMES = (
    "Alex", "Ben", "Charlie", "Dave", "Eddie",
    "Frankie", "George", "Harry", "Izzy", "Jack",
    "Kai", "Leo", "Max", "Nate", "Ollie",
    "Pete", "Quinn", "Ryan", "Sam", "Toby",
)

# Each queue holds ticket dictionaries so the dashboard can show useful context.
queues = {
    dietary: {
        "waiting": [],
        "now_serving": None,
        "paused": False,
        "served": 0,
        "second_round_eligible": [],
        "second_round_active": False,
    }
    for dietary in DIETARY_OPTIONS
}
solo_numbers = count(1)
announcement_numbers = count(1)
announcements = []


def make_group_id(group_name):
    clean_name = re.sub(r"[^A-Za-z0-9]+", "-", group_name.strip()).strip("-").upper()
    return f"G-{clean_name}" if clean_name else None


def checkbox_checked(field_name):
    """Accept browser checkbox values plus common API/test equivalents."""
    return request.form.get(field_name, "").strip().lower() in {
        "on", "true", "1", "yes"
    }


def admin_required(view):
    @wraps(view)
    def protected_view(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Admin login required for that action.", "warning")
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    return protected_view


def queue_snapshot():
    return {
        dietary: {
            "waiting": list(queue["waiting"]),
            "now_serving": queue["now_serving"],
            "paused": queue["paused"],
            "served": queue["served"],
            "second_round_eligible": list(queue["second_round_eligible"]),
            "second_round_active": queue["second_round_active"],
        }
        for dietary, queue in queues.items()
    }


def move_current_to_second_round(queue):
    """Move an opted-in primary ticket to the leftovers pool once its turn passes."""
    current = queue["now_serving"]
    if (
        current
        and current.get("second_round_opt_in")
        and not current.get("second_round")
        and current not in queue["second_round_eligible"]
    ):
        current["initial_turn_passed"] = True
        queue["second_round_eligible"].append(current)


def call_ticket(queue, ticket, *, second_round=False):
    """Make a ticket the active ticket and update the queue counters."""
    ticket["second_round"] = second_round
    queue["now_serving"] = ticket
    queue["second_round_active"] = second_round
    queue["served"] += 1


def seed_demo_data(prepare_leftovers=True):
    """Seed predictable presentation data, including ready demo leftovers."""
    for index, group_name in enumerate(DEMO_GROUP_NAMES):
        dietary = DIETARY_OPTIONS[index % len(DIETARY_OPTIONS)]
        queues[dietary]["waiting"].append(
            {
                "id": make_group_id(group_name),
                "label": group_name,
                "type": "Group",
                "joined_at": datetime.now().strftime("%H:%M"),
                "time_slot": TIME_SLOTS[index % len(TIME_SLOTS)],
                "second_round_opt_in": index % 3 == 0,
                "initial_turn_passed": False,
                "second_round": False,
            }
        )

    for index, nickname in enumerate(DEMO_SOLO_NAMES):
        dietary = DIETARY_OPTIONS[(index + 1) % len(DIETARY_OPTIONS)]
        queues[dietary]["waiting"].append(
            {
                "id": f"S-{next(solo_numbers)}",
                "label": nickname,
                "type": "Solo",
                "joined_at": datetime.now().strftime("%H:%M"),
                "time_slot": TIME_SLOTS[(index + 1) % len(TIME_SLOTS)],
                "second_round_opt_in": index % 4 == 0,
                "initial_turn_passed": False,
                "second_round": False,
            }
        )

    if prepare_leftovers:
        # Keep one completed opted-in ticket ready in each category so the
        # leftovers controls can be demonstrated immediately after a reload.
        for queue in queues.values():
            demo_leftover = next(
                (ticket for ticket in queue["waiting"] if ticket["second_round_opt_in"]),
                None,
            )
            if demo_leftover is not None:
                queue["waiting"].remove(demo_leftover)
                demo_leftover["initial_turn_passed"] = True
                queue["second_round_eligible"].append(demo_leftover)


seed_demo_data()


def reload_demo_data():
    """Reset all queues and reload the presentation dataset from the beginning."""
    global solo_numbers
    queues.clear()
    queues.update(
        {
            dietary: {
                "waiting": [],
                "now_serving": None,
                "paused": False,
                "served": 0,
                "second_round_eligible": [],
                "second_round_active": False,
            }
            for dietary in DIETARY_OPTIONS
        }
    )
    solo_numbers = count(1)
    seed_demo_data()


@app.get("/")
def index():
    return render_template(
        "index.html",
        dietary_options=DIETARY_OPTIONS,
        time_slots=TIME_SLOTS,
        queues=queue_snapshot(),
        announcements=list(announcements),
        is_admin=session.get("is_admin", False),
    )


@app.post("/register")
def register():
    dietary = request.form.get("dietary", "").strip()
    ticket_type = request.form.get("ticket_type", "solo").strip()
    group_name = request.form.get("group_name", "").strip()
    time_slot = request.form.get("time_slot", "").strip()

    if dietary not in DIETARY_OPTIONS:
        flash("Choose a valid dietary preference.", "danger")
        return redirect(url_for("index"))
    if time_slot not in TIME_SLOTS:
        flash("Choose a pickup time slot.", "danger")
        return redirect(url_for("index"))

    if ticket_type == "group":
        ticket_id = make_group_id(group_name)
        if not ticket_id:
            flash("Enter a group name to create a group ticket.", "danger")
            return redirect(url_for("index"))
        all_tickets = (
            queues[dietary]["waiting"] + queues[dietary]["second_round_eligible"]
        )
        if queues[dietary]["now_serving"]:
            all_tickets.append(queues[dietary]["now_serving"])
        if any(ticket["id"] == ticket_id for ticket in all_tickets):
            flash(f"{ticket_id} is already waiting in the {dietary} queue.", "warning")
            return redirect(url_for("index"))
        label = group_name
    else:
        ticket_id = f"S-{next(solo_numbers)}"
        label = "Solo developer"

    queues[dietary]["waiting"].append(
        {
            "id": ticket_id,
            "label": label,
            "type": "Group" if ticket_type == "group" else "Solo",
            "joined_at": datetime.now().strftime("%H:%M"),
            "time_slot": time_slot,
            "second_round_opt_in": checkbox_checked("second_round_opt_in"),
            "initial_turn_passed": False,
            "second_round": False,
        }
    )
    flash(f"Ticket {ticket_id} added to the {dietary} queue.", "success")
    return redirect(url_for("index"))


@app.post("/admin/login")
def admin_login():
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        flash(
            "Admin login is unavailable until LUNCHLINE_ADMIN_USERNAME and "
            "LUNCHLINE_ADMIN_PASSWORD are configured.",
            "danger",
        )
    elif (
        request.form.get("username") == ADMIN_USERNAME
        and request.form.get("password") == ADMIN_PASSWORD
    ):
        session["is_admin"] = True
        flash("Admin controls unlocked.", "success")
    else:
        flash("Invalid admin credentials.", "danger")
    return redirect(url_for("index"))


@app.post("/admin/logout")
@admin_required
def admin_logout():
    session.pop("is_admin", None)
    flash("Admin controls locked.", "info")
    return redirect(url_for("index"))


@app.post("/admin/reload-demo")
@admin_required
def reload_demo():
    reload_demo_data()
    flash("Demo dataset reloaded. Opted-in tickets are ready for second-round testing.", "info")
    return redirect(url_for("index"))


@app.post("/admin/call-next")
@admin_required
def call_next():
    dietary = request.form.get("dietary", "Normal").strip()
    queue = queues.get(dietary)
    if queue is None:
        flash("Choose a valid queue.", "danger")
    elif queue["paused"]:
        flash(f"{dietary} queue is paused.", "warning")
    elif not queue["waiting"]:
        move_current_to_second_round(queue)
        flash(f"The {dietary} queue is empty.", "info")
    else:
        move_current_to_second_round(queue)
        call_ticket(queue, queue["waiting"].pop(0))
        flash(f"Now serving {queue['now_serving']['id']} from the {dietary} queue.", "success")
    return redirect(url_for("index"))


@app.post("/admin/update-queue")
@admin_required
def update_queue():
    dietary = request.form.get("dietary", "").strip()
    action = request.form.get("action", "").strip()
    queue = queues.get(dietary)

    if queue is None:
        flash("Choose a valid queue to update.", "danger")
        return redirect(url_for("index"))

    if action == "pause":
        queue["paused"] = True
        flash(f"{dietary} queue paused.", "warning")
    elif action == "resume":
        queue["paused"] = False
        flash(f"{dietary} queue resumed.", "success")
    elif action == "purge":
        try:
            amount = max(0, int(request.form.get("amount", "0")))
        except ValueError:
            amount = 0
        removed = min(amount, len(queue["waiting"]))
        del queue["waiting"][:removed]
        flash(f"Skipped {removed} waiting ticket(s) in the {dietary} queue.", "warning")
    else:
        flash("Choose a queue action.", "danger")

    return redirect(url_for("index"))


@app.post("/admin/call-second-round")
@admin_required
def call_second_round():
    dietary = request.form.get("dietary", "").strip()
    queue = queues.get(dietary)
    if queue is None:
        flash("Choose a valid queue.", "danger")
    elif queue["paused"]:
        flash(f"{dietary} queue is paused.", "warning")
    else:
        # Finalize the currently displayed primary ticket before checking the pool.
        # This also makes the one-ticket/last-ticket case work when the admin
        # clicks "Call leftovers" directly.
        move_current_to_second_round(queue)
        if not queue["second_round_eligible"]:
            flash(f"No opted-in tickets are eligible for a second round in {dietary}.", "info")
        else:
            call_ticket(queue, queue["second_round_eligible"].pop(0), second_round=True)
            flash(
                f"Second Round / Leftovers: now serving {queue['now_serving']['id']}.",
                "success",
            )
    return redirect(url_for("index"))


@app.post("/admin/announcement")
@admin_required
def post_announcement():
    message = request.form.get("message", "").strip()
    if message:
        announcements.insert(
            0,
            {
                "id": next(announcement_numbers),
                "message": message,
                "posted_at": datetime.now().strftime("%H:%M"),
            },
        )
        flash("Announcement posted.", "success")
    else:
        flash("Enter an announcement first.", "danger")
    return redirect(url_for("index"))


@app.post("/admin/announcement/<int:announcement_id>/delete")
@admin_required
def delete_announcement(announcement_id):
    original_count = len(announcements)
    announcements[:] = [
        announcement
        for announcement in announcements
        if announcement["id"] != announcement_id
    ]
    if len(announcements) < original_count:
        flash("Announcement deleted.", "info")
    else:
        flash("Announcement was already cleared.", "info")
    return redirect(url_for("index"))


@app.post("/admin/announcement/clear")
@admin_required
def clear_announcements():
    announcements.clear()
    flash("All announcements cleared.", "info")
    return redirect(url_for("index"))


@app.get("/display")
def display():
    return render_template(
        "display.html", queues=queue_snapshot(), announcements=list(announcements)
    )


@app.get("/api/queues")
def queues_api():
    return {"queues": queue_snapshot(), "announcements": list(announcements)}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
