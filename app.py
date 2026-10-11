"""Smart Clinic — web version.

Run:  python app.py        then open http://127.0.0.1:5000
Default admin login: A001 / admin123  (change it after first login by
creating your own admin flow, or edit clinic_data.json).
"""
import os
import secrets
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory, session

from clinic_core import (AccessDeniedError, ClinicError, ClinicService, NotFoundError,
                         VALID_STATUSES, DAYS)

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.environ.get("CLINIC_DATA", os.path.join(BASE, "clinic_data.json"))


def _secret_key():
    key = os.environ.get("CLINIC_SECRET")
    if key:
        return key
    path = os.path.join(BASE, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    with open(path) as f:
        return f.read().strip()


def create_app(data_file=DATA_FILE):
    app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="/static")
    app.secret_key = _secret_key()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      MAX_CONTENT_LENGTH=64 * 1024)
    clinic = ClinicService(data_file)
    app.clinic = clinic

    # ---------------------------------------------------------- helpers
    def current_user():
        uid = session.get("user_id")
        return clinic.find_user(uid) if uid else None

    def login_required(*roles):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                user = current_user()
                if user is None:
                    session.clear()
                    return jsonify(error="Please log in."), 401
                if roles and user["role"] not in roles:
                    return jsonify(error="Access denied."), 403
                return fn(user, *a, **kw)
            return wrapper
        return deco

    def body():
        return request.get_json(silent=True) or {}

    @app.errorhandler(ClinicError)
    def clinic_error(err):
        code = 403 if isinstance(err, AccessDeniedError) else 404 if isinstance(err, NotFoundError) else 400
        return jsonify(error=str(err)), code

    @app.errorhandler(404)
    def not_found(_):
        return jsonify(error="Not found."), 404

    @app.errorhandler(500)
    def server_error(_):
        return jsonify(error="Something went wrong on the server."), 500

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.before_request
    def require_json_for_writes():
        # Cross-site forms cannot send application/json, which blocks simple CSRF.
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/") \
                and request.content_length and not request.is_json:
            return jsonify(error="Expected JSON."), 415

    # ------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # ------------------------------------------------------------- auth
    @app.post("/api/register")
    def register():
        d = body()
        return jsonify(clinic.register_patient(d.get("name"), d.get("phone"), d.get("age"),
                                               d.get("password"))), 201

    @app.post("/api/login")
    def login():
        d = body()
        profile = clinic.login(d.get("user_id"), d.get("password"))
        session.clear()
        session["user_id"] = profile["user_id"]
        return jsonify(profile)

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/me")
    def me():
        user = current_user()
        if user is None:
            return jsonify(user=None)
        return jsonify(user=clinic.profile(user["user_id"]))

    @app.get("/api/meta")
    def meta():
        return jsonify(service_rates=clinic.settings["service_rates"], statuses=VALID_STATUSES, days=DAYS)

    # ---------------------------------------------------- patient: book
    @app.get("/api/specialties")
    @login_required("patient")
    def specialties(user):
        return jsonify(clinic.specialties())

    @app.get("/api/doctors/recommend")
    @login_required("patient")
    def recommend(user):
        return jsonify(clinic.recommend_doctors(request.args.get("specialty"), request.args.get("date")))

    @app.get("/api/slots")
    @login_required("patient")
    def slots(user):
        return jsonify(clinic.available_slots(request.args.get("doctor_id"), request.args.get("date")))

    @app.post("/api/appointments")
    @login_required("patient")
    def book(user):
        d = body()
        return jsonify(clinic.book_appointment(user["user_id"], d.get("doctor_id"), d.get("date"),
                                               d.get("time"), d.get("service_type"))), 201

    @app.get("/api/my/appointments")
    @login_required("patient")
    def my_appointments(user):
        return jsonify(clinic.patient_appointments(user["user_id"]))

    @app.get("/api/payments/pending")
    @login_required("patient")
    def pending(user):
        rows = clinic.pending_payments(user["user_id"])
        for r in rows:
            a = clinic.find_appointment(r["appointment_id"])
            if a:
                r["date"], r["time"] = a["date"], a["time"]
                doc = clinic.find_doctor(a["doctor_id"])
                r["doctor_name"] = doc["name"] if doc else a["doctor_id"]
        return jsonify(rows)

    @app.post("/api/payments/pay")
    @login_required("patient")
    def pay(user):
        return jsonify(clinic.pay(user["user_id"], body().get("payment_code")))

    @app.get("/api/my/history")
    @login_required("patient")
    def my_history(user):
        return jsonify(clinic.patient_history(user["user_id"]))

    # --------------------------------------------- shared appointment ops
    @app.post("/api/appointments/<aid>/cancel")
    @login_required("patient", "doctor", "admin")
    def cancel(user, aid):
        return jsonify(clinic.cancel_appointment(user, aid))

    @app.post("/api/appointments/<aid>/status")
    @login_required("nurse", "doctor")
    def set_status(user, aid):
        return jsonify(clinic.update_status(user, aid, body().get("status")))

    # ------------------------------------------------------------ nurse
    @app.get("/api/nurse/today")
    @login_required("nurse")
    def nurse_today(user):
        return jsonify(clinic.nurse_today(user["user_id"]))

    @app.get("/api/nurse/doctors")
    @login_required("nurse")
    def nurse_doctors(user):
        return jsonify(clinic.nurse_doctors(user["user_id"]))

    @app.post("/api/nurse/checkin")
    @login_required("nurse")
    def checkin(user):
        return jsonify(clinic.check_in(user["user_id"], body().get("confirmation_number")))

    @app.get("/api/queue")
    @login_required("nurse")
    def queue(user):
        return jsonify(clinic.waiting_queue_rows(emergency_only=request.args.get("emergency") == "1"))

    @app.get("/api/patients/search")
    @login_required("nurse", "doctor", "admin")
    def search(user):
        return jsonify(clinic.search_patients(request.args.get("q")))

    # ----------------------------------------------------------- doctor
    @app.get("/api/doctor/appointments")
    @login_required("doctor")
    def doctor_appts(user):
        return jsonify(clinic.doctor_appointments(user["user_id"], request.args.get("today") == "1"))

    @app.get("/api/doctor/queue")
    @login_required("doctor")
    def doctor_queue(user):
        return jsonify(clinic.waiting_queue_rows(doctor_id=user["user_id"]))

    @app.post("/api/doctor/start")
    @login_required("doctor")
    def start(user):
        return jsonify(clinic.start_visit(user["user_id"], body().get("appointment_id"))), 201

    @app.get("/api/doctor/visits")
    @login_required("doctor")
    def doctor_visits(user):
        return jsonify(clinic.doctor_visits(user["user_id"], request.args.get("open") == "1"))

    @app.post("/api/doctor/visits/<vid>/assess")
    @login_required("doctor")
    def assess(user, vid):
        d = body()
        return jsonify(clinic.assess(user["user_id"], vid, d.get("case_type"), d.get("diagnosis"),
                                     d.get("treatment"), d.get("follow_up")))

    @app.post("/api/doctor/visits/<vid>/complete")
    @login_required("doctor")
    def complete(user, vid):
        return jsonify(clinic.complete_visit(user["user_id"], vid))

    @app.post("/api/doctor/patients/<pid>/case-type")
    @login_required("doctor")
    def case_type(user, pid):
        return jsonify(clinic.set_case_type(pid.upper(), body().get("case_type")))

    @app.get("/api/patients/<pid>/history")
    @login_required("doctor")
    def history(user, pid):
        return jsonify(clinic.patient_history(pid.strip().upper()))

    # ------------------------------------------------------------ admin
    @app.get("/api/admin/patients")
    @login_required("admin")
    def a_patients(user):
        return jsonify(clinic.list_patients())

    @app.get("/api/admin/doctors")
    @login_required("admin")
    def a_doctors(user):
        return jsonify(clinic.list_doctors())

    @app.get("/api/admin/nurses")
    @login_required("admin")
    def a_nurses(user):
        return jsonify(clinic.list_nurses())

    @app.get("/api/admin/appointments")
    @login_required("admin")
    def a_appts(user):
        return jsonify(clinic.admin_appointments())

    @app.get("/api/admin/report")
    @login_required("admin")
    def a_report(user):
        return jsonify(clinic.report(request.args.get("date") or None))

    @app.get("/api/admin/revenue")
    @login_required("admin")
    def a_revenue(user):
        return jsonify(clinic.doctor_revenue())

    @app.get("/api/admin/history")
    @login_required("admin")
    def a_history(user):
        return jsonify(clinic.history_log())

    @app.post("/api/admin/doctors")
    @login_required("admin")
    def a_add_doctor(user):
        d = body()
        return jsonify(clinic.add_doctor(d.get("name"), d.get("phone"), d.get("specialty"), d.get("rate"),
                                         d.get("duration"), d.get("working_days"), d.get("start"),
                                         d.get("end"), d.get("password"))), 201

    @app.post("/api/admin/nurses")
    @login_required("admin")
    def a_add_nurse(user):
        d = body()
        return jsonify(clinic.add_nurse(d.get("name"), d.get("phone"), d.get("password"),
                                        d.get("doctor_id"))), 201

    @app.delete("/api/admin/patients/<pid>")
    @login_required("admin")
    def a_rm_patient(user, pid):
        clinic.remove_patient(pid.upper())
        return jsonify(ok=True)

    @app.delete("/api/admin/doctors/<did>")
    @login_required("admin")
    def a_rm_doctor(user, did):
        clinic.remove_doctor(did.upper())
        return jsonify(ok=True)

    @app.delete("/api/admin/nurses/<nid>")
    @login_required("admin")
    def a_rm_nurse(user, nid):
        clinic.remove_nurse(nid.upper())
        return jsonify(ok=True)

    return app


if __name__ == "__main__":
    create_app().run(host=os.environ.get("HOST", "127.0.0.1"),
                     port=int(os.environ.get("PORT", 5000)), debug=False)
