# Smart Clinic (web)

Your console `ClinicSystem` as a web app: a Flask JSON API plus a single-page
browser interface for all four roles (patient, nurse, doctor, admin).

## Run it

```bash
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

First login: **A001 / admin123** (the original default admin). Log in, add a
doctor and a nurse, then register patients from the login page.

Your existing `clinic_data.json` works as is. Copy it next to `app.py` (or set
`CLINIC_DATA=/path/to/clinic_data.json`). Old SHA-256 and plain-text passwords
are upgraded to salted hashes automatically.

Optional environment variables: `PORT`, `HOST`, `CLINIC_DATA`, `CLINIC_SECRET`
(session signing key; otherwise one is generated into `.secret_key`).

## What changed from the console version

Same rules, same data format. These behaviours are different on purpose:

- **Each bill belongs to its appointment.** The old code could attach a patient's
  older unpaid bill to a new booking, charging the wrong amount.
- **Confirmation numbers stay visible** on My appointments. The console printed
  it once; on the web, losing it would block check-in.
- **Doctor end time must be after start time**, and slots can't be double-booked
  by two people booking at once (all writes are serialized and saved atomically).
- **Cancelled or status-changed appointments leave the waiting queue**, and
  removing a doctor or patient also clears their queue entries and nurse links.
- **Permissions are enforced on the server**: patients only see their own
  data, nurses/doctors only change appointments assigned to them.
- A corrupt JSON file is renamed to `clinic_data.json.corrupt` instead of being
  silently overwritten with empty data.
- `folium` was imported but never used, so it is no longer required.

## Before real use

This is built for a single clinic on a trusted network. For anything public:
serve it behind HTTPS with a production server (e.g. `gunicorn app:create_app()`),
change the default admin password, and move from the JSON file to a database.
There is no login rate limiting or password-change screen yet.

## Layout

```
app.py          Flask routes, sessions, role checks
clinic_core.py  all business rules (ported from your script)
static/         index.html, app.js, style.css
```
