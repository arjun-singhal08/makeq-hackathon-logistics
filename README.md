# MakeQ · Smart Hackathon Food Queue & Meal Logistics Engine

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg?logo=python&logoColor=white)](https://python.org)
[![Flask 3.0+](https://img.shields.io/badge/Framework-Flask%203.0%2B-black.svg?logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![SQLAlchemy ORM](https://img.shields.io/badge/ORM-SQLAlchemy%203.1%2B-red.svg)](https://www.sqlalchemy.org)
[![Gunicorn](https://img.shields.io/badge/WSGI-Gunicorn-green.svg)](https://gunicorn.org)
[![Render Deployed](https://img.shields.io/badge/Deployed-Render-46E3B7.svg?logo=render&logoColor=white)](https://arjun-singhal08.onrender.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **Live Production Showcase:** **[https://arjun-singhal08.onrender.com](https://arjun-singhal08.onrender.com)**  
> **Demo Organizer Login:** Username: `admin` | Password: `hackathon2026`

---

## 💡 The Problem

At major hackathons with 500+ hackers, meal times are notoriously chaotic:
1. **Severe Bottlenecks & Wasted Hacking Time:** Hundreds of participants rush the buffet simultaneously, causing 45+ minute lines that disrupt flow state and project momentum.
2. **Dietary Segregation & Cross-Contamination Confusion:** Participants requiring Halal, Vegetarian, Vegan, or Gluten-Free meals struggle to find their allocated portions, which are frequently taken by mistake.
3. **Uncoordinated Team Coordination:** Teams must send members individually or guess arrival times, leading to cold food.
4. **Food Waste vs. Premature Depletion:** Organizers have no live visibility into inventory depletion rates and cannot broadcast second-round leftovers smoothly.

---

## 🚀 The Solution: MakeQ

**MakeQ** is a high-concurrency, production-grade event meal logistics platform engineered to eliminate food lines. Built with **Flask, SQLAlchemy, Gunicorn**, and a responsive dark-slate UI, MakeQ coordinates participants, dietary stations, and organizers in real time.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                MAKEQ SYSTEM                                │
├───────────────────────────────┬─────────────────────────────┬───────────────┤
│    PARTICIPANT INTERFACE      │    VENUE STAGE DISPLAY      │ ORGANIZER DECK│
│                               │                             │               │
│ • Staggered 15-min windows    │ • Full-screen dark UI       │ • Call Next   │
│ • Dietary counters            │ • Massive "NOW SERVING"     │ • Depletion % │
│ • Cryptographic claim token   │ • "NEXT IN LINE" preview    │ • Leftovers 📢│
│ • Wait time estimation        │ • Real-time broadcast line  │ • Pause/Resume│
└───────────────┬───────────────┴──────────────┬──────────────┴───────┬───────┘
                │                              │                      │
                ▼                              ▼                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                FLASK BACKEND & ATOMIC INVENTORY ENGINE                      │
│                                                                             │
│ • Idempotent auto-initialization & database seeding on startup              │
│ • Row-level locking (SELECT ... FOR UPDATE) preventing double-claims        │
│ • SHA-256 ticket claim hash verification                                    │
│ • REST APIs with CORS: /api/queues & /api/display (4s polling)              │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 DATA LAYER: PostgreSQL (Render) / SQLite (Local)             │
│   Events · Meal Services · Dietary Queues · Pickup Windows · Tickets        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## ✨ Key Features

### 1. 🍱 Dedicated Dietary Station Queues
- Segregated queues for **Standard (Omnivore)**, **Halal**, **Vegetarian**, **Vegan**, and **Gluten-Free**.
- Each station operates independently with its own real-time queue counter and calling system.

### 2. ⏱️ Staggered 15-Minute Pickup Windows
- Capacity-limited time slots (e.g., 12:00–12:15, 12:15–12:30) smooth participant arrival rates across the full meal duration.
- Atomic reservation checks prevent overbooking beyond kitchen capacity.

### 3. 🔐 Cryptographic Digital Claim Passes
- Each reservation generates a public ticket identifier (`MQ-XXXXXX`) and a private 32-byte cryptographic claim code.
- Only a SHA-256 digest is stored in the database.
- Digital claim passes render on the participant's device with a one-click copy button and estimated wait time.

### 4. 📺 Live Stage Display View (`/display`)
- Designed specifically for venue projectors and television screens across dining halls.
- High-contrast, dark slate `#0F172A` theme with huge typography.
- Displays "NOW SERVING", "NEXT IN LINE" preview badges, and an emergency/logistics announcement ticker.
- Client-side auto-polling every 4 seconds (`/api/display`) with smooth visual pulse effects when new tickets are called.

### 5. ⚡ Authenticated Organizer Control Deck (`/admin`)
- **One-Click Ticket Calling:** Call the next waiting ticket per station with concurrency protection.
- **Ticket Lifecycle State Machine:** Progress tickets seamlessly through `WAITING` → `CALLED` → `IN_SERVICE` → `COMPLETED`, `NO_SHOW`, or `REQUEUE`.
- **Live Inventory Depletion Progress Bars:** Real-time visibility into available, reserved, and collected portions with dynamic color warnings:
  - 🟢 Green: > 35% remaining
  - 🟡 Amber: 15% – 35% remaining
  - 🔴 Red: < 15% remaining (low inventory alert)
- **📢 Instant Second-Round Leftovers Broadcast:** One-click button broadcasts an immediate notification to all participant screens and venue projectors when uncollected food is opened for general collection.
- **Queue Controls:** Toggle Pause / Resume for any dietary station.
- **Service Switcher:** Seamlessly transition between Breakfast, Lunch, and Dinner.

### 6. 👥 Team Meal Preferences
- Allows hackathon teams to submit coordinated meal preferences across all active team members in one reservation.

### 7. 🚀 Zero-Config Auto-Initialization
- Automatically detects if core tables exist on startup (whether PostgreSQL on Render or local SQLite).
- Runs `db.create_all()` and seeds default demo data idempotently:
  - Default Event: *"Hackathon 2026 Logistics"*
  - 3 Meal Services: Breakfast (08:00–09:30), Lunch (12:00–14:00), Dinner (18:30–20:30)
  - 5 Dietary Options with realistic caps: 150 Standard, 60 Halal, 50 Vegetarian, 20 Vegan, 20 Gluten-Free
  - 15-minute staggered pickup windows

---

## 🛠️ Technology Stack

| Layer | Technologies |
|---|---|
| **Backend Framework** | Python 3.11+, Flask 3.0+ |
| **ORM & Database** | SQLAlchemy 3.1+, PostgreSQL (Production via `psycopg3`), SQLite (Local) |
| **Database Migrations** | Alembic, Flask-Migrate |
| **WSGI Web Server** | Gunicorn (2 workers, 4 threads, timeout 120s) |
| **Frontend UI/UX** | Modern Semantic HTML5, CSS Variables, Inter & JetBrains Mono fonts |
| **Real-Time Layer** | 4-Second Async Auto-Polling via REST APIs with CORS enabled |
| **Testing** | pytest (100% test pass rate across 46 unit & integration tests) |
| **Deployment Platform** | Render (`render.yaml`, `Procfile`) |

---

## ⚡ Quickstart Guide (Local Development)

### Prerequisites
- Python 3.11 or higher
- Git

### 1. Clone Repository & Setup Virtual Environment
```bash
git clone https://github.com/arjun-singhal08/arjun-singhal08.git
cd MakeQ

# Create and activate virtual environment
python -m venv .venv

# On Linux/macOS:
source .venv/bin/activate

# On Windows PowerShell:
.\.venv\Scripts\Activate.ps1
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Launch Application
```bash
python app.py
```
> **Automatic Auto-Seeding:** On first run, MakeQ automatically initializes `instance/makeq.db` with the "Hackathon 2026 Logistics" event, 3 meal services, 5 dietary queues, and 15-minute pickup windows.

Visit the application in your browser:
- **Participant Booking & Live Queues:** [http://127.0.0.1:5000/](http://127.0.0.1:5000/)
- **Stage TV Display:** [http://127.0.0.1:5000/display](http://127.0.0.1:5000/display)
- **Organizer Control Deck:** [http://127.0.0.1:5000/admin](http://127.0.0.1:5000/admin) *(Login: `admin` / `hackathon2026`)*

### 4. Run Test Suite
```bash
pytest
```
*Expected output: `46 passed in ~12s (100%)`*

---

## ☁️ Deployment on Render

MakeQ includes zero-configuration deployment manifests for Render:

1. **`Procfile`**:
   ```text
   web: gunicorn app:app --workers 2 --threads 4 --timeout 120
   ```
2. **`render.yaml`**:
   Configures the web service runtime, build command, and environment variables.

### Environment Variables

| Variable | Description | Default / Example |
|---|---|---|
| `DATABASE_URL` / `MAKEQ_DATABASE_URL` | PostgreSQL connection URI (auto-adapted for `postgresql+psycopg://`) | Render PostgreSQL URI |
| `MAKEQ_SECRET_KEY` | Flask session secret key | Auto-generated in production |
| `MAKEQ_ENV` | Environment identifier | `production` |
| `MAKEQ_ADMIN_USERNAME` | Organizer deck username | `admin` |
| `MAKEQ_ADMIN_PASSWORD` | Organizer deck password | `hackathon2026` |
| `PORT` | Listening port for Gunicorn | `5000` (or set by Render) |

---

## 📡 REST API Reference

All public API endpoints return JSON and include cross-origin resource sharing (`Access-Control-Allow-Origin: *`) headers for integration with remote signage screens and mobile applications.

### 1. `GET /api/queues`
Returns a real-time snapshot of all dietary queue stations and active announcements.

**Response Example:**
```json
{
  "event": { "name": "Hackathon 2026 Logistics" },
  "queues": {
    "Standard": {
      "now_serving": { "public_id": "MQ-0042", "status": "CALLED" },
      "paused": false,
      "waiting_count": 8,
      "next_up": [
        { "public_id": "MQ-0043" },
        { "public_id": "MQ-0044" }
      ]
    },
    "Halal": {
      "now_serving": null,
      "paused": false,
      "waiting_count": 3,
      "next_up": [{ "public_id": "MQ-0045" }]
    }
  },
  "announcements": [
    {
      "id": 1,
      "message": "🎉 Welcome to Hackathon 2026! Meal pickup windows are now open.",
      "posted_at": "12:00 UTC",
      "created_at": "2026-09-20T12:00:00Z"
    }
  ]
}
```

### 2. `GET /api/display`
Optimized payload for high-resolution venue projectors and stage TVs.

**Response Example:**
```json
{
  "event_name": "Hackathon 2026 Logistics",
  "total_waiting": 14,
  "queues": { ... },
  "announcements": [ ... ],
  "timestamp": "2026-09-20T12:15:30Z"
}
```

---

## 👨‍💻 Author & Portfolio

**Arjun Singhal**  
*Business AI Systems Student, National University of Singapore (NUS)*  
Passionate about Artificial Intelligence, Full-Stack Architecture, and Building Impactful Products.

- 🌐 **Live Demo:** [https://arjun-singhal08.onrender.com](https://arjun-singhal08.onrender.com)
- 💼 **LinkedIn:** [https://www.linkedin.com/in/arjunsinghal25](https://www.linkedin.com/in/arjunsinghal25)
- 💻 **GitHub:** [https://github.com/arjun-singhal08](https://github.com/arjun-singhal08)

---

## 📄 License
This project is open-source and licensed under the [MIT License](LICENSE).
