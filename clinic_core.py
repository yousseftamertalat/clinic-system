"""Clinic business logic, ported from the console ClinicSystem.

Data is stored in the same clinic_data.json layout as the original script,
so an existing file keeps working. Every public method raises ClinicError
(or a subclass) on a rule violation; the web layer turns those into 4xx
responses.
"""
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timedelta
from functools import reduce

from werkzeug.security import check_password_hash, generate_password_hash


# ---------------------------------------------------------------- errors
class ClinicError(Exception):
    pass


class ValidationError(ClinicError):
    pass


class AccessDeniedError(ClinicError):
    pass


class NotFoundError(ClinicError):
    pass


class InvalidAppointmentTimeError(ClinicError):
    pass


class DoctorUnavailableError(ClinicError):
    pass


class DuplicateBookingError(ClinicError):
    pass


class MissingPatientError(NotFoundError):
    pass


class MissingDoctorError(NotFoundError):
    pass


class InvalidPaymentError(ClinicError):
    pass


class InvalidCaseTypeError(ClinicError):
    pass


class VisitAlreadyCompletedError(ClinicError):
    pass


class PatientAlreadyCheckedInError(ClinicError):
    pass


# ------------------------------------------------------------- passwords
def hash_password(password):
    return generate_password_hash(password)


def verify_password(password, stored):
    """Accepts new werkzeug hashes and the original unsalted SHA-256 hashes."""
    if re.fullmatch(r"[0-9a-f]{64}", stored or ""):
        legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return secrets.compare_digest(legacy, stored)
    return check_password_hash(stored, password)


def is_legacy_hash(stored):
    return bool(re.fullmatch(r"[0-9a-f]{64}", stored or ""))


# --------------------------------------------------------- record classes
VALID_STATUSES = ["BOOKED", "CONFIRMED", "CHECKED_IN", "WAITING",
                  "IN_PROGRESS", "COMPLETED", "CANCELLED", "NO_SHOW"]
DAYS = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
PRIORITY_NAMES = {1: "EMERGENCY", 2: "URGENT", 3: "REGULAR"}


class Appointment:
    def __init__(self, appointment_id, patient_id, doctor_id, date, time,
                 service_type="consultation", status="BOOKED", payment_status="UNPAID",
                 case_type=None, priority=None, arrival_time=None):
        self.appointment_id = appointment_id
        self.patient_id = patient_id
        self.doctor_id = doctor_id
        self.date = date
        self.time = time
        self.service_type = service_type
        self.status = status
        self.payment_status = payment_status
        self.case_type = case_type
        self.priority = priority
        self.arrival_time = arrival_time

    def get_info(self):
        return dict(self.__dict__)


class Payment:
    def __init__(self, payment_code, confirmation_number, amount, status="PENDING",
                 appointment_id=None, patient_id=None):
        self.payment_code = payment_code
        self.confirmation_number = confirmation_number
        self.amount = amount
        self.status = status
        self.appointment_id = appointment_id
        self.patient_id = patient_id

    def process_payment(self):
        if self.amount <= 0:
            raise InvalidPaymentError("Invalid payment amount.")
        if self.status == "PAID":
            raise InvalidPaymentError("Payment has already been completed.")
        self.status = "PAID"
        if not self.confirmation_number:
            self.confirmation_number = secrets.randbelow(900000000) + 100000000
        return True

    def get_info(self):
        return dict(self.__dict__)


class Bill:
    def __init__(self, bill_id, patient_id, doctor_id, service_type, base_rate,
                 doctor_rate, total, payment_status="UNPAID", appointment_id=None):
        self.bill_id = bill_id
        self.patient_id = patient_id
        self.doctor_id = doctor_id
        self.service_type = service_type
        self.base_rate = base_rate
        self.doctor_rate = doctor_rate
        self.total = total
        self.payment_status = payment_status
        self.appointment_id = appointment_id

    def get_info(self):
        return dict(self.__dict__)


class Visit:
    def __init__(self, visit_id, patient_id, doctor_id, case_type=None, priority=None,
                 diagnosis=None, treatment=None, follow_up=None, status="WAITING"):
        self.visit_id = visit_id
        self.patient_id = patient_id
        self.doctor_id = doctor_id
        self.case_type = case_type
        self.priority = priority
        self.diagnosis = diagnosis
        self.treatment = treatment
        self.follow_up = follow_up
        self.status = status

    def complete_visit(self):
        if self.status == "COMPLETED":
            raise VisitAlreadyCompletedError("Visit is already completed.")
        self.status = "COMPLETED"

    def get_info(self):
        return dict(self.__dict__)


# ---------------------------------------------------------------- service
def _locked(method):
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class ClinicService:
    def __init__(self, file_name="clinic_data.json"):
        self.file_name = file_name
        self.lock = threading.RLock()
        self.users = []
        self.patients = []
        self.doctors = []
        self.nurses = []
        self.appointments = []
        self.visits = []
        self.bills = []
        self.payments = []
        self.waiting_queue = []
        self.action_history = []
        self.settings = {
            "service_rates": {"consultation": 2.5, "examination": 3.75, "emergency": 6.75},
            "case_priority": {"emergency": 1, "urgent": 2, "regular": 3},
        }
        self._load()
        self._create_default_admin()

    # ------------------------------------------------------- persistence
    def _load(self):
        try:
            with open(self.file_name, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            self._save()
            return
        except json.JSONDecodeError:
            # Keep the unreadable file instead of silently overwriting it.
            os.replace(self.file_name, self.file_name + ".corrupt")
            self._save()
            return
        for key in ("users", "patients", "doctors", "nurses", "appointments", "visits",
                    "bills", "payments", "waiting_queue", "action_history"):
            setattr(self, key, data.get(key, []))
        self.settings = data.get("settings", self.settings)
        for doctor in self.doctors:
            doctor.setdefault("appointment_duration", 30)
        for user in self.users:
            if user.get("password_hashed") is None and user.get("password"):
                user["password"] = hash_password(user["password"])
                user["password_hashed"] = True
        self._save()

    def _save(self):
        data = {k: getattr(self, k) for k in (
            "users", "patients", "doctors", "nurses", "appointments", "visits",
            "bills", "payments", "waiting_queue", "action_history", "settings")}
        directory = os.path.dirname(os.path.abspath(self.file_name))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, self.file_name)  # atomic: no half-written file

    def _log(self, message):
        self.action_history.append(message)

    def _create_default_admin(self):
        if any(u["user_id"] == "A001" for u in self.users):
            return
        self.users.append({"user_id": "A001", "name": "Clinic Admin", "phone": "01000000000",
                           "role": "admin", "password": hash_password("admin123"),
                           "password_hashed": True})
        self._save()

    # ---------------------------------------------------------- lookups
    @staticmethod
    def _find(items, key, value):
        for item in items:
            if item.get(key) == value:
                return item
        return None

    def find_user(self, uid): return self._find(self.users, "user_id", uid)
    def find_patient(self, pid): return self._find(self.patients, "patient_id", pid)
    def find_doctor(self, did): return self._find(self.doctors, "doctor_id", did)
    def find_nurse(self, nid): return self._find(self.nurses, "nurse_id", nid)
    def find_appointment(self, aid): return self._find(self.appointments, "appointment_id", aid)
    def find_visit(self, vid): return self._find(self.visits, "visit_id", vid)

    def _need_patient(self, pid):
        p = self.find_patient(pid)
        if p is None:
            raise MissingPatientError("Patient not found.")
        return p

    def _need_doctor(self, did):
        d = self.find_doctor(did)
        if d is None:
            raise MissingDoctorError("Doctor not found.")
        return d

    def _need_appointment(self, aid):
        a = self.find_appointment(aid)
        if a is None:
            raise NotFoundError("Appointment not found.")
        return a

    def _next_id(self, prefix, exists, start=1):
        n = start
        while exists(f"{prefix}{n:03d}"):
            n += 1
        return f"{prefix}{n:03d}"

    def _user_id(self, prefix):
        return self._next_id(prefix, lambda x: self.find_user(x) is not None)

    # -------------------------------------------------------- validation
    @staticmethod
    def valid_phone(phone):
        return re.fullmatch(r"01[0125]\d{8}", phone or "") is not None

    @staticmethod
    def _clean_name(value, label="Name"):
        value = (value or "").strip()
        if not value:
            raise ValidationError(f"{label} cannot be empty.")
        return value

    def _clean_phone(self, phone):
        phone = (phone or "").strip()
        if not self.valid_phone(phone):
            raise ValidationError("Invalid phone number. Use 11 digits starting with 010, 011, 012 or 015.")
        return phone

    @staticmethod
    def _clean_password(password):
        password = (password or "").strip()
        if len(password) < 4:
            raise ValidationError("Password must contain at least 4 characters.")
        return password

    @staticmethod
    def _clean_date(date):
        try:
            datetime.strptime(date or "", "%Y-%m-%d")
        except ValueError:
            raise ValidationError("Invalid date. Use YYYY-MM-DD.")
        return date

    # -------------------------------------------------------------- auth
    @_locked
    def login(self, user_id, password):
        user = self.find_user((user_id or "").strip().upper())
        if user is None or not verify_password((password or "").strip(), user["password"]):
            raise ClinicError("Wrong ID or password.")
        if is_legacy_hash(user["password"]):  # upgrade old SHA-256 hash on login
            user["password"] = hash_password((password or "").strip())
            self._save()
        return self.profile(user["user_id"])

    @_locked
    def profile(self, user_id):
        user = self.find_user(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        data = {"user_id": user["user_id"], "name": user["name"],
                "phone": user["phone"], "role": user["role"]}
        if user["role"] == "patient":
            p = self.find_patient(user_id)
            if p:
                data.update(age=p["age"], case_type=p.get("case_type"), priority=p.get("priority"),
                            appointment_count=len(p["appointments"]))
        elif user["role"] == "doctor":
            d = self.find_doctor(user_id)
            if d:
                data.update(specialty=d["specialty"], rate=d["rate"],
                            appointment_duration=d.get("appointment_duration", 30),
                            working_days=d["working_days"], working_hours=d["working_hours"])
        elif user["role"] == "nurse":
            n = self.find_nurse(user_id)
            if n:
                data.update(assigned_doctors=n["assigned_doctors"])
        return data

    # ------------------------------------------------------ registration
    @_locked
    def register_patient(self, name, phone, age, password):
        name = self._clean_name(name)
        phone = self._clean_phone(phone)
        try:
            age = int(age)
        except (TypeError, ValueError):
            raise ValidationError("Age must be a number.")
        if not 0 < age <= 120:
            raise ValidationError("Invalid age.")
        password = self._clean_password(password)
        pid = self._user_id("P")
        self.users.append({"user_id": pid, "name": name, "phone": phone, "role": "patient",
                           "password": hash_password(password), "password_hashed": True})
        self.patients.append({"patient_id": pid, "name": name, "age": age, "phone": phone,
                              "case_type": None, "priority": None, "appointments": []})
        self._log(f"Patient {pid} registered.")
        self._save()
        return {"patient_id": pid}

    @_locked
    def add_doctor(self, name, phone, specialty, rate, duration, working_days, start, end, password):
        name = self._clean_name(name, "Doctor name")
        phone = self._clean_phone(phone)
        specialty = self._clean_name(specialty, "Specialty")
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            raise ValidationError("Rate must be a number.")
        if rate <= 0:
            raise ValidationError("Rate must be greater than zero.")
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            raise ValidationError("Duration must be a number.")
        if duration <= 0:
            raise ValidationError("Duration must be greater than zero.")
        if not isinstance(working_days, list) or len(set(working_days)) != 3 \
                or any(d not in DAYS for d in working_days):
            raise ValidationError("Doctor must work exactly 3 different days.")
        time_re = r"(?:[01]\d|2[0-3]):[0-5]\d"
        if not re.fullmatch(time_re, start or ""):
            raise ValidationError("Invalid starting time. Use HH:MM.")
        if not re.fullmatch(time_re, end or ""):
            raise ValidationError("Invalid ending time. Use HH:MM.")
        if end <= start:
            raise ValidationError("Ending time must be after the starting time.")
        password = self._clean_password(password)
        did = self._user_id("D")
        self.users.append({"user_id": did, "name": name, "phone": phone, "role": "doctor",
                           "password": hash_password(password), "password_hashed": True})
        self.doctors.append({"doctor_id": did, "name": name, "specialty": specialty, "phone": phone,
                             "rate": rate, "appointment_duration": duration,
                             "working_days": [d for d in DAYS if d in working_days],
                             "working_hours": {"start": start, "end": end},
                             "availability": True, "assigned_nurses": []})
        self._log(f"Doctor {did} added.")
        self._save()
        return {"doctor_id": did}

    @_locked
    def add_nurse(self, name, phone, password, doctor_id):
        name = self._clean_name(name, "Nurse name")
        phone = self._clean_phone(phone)
        password = self._clean_password(password)
        if not self.doctors:
            raise DoctorUnavailableError("No doctors available. Add a doctor first.")
        doctor = self._need_doctor((doctor_id or "").strip().upper())
        nid = self._user_id("N")
        self.users.append({"user_id": nid, "name": name, "phone": phone, "role": "nurse",
                           "password": hash_password(password), "password_hashed": True})
        self.nurses.append({"nurse_id": nid, "name": name, "phone": phone,
                            "assigned_doctors": [doctor["doctor_id"]], "availability": True})
        doctor["assigned_nurses"].append(nid)
        self._log(f"Nurse {nid} added and assigned to doctor {doctor['doctor_id']}.")
        self._save()
        return {"nurse_id": nid, "doctor_id": doctor["doctor_id"]}

    # ------------------------------------------------------------- lists
    @_locked
    def list_patients(self):
        return [dict(p) for p in self.patients]

    @_locked
    def list_doctors(self):
        return [dict(d) for d in self.doctors]

    @_locked
    def list_nurses(self):
        return [dict(n) for n in self.nurses]

    @_locked
    def list_all_appointments(self):
        return sorted((dict(a) for a in self.appointments), key=lambda a: (a["date"], a["time"]))

    @_locked
    def search_patients(self, keyword):
        keyword = (keyword or "").strip().lower()
        return [dict(p) for p in self.patients
                if keyword in p["patient_id"].lower() or keyword in p["name"].lower()]

    # ------------------------------------------------------- scheduling
    @_locked
    def specialties(self):
        found = []
        for d in self.doctors:
            if d["availability"] and d["specialty"] not in found:
                found.append(d["specialty"])
        return found

    def _is_working(self, doctor, date):
        try:
            return datetime.strptime(date, "%Y-%m-%d").strftime("%A") in doctor["working_days"]
        except ValueError:
            return False

    def _load_count(self, doctor_id, date):
        return sum(1 for a in self.appointments
                   if a["doctor_id"] == doctor_id and a["date"] == date and a["status"] != "CANCELLED")

    @_locked
    def recommend_doctors(self, specialty, date):
        self._clean_date(date)
        result = []
        for d in self.doctors:
            if d["specialty"].lower() == (specialty or "").lower() and d["availability"] \
                    and self._is_working(d, date):
                result.append({"doctor": dict(d), "load": self._load_count(d["doctor_id"], date)})
        result.sort(key=lambda x: x["load"])
        return result

    def _slots(self, doctor, date):
        if not doctor["availability"] or not self._is_working(doctor, date):
            return []
        try:
            start = datetime.strptime(doctor["working_hours"]["start"], "%H:%M")
            end = datetime.strptime(doctor["working_hours"]["end"], "%H:%M")
        except (KeyError, ValueError):
            return []
        duration = doctor.get("appointment_duration", 30)
        booked = {a["time"] for a in self.appointments
                  if a["doctor_id"] == doctor["doctor_id"] and a["date"] == date
                  and a["status"] != "CANCELLED"}
        slots, current = [], start
        while current + timedelta(minutes=duration) <= end:
            t = current.strftime("%H:%M")
            if t not in booked:
                slots.append(t)
            current += timedelta(minutes=duration)
        return slots

    @_locked
    def available_slots(self, doctor_id, date):
        self._clean_date(date)
        return self._slots(self._need_doctor(doctor_id), date)

    # ----------------------------------------------------------- billing
    def _price(self, doctor, service_type):
        service_type = (service_type or "").lower()
        rates = self.settings["service_rates"]
        if service_type not in rates:
            raise InvalidPaymentError("Invalid service type.")
        base = rates[service_type]
        rate = float(doctor["rate"])
        return base, rate, round(base * rate, 3)

    def _bill_for(self, appointment):
        """The bill that belongs to this appointment (falls back to the old
        patient+doctor matching for bills created before appointment_id existed)."""
        for b in self.bills:
            if b.get("appointment_id") == appointment["appointment_id"]:
                return b
        for b in self.bills:
            if "appointment_id" not in b and b["patient_id"] == appointment["patient_id"] \
                    and b["doctor_id"] == appointment["doctor_id"] and b["payment_status"] == "UNPAID":
                return b
        return None

    def _payment_code(self):
        while True:
            code = str(secrets.randbelow(900000000000) + 100000000000)
            if not any(p["payment_code"] == code for p in self.payments):
                return code

    @_locked
    def book_appointment(self, patient_id, doctor_id, date, time, service_type):
        patient = self._need_patient(patient_id)
        self._clean_date(date)
        if datetime.strptime(date, "%Y-%m-%d").date() < datetime.now().date():
            raise InvalidAppointmentTimeError("You cannot book a past date.")
        doctor = self._need_doctor(doctor_id)
        if not doctor["availability"] or not self._is_working(doctor, date):
            raise DoctorUnavailableError("This doctor does not work on the selected date.")
        if time not in self._slots(doctor, date):
            raise DoctorUnavailableError("That time slot is no longer available.")
        if any(a["patient_id"] == patient_id and a["doctor_id"] == doctor_id and a["date"] == date
               and a["time"] == time and a["status"] != "CANCELLED" for a in self.appointments):
            raise DuplicateBookingError("Duplicate booking detected.")
        base, rate, total = self._price(doctor, service_type)
        service_type = service_type.lower()

        aid = self._next_id("AP", lambda x: self.find_appointment(x) is not None,
                            start=len(self.appointments) + 1)
        appt = Appointment(aid, patient_id, doctor_id, date, time, service_type).get_info()
        self.appointments.append(appt)
        patient["appointments"].append(aid)

        bid = self._next_id("B", lambda x: any(b["bill_id"] == x for b in self.bills),
                            start=len(self.bills) + 1)
        bill = Bill(bid, patient_id, doctor_id, service_type, base, rate, total,
                    appointment_id=aid).get_info()
        self.bills.append(bill)

        payment = Payment(self._payment_code(), None, bill["total"], "PENDING", aid, patient_id).get_info()
        self.payments.append(payment)
        appt["payment_status"] = "PENDING"
        appt["status"] = "CONFIRMED"

        self._log(f"Patient {patient_id} booked appointment {aid}.")
        self._save()
        return {"appointment": dict(appt), "bill": dict(bill), "payment": dict(payment),
                "doctor": {"name": doctor["name"], "specialty": doctor["specialty"]}}

    @_locked
    def pending_payments(self, patient_id):
        return [dict(p) for p in self.payments
                if p["patient_id"] == patient_id and p["status"] == "PENDING"]

    @_locked
    def pay(self, patient_id, payment_code):
        payment = next((p for p in self.payments
                        if p["patient_id"] == patient_id and p["status"] == "PENDING"
                        and p["payment_code"] == (payment_code or "").strip()), None)
        if payment is None:
            raise InvalidPaymentError("Invalid payment code.")
        obj = Payment(payment["payment_code"], payment["confirmation_number"], payment["amount"],
                      payment["status"], payment["appointment_id"], payment["patient_id"])
        obj.process_payment()
        payment.update(obj.get_info())
        appt = self.find_appointment(payment["appointment_id"])
        if appt:
            appt["payment_status"] = "PAID"
            appt["status"] = "CONFIRMED"
            bill = self._bill_for(appt)
            if bill:
                bill["payment_status"] = "PAID"
        self._log(f"Patient {patient_id} paid appointment {payment['appointment_id']}.")
        self._save()
        return {"confirmation_number": payment["confirmation_number"],
                "appointment_id": payment["appointment_id"], "amount": payment["amount"]}

    # ------------------------------------------------------ appointments
    def _decorate(self, a, with_patient=False, with_doctor=False):
        row = dict(a)
        if with_patient:
            p = self.find_patient(a["patient_id"])
            row["patient_name"] = p["name"] if p else a["patient_id"]
        if with_doctor:
            d = self.find_doctor(a["doctor_id"])
            row["doctor_name"] = d["name"] if d else a["doctor_id"]
            row["specialty"] = d["specialty"] if d else None
        return row

    @_locked
    def patient_appointments(self, patient_id):
        rows = []
        for a in self.appointments:
            if a["patient_id"] != patient_id:
                continue
            row = self._decorate(a, with_doctor=True)
            pay = next((p for p in self.payments if p["appointment_id"] == a["appointment_id"]), None)
            if pay:
                row["amount"] = pay["amount"]
                row["payment_code"] = pay["payment_code"] if pay["status"] == "PENDING" else None
                row["confirmation_number"] = pay["confirmation_number"] if pay["status"] == "PAID" else None
            rows.append(row)
        return sorted(rows, key=lambda a: (a["date"], a["time"]), reverse=True)

    @_locked
    def doctor_appointments(self, doctor_id, today_only=False):
        today = datetime.now().strftime("%Y-%m-%d")
        rows = [self._decorate(a, with_patient=True) for a in self.appointments
                if a["doctor_id"] == doctor_id and (not today_only or a["date"] == today)]
        return sorted(rows, key=lambda a: (a["date"], a["time"]))

    @_locked
    def nurse_today(self, nurse_id):
        nurse = self.find_nurse(nurse_id)
        if nurse is None:
            raise NotFoundError("Nurse not found.")
        today = datetime.now().strftime("%Y-%m-%d")
        rows = [self._decorate(a, with_patient=True, with_doctor=True) for a in self.appointments
                if a["doctor_id"] in nurse["assigned_doctors"] and a["date"] == today]
        return sorted(rows, key=lambda a: (a["doctor_id"], a["time"]))

    @_locked
    def admin_appointments(self):
        return [self._decorate(a, with_patient=True, with_doctor=True)
                for a in self.list_all_appointments()]

    def _drop_from_queue(self, appointment_id):
        self.waiting_queue = [q for q in self.waiting_queue if q["appointment_id"] != appointment_id]

    @_locked
    def cancel_appointment(self, user, appointment_id):
        appt = self._need_appointment((appointment_id or "").strip().upper())
        if user["role"] == "patient" and appt["patient_id"] != user["user_id"]:
            raise AccessDeniedError("Access denied.")
        if user["role"] == "doctor" and appt["doctor_id"] != user["user_id"]:
            raise AccessDeniedError("Access denied.")
        if appt["status"] == "CANCELLED":
            raise ClinicError("Appointment is already cancelled.")
        appt["status"] = "CANCELLED"
        self._drop_from_queue(appt["appointment_id"])
        self._log(f"Appointment {appt['appointment_id']} cancelled.")
        self._save()
        return dict(appt)

    @_locked
    def update_status(self, user, appointment_id, new_status):
        appt = self._need_appointment((appointment_id or "").strip().upper())
        new_status = (new_status or "").upper()
        if new_status not in VALID_STATUSES:
            raise InvalidAppointmentTimeError("Invalid appointment status.")
        if user["role"] == "doctor" and appt["doctor_id"] != user["user_id"]:
            raise AccessDeniedError("This appointment does not belong to you.")
        if user["role"] == "nurse":
            nurse = self.find_nurse(user["user_id"])
            if not nurse or appt["doctor_id"] not in nurse["assigned_doctors"]:
                raise AccessDeniedError("This appointment is not assigned to your doctors.")
        appt["status"] = new_status
        if new_status != "WAITING":
            self._drop_from_queue(appt["appointment_id"])
        self._log(f"Appointment {appt['appointment_id']} updated to {new_status}.")
        self._save()
        return dict(appt)

    # ----------------------------------------------- triage and queueing
    def _priority(self, case_type):
        rules = {"emergency": 1, "urgent": 2, "regular": 3}
        key = (case_type or "").lower()
        if key not in rules:
            raise InvalidCaseTypeError("Invalid case type.")
        return rules[key]

    def _sort_queue(self):
        self.waiting_queue.sort(key=lambda x: (x["priority"], x["arrival_time"]))

    @_locked
    def check_in(self, nurse_id, confirmation_number):
        confirmation = str(confirmation_number or "").strip()
        payment = next((p for p in self.payments
                        if str(p["confirmation_number"]) == confirmation and p["status"] == "PAID"), None)
        if payment is None or not confirmation:
            raise InvalidPaymentError("Payment confirmation is invalid.")
        appt = self.find_appointment(payment["appointment_id"])
        if appt is None:
            raise NotFoundError("Appointment not found.")
        if appt["payment_status"] != "PAID":
            raise InvalidPaymentError("Appointment is not paid.")
        if appt["status"] in ("CHECKED_IN", "WAITING"):
            raise PatientAlreadyCheckedInError("Patient already checked in.")
        if appt["status"] in ("CANCELLED", "COMPLETED", "IN_PROGRESS", "NO_SHOW"):
            raise ClinicError(f"Cannot check in an appointment that is {appt['status']}.")
        nurse = self.find_nurse(nurse_id)
        if nurse is None or appt["doctor_id"] not in nurse["assigned_doctors"]:
            raise AccessDeniedError("This patient is not assigned to your doctors.")
        patient = self._need_patient(appt["patient_id"])
        arrival = datetime.now().strftime("%H:%M:%S")
        appt["arrival_time"] = arrival
        appt["status"] = "WAITING"
        case_type = patient.get("case_type")
        if case_type:
            appt["case_type"], appt["priority"] = case_type, self._priority(case_type)
        else:
            appt["case_type"], appt["priority"] = "regular", 3
        if appt["appointment_id"] not in [q["appointment_id"] for q in self.waiting_queue]:
            self.waiting_queue.append({"appointment_id": appt["appointment_id"],
                                       "patient_id": appt["patient_id"], "doctor_id": appt["doctor_id"],
                                       "priority": appt["priority"], "arrival_time": arrival})
        self._sort_queue()
        self._log(f"Nurse {nurse_id} checked in patient {appt['patient_id']}.")
        self._save()
        return self._decorate(appt, with_patient=True)

    def _queue_rows(self, items):
        rows = []
        for q in items:
            p = self.find_patient(q["patient_id"])
            row = dict(q)
            row["patient_name"] = p["name"] if p else q["patient_id"]
            row["priority_name"] = PRIORITY_NAMES.get(q["priority"], "UNKNOWN")
            rows.append(row)
        return rows

    @_locked
    def waiting_queue_rows(self, emergency_only=False, doctor_id=None):
        self._sort_queue()
        items = [q for q in self.waiting_queue
                 if (not emergency_only or q["priority"] == 1)
                 and (doctor_id is None or q["doctor_id"] == doctor_id)]
        return self._queue_rows(items)

    @_locked
    def nurse_doctors(self, nurse_id):
        nurse = self.find_nurse(nurse_id)
        if nurse is None:
            raise NotFoundError("Nurse not found.")
        return [dict(d) for d in (self.find_doctor(i) for i in nurse["assigned_doctors"]) if d]

    # ------------------------------------------------------------ visits
    @_locked
    def set_case_type(self, patient_id, case_type):
        patient = self._need_patient(patient_id)
        priority = self._priority(case_type)
        patient["case_type"] = case_type.lower()
        patient["priority"] = priority
        self._save()
        return {"case_type": patient["case_type"], "priority": priority}

    @_locked
    def start_visit(self, doctor_id, appointment_id):
        appt = self._need_appointment((appointment_id or "").strip().upper())
        if appt["doctor_id"] != doctor_id:
            raise AccessDeniedError("This appointment does not belong to you.")
        if appt["status"] != "WAITING":
            raise ClinicError("Patient is not waiting.")
        patient = self._need_patient(appt["patient_id"])
        appt["status"] = "IN_PROGRESS"
        vid = self._next_id("V", lambda x: self.find_visit(x) is not None, start=len(self.visits) + 1)
        visit = Visit(vid, patient["patient_id"], doctor_id, patient.get("case_type"),
                      patient.get("priority"), None, None, None, "IN_PROGRESS").get_info()
        self.visits.append(visit)
        self._drop_from_queue(appt["appointment_id"])
        self._log(f"Doctor {doctor_id} started visit {vid}.")
        self._save()
        return dict(visit)

    @_locked
    def doctor_visits(self, doctor_id, open_only=False):
        rows = []
        for v in self.visits:
            if v["doctor_id"] != doctor_id or (open_only and v["status"] == "COMPLETED"):
                continue
            p = self.find_patient(v["patient_id"])
            row = dict(v)
            row["patient_name"] = p["name"] if p else v["patient_id"]
            rows.append(row)
        return rows

    @_locked
    def assess(self, doctor_id, visit_id, case_type, diagnosis, treatment, follow_up):
        visit = self.find_visit((visit_id or "").strip().upper())
        if visit is None:
            raise NotFoundError("Visit not found.")
        if visit["doctor_id"] != doctor_id:
            raise AccessDeniedError("Access denied.")
        if visit["status"] == "COMPLETED":
            raise VisitAlreadyCompletedError("Visit already completed.")
        info = self.set_case_type(visit["patient_id"], case_type)
        visit["case_type"], visit["priority"] = info["case_type"], info["priority"]
        visit["diagnosis"] = (diagnosis or "").strip()
        visit["treatment"] = (treatment or "").strip()
        visit["follow_up"] = (follow_up or "").strip()
        self._save()
        return dict(visit)

    @_locked
    def complete_visit(self, doctor_id, visit_id):
        visit = self.find_visit((visit_id or "").strip().upper())
        if visit is None:
            raise NotFoundError("Visit not found.")
        if visit["doctor_id"] != doctor_id:
            raise AccessDeniedError("Access denied.")
        obj = Visit(**{k: visit[k] for k in ("visit_id", "patient_id", "doctor_id", "case_type",
                                             "priority", "diagnosis", "treatment", "follow_up", "status")})
        obj.complete_visit()
        visit.update(obj.get_info())
        for a in self.appointments:
            if a["patient_id"] == visit["patient_id"] and a["doctor_id"] == visit["doctor_id"] \
                    and a["status"] == "IN_PROGRESS":
                a["status"] = "COMPLETED"
                break
        self._log(f"Doctor {doctor_id} completed visit {visit_id}.")
        self._save()
        return dict(visit)

    @_locked
    def patient_history(self, patient_id):
        self._need_patient(patient_id)
        rows = []
        for v in self.visits:
            if v["patient_id"] == patient_id:
                d = self.find_doctor(v["doctor_id"])
                row = dict(v)
                row["doctor_name"] = d["name"] if d else v["doctor_id"]
                rows.append(row)
        return rows

    # ----------------------------------------------------------- reports
    @_locked
    def report(self, report_date=None):
        report_date = report_date or datetime.now().strftime("%Y-%m-%d")
        self._clean_date(report_date)
        day = [a for a in self.appointments if a["date"] == report_date]
        paid_bills = [b for b in self.bills if b["payment_status"] == "PAID"]
        revenue = reduce(lambda x, y: x + y, map(lambda b: b["total"], paid_bills), 0)
        return {
            "date": report_date,
            "total_patients": len(self.patients),
            "completed_visits": sum(1 for v in self.visits if v["status"] == "COMPLETED"),
            "waiting_patients": sum(1 for a in day if a["status"] in ("WAITING", "CHECKED_IN")),
            "cancelled": sum(1 for a in day if a["status"] == "CANCELLED"),
            "emergency_cases": sum(1 for a in day if a.get("case_type") == "emergency"),
            "urgent_cases": sum(1 for a in day if a.get("case_type") == "urgent"),
            "regular_cases": sum(1 for a in day if a.get("case_type") in ("regular", None)),
            "doctors_working": sum(1 for d in self.doctors
                                   if d["availability"] and self._is_working(d, report_date)),
            "nurses_working": sum(1 for n in self.nurses if n["availability"]),
            "paid_appointments": sum(1 for a in day if a["payment_status"] == "PAID"),
            "unpaid_appointments": sum(1 for a in day if a["payment_status"] != "PAID"),
            "total_revenue": round(revenue, 3),
        }

    @_locked
    def doctor_revenue(self):
        rows = []
        for d in self.doctors:
            amounts = [b["total"] for b in self.bills
                       if b["doctor_id"] == d["doctor_id"] and b["payment_status"] == "PAID"]
            rows.append({"doctor_id": d["doctor_id"], "name": d["name"],
                         "specialty": d["specialty"], "revenue": round(sum(amounts), 3)})
        return rows

    @_locked
    def history_log(self):
        return list(self.action_history)

    # ----------------------------------------------------------- removal
    @_locked
    def remove_patient(self, patient_id):
        self._need_patient(patient_id)
        self.patients = [p for p in self.patients if p["patient_id"] != patient_id]
        self.users = [u for u in self.users if u["user_id"] != patient_id]
        self.waiting_queue = [q for q in self.waiting_queue if q["patient_id"] != patient_id]
        self._log(f"Patient {patient_id} removed.")
        self._save()

    @_locked
    def remove_doctor(self, doctor_id):
        self._need_doctor(doctor_id)
        self.doctors = [d for d in self.doctors if d["doctor_id"] != doctor_id]
        self.users = [u for u in self.users if u["user_id"] != doctor_id]
        self.waiting_queue = [q for q in self.waiting_queue if q["doctor_id"] != doctor_id]
        for n in self.nurses:
            if doctor_id in n["assigned_doctors"]:
                n["assigned_doctors"].remove(doctor_id)
        self._log(f"Doctor {doctor_id} removed.")
        self._save()

    @_locked
    def remove_nurse(self, nurse_id):
        if self.find_nurse(nurse_id) is None:
            raise NotFoundError("Nurse not found.")
        self.nurses = [n for n in self.nurses if n["nurse_id"] != nurse_id]
        self.users = [u for u in self.users if u["user_id"] != nurse_id]
        for d in self.doctors:
            if nurse_id in d["assigned_nurses"]:
                d["assigned_nurses"].remove(nurse_id)
        self._log(f"Nurse {nurse_id} removed.")
        self._save()
