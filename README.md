# MakeQ

MakeQ is a food-distribution and food-queue application for hackathons. The
Phase 1 foundation persists events, meal services, configurable food options,
capacity-limited pickup windows, food tickets, inventory history, and the full
ticket lifecycle.

Stage 1 adds an organizer-managed hackathon roster: registered participants,
teams, and one optional team membership per participant. Once an event has a
roster record, new reservations must select an active registered participant or
team; older tickets remain valid historical records without an identity link.

Stage 2 stores one coordinated team preference submission with one relational
meal choice per active team member. It intentionally records preferences only:
it does not yet allocate food, reserve pickup capacity, or create team tickets.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
flask --app app db upgrade
flask --app app seed-demo
python app.py
```

The local SQLite database is created at `instance/makeq.db`. Database
starter setup is explicit and idempotent. `seed-demo` creates `MakeQ Demo
Hackathon`, an open Dinner service, configurable Standard, Halal, Vegetarian,
and Vegan options, and three pickup windows. It never creates participant
tickets.

If the database already came from the pre-migration Phase 1 application, mark
that existing schema as the baseline once before upgrading:

```powershell
flask --app app db stamp 0001_phase1_baseline
flask --app app db upgrade
flask --app app seed-demo
```

Do not stamp a fresh database; use the normal `db upgrade` command instead.

## Configuration

- `MAKEQ_SECRET_KEY`: stable session-signing key; required for stable deployed sessions.
- `MAKEQ_ENV`: set to `production` in production; this requires a secret key.
- `MAKEQ_ADMIN_USERNAME`: shared Phase 1A administrator username.
- `MAKEQ_ADMIN_PASSWORD`: shared Phase 1A administrator password.
- `MAKEQ_DATABASE_URL`: optional SQLAlchemy database URL.
- `DATABASE_URL`: Render-compatible fallback database URL.
- `MAKEQ_SECURE_COOKIES`: set to `true` when serving over HTTPS.
- `MAKEQ_DEBUG`: set to `true` only for local debugging.
- `PORT`: HTTP port, default `5000`.

The former `LUNCHLINE_SECRET_KEY`, `LUNCHLINE_ADMIN_USERNAME`, and
`LUNCHLINE_ADMIN_PASSWORD` variables remain supported as compatibility
fallbacks. New deployments should use the `MAKEQ_*` names.

## Ticket lifecycle

Tickets move through `WAITING`, `CALLED`, `IN_SERVICE`, `COMPLETED`,
`NO_SHOW`, and `CANCELLED`. Requeueing keeps the same ticket record and public
ID, returns an eligible ticket to `WAITING`, and places it at the end of its
queue. Normal queue operations never delete ticket records.

Food tickets store their quantity explicitly and link to one event, meal
service, meal option, and pickup window. Each has a public ticket ID and a
separate private claim credential; only a SHA-256 digest of the private token
is stored, and claim credentials are never included in the public queue API.

Reservations update meal availability and pickup-window capacity with
conditional database writes inside one transaction. Cancellation and no-show
release both reservations. Completion records the amount actually collected
and releases any uncollected balance. `InventoryMovement` rows retain the
received, allocated, released, collected, wasted, and adjusted history.

## Production

The existing WSGI entry point remains:

```text
gunicorn app:app
```

SQLite is intended for local development. Set `MAKEQ_DATABASE_URL` or
`DATABASE_URL` for PostgreSQL deployment. Run `flask --app app db upgrade` as a
one-off pre-deploy step before starting Gunicorn. Do not run `seed-demo` in
production unless the starter event is intentionally wanted.

## About the author

# Hi, I'm Arjun Singhal 👋

I'm a Business AI Systems student at the National University of Singapore, exploring AI, software development, and product building to solve real-world problems.

## About Me

- 🎓 Studying Business AI Systems at NUS
- 🤖 Interested in Artificial Intelligence and AI-powered applications
- 💻 Currently building my foundations in Python, software development, and web technologies
- 🚀 Interested in startups, product development, and technology
- 🧠 Learning by building projects, participating in hackathons, and experimenting with new technologies

## Currently Learning

- Python
- Git & GitHub
- Web development
- AI application development
- Software engineering fundamentals

## Featured Project

### LunchLine — Hackception by NUS Hackers

🔗 🔗 **[Try MakeQ →](https://arjun-singhal08.onrender.com)**
💻 **Source Code:** https://github.com/arjun-singhal08/arjun-singhal08

The project gave me my first hands-on experience building and running a web application and introduced me to technologies and workflows that I'm continuing to learn.

## Interests

Artificial Intelligence · Software Development · Product Building · Startups · Technology

---

📫 [LinkedIn](https://www.linkedin.com/in/arjunsinghal25)
