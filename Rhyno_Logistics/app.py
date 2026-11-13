import hashlib
import json
import math
import mimetypes
import os
import secrets
import re
import smtplib
import ssl
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote, urlparse

import oracledb
import requests
from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, request, send_from_directory, send_file, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
ORACLE_USER = os.environ.get("ORACLE_USER")
ORACLE_PASSWORD = os.environ.get("ORACLE_PASSWORD")
ORACLE_DSN = os.environ.get("ORACLE_DSN")
FLASK_ENV = os.environ.get("FLASK_ENV", "development")
SECRET_KEY = os.environ.get("RHYNO_SECRET_KEY")
AMADEUS_CLIENT_ID = os.environ.get("AMADEUS_CLIENT_ID", "")
AMADEUS_CLIENT_SECRET = os.environ.get("AMADEUS_CLIENT_SECRET", "")
AMADEUS_API_BASE_URL = os.environ.get(
    "AMADEUS_API_BASE_URL", "https://test.api.amadeus.com"
).rstrip("/")
FLIGHT_SEARCH_MODE = os.environ.get("FLIGHT_SEARCH_MODE", "demo").strip().lower()
if FLIGHT_SEARCH_MODE not in {"demo", "amadeus"}:
    raise RuntimeError("FLIGHT_SEARCH_MODE must be either 'demo' or 'amadeus'.")
_AMADEUS_ACCESS_TOKEN = ""
_AMADEUS_ACCESS_TOKEN_EXPIRES_AT = 0
_AMADEUS_ACCESS_TOKEN_LOCK = threading.Lock()
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = os.environ.get("SMTP_PORT", "587")
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL", "")
CONTACT_RECIPIENT = os.environ.get("CONTACT_RECIPIENT", SMTP_FROM_EMAIL)
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "1") == "1"
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "")
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYSTACK_DEMO_MODE = os.environ.get("PAYSTACK_DEMO_MODE", "1").strip().lower() in {"1", "true", "yes", "on"}
PAYSTACK_API_BASE = "https://api.paystack.co"
PAYPAL_DEMO_MODE = os.environ.get("PAYPAL_DEMO_MODE", "1").strip().lower() in {"1", "true", "yes", "on"}
PAYPAL_MODE = os.environ.get("PAYPAL_MODE", "sandbox").strip().lower()
if PAYPAL_MODE not in {"sandbox", "live"}:
    raise RuntimeError("PAYPAL_MODE must be either sandbox or live.")
PAYPAL_API_BASE = (
    "https://api-m.sandbox.paypal.com" if PAYPAL_MODE == "sandbox"
    else "https://api-m.paypal.com"
)
AUTH_SESSION_MAX_AGE_SECONDS = int(timedelta(hours=8).total_seconds())
if FLASK_ENV == "production" and not SECRET_KEY:
    raise RuntimeError("RHYNO_SECRET_KEY must be configured in production.")
PUBLIC_BASE_PARSED = urlparse(PUBLIC_BASE_URL)
if PUBLIC_BASE_PARSED.scheme not in {"http", "https"} or not PUBLIC_BASE_PARSED.hostname:
    raise RuntimeError("PUBLIC_BASE_URL must be an absolute HTTP or HTTPS URL.")
if FLASK_ENV == "production" and PUBLIC_BASE_PARSED.scheme != "https":
    raise RuntimeError("PUBLIC_BASE_URL must use HTTPS in production.")
if PAYPAL_MODE == "live" and FLASK_ENV != "production":
    raise RuntimeError("Live PayPal mode is only allowed when FLASK_ENV=production.")

app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=SECRET_KEY or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=FLASK_ENV == "production",
    PERMANENT_SESSION_LIFETIME=timedelta(seconds=AUTH_SESSION_MAX_AGE_SECONDS),
    SESSION_REFRESH_EACH_REQUEST=False,
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
)

UPLOAD_DIR = BASE_DIR.parent / f".{BASE_DIR.name}-private-documents"
ALLOWED_DOCUMENT_TYPES = {
    ".pdf": (b"%PDF-",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
}
BOOKING_CATEGORIES = {"flight", "parcel", "clearing", "freight"}
BOOKING_STATUSES = {
    "Pending", "Pending payment", "Pending quotation", "Documents Received", "Under Review", "Approved",
    "Rejected", "Processing", "Cleared", "Completed", "Cancelled",
}
PUBLIC_FILES = {
    "index.html", "about.html", "services.html", "flights.html", "parcels.html",
    "clearing.html", "freight.html", "contact.html", "portal.html", "register.html",
    "assets/images/hero-illustration.svg", "assets/images/logo.svg", "assets/images/map-marker.svg",
    "assets/data/location-data.json",
    "css/style.css", "css/admin.css",
    "js/script.js", "js/site-shell.js", "js/portal.js", "js/register.js", "js/admin.js", "js/location-options.js", "js/booking-access.js",
}
LOGIN_ATTEMPTS = {}
FLIGHT_SEARCH_ATTEMPTS = {}
PASSWORD_RESET_ATTEMPTS = {}
CONTACT_ATTEMPTS = {}
REGISTRATION_ATTEMPTS = {}
LOGIN_ATTEMPTS_LOCK = threading.Lock()
MAX_RATE_LIMIT_KEYS = 10000


def now():
    return datetime.now(timezone.utc)


def db():
    if not ORACLE_USER or not ORACLE_PASSWORD or not ORACLE_DSN:
        raise RuntimeError("ORACLE_USER, ORACLE_PASSWORD, and ORACLE_DSN must be set.")
    return oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)


def rate_limited(bucket, limit, seconds):
    address = request.remote_addr or "unknown"
    current_time = time.time()
    cutoff = current_time - seconds
    with LOGIN_ATTEMPTS_LOCK:
        for key in list(bucket):
            attempts = [stamp for stamp in bucket[key] if stamp > cutoff]
            if attempts:
                bucket[key] = attempts
            else:
                del bucket[key]
        if address not in bucket and len(bucket) >= MAX_RATE_LIMIT_KEYS:
            oldest_key = min(bucket, key=lambda key: bucket[key][-1])
            del bucket[oldest_key]
        attempts = [stamp for stamp in bucket.get(address, []) if stamp > cutoff]
        if len(attempts) >= limit:
            bucket[address] = attempts
            return True
        attempts.append(current_time)
        bucket[address] = attempts
    return False


def smtp_configured():
    return bool(SMTP_HOST and SMTP_FROM_EMAIL)


def send_email(recipient, subject, body):
    if not smtp_configured():
        raise RuntimeError("SMTP_HOST, SMTP_FROM_EMAIL, and CONTACT_RECIPIENT must be configured.")
    try:
        port = int(SMTP_PORT)
    except ValueError as error:
        raise RuntimeError("SMTP_PORT must be a valid port number.") from error
    message = EmailMessage()
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(SMTP_HOST, port, timeout=15) as server:
        if SMTP_USE_TLS:
            server.starttls(context=ssl.create_default_context())
        if SMTP_USERNAME:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(message)


def paypal_access_token():
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise RuntimeError("PayPal sandbox credentials are not configured.")
    response = requests.post(
        f"{PAYPAL_API_BASE}/v1/oauth2/token",
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise ValueError("PayPal returned an invalid access token response.")
    return token


def paypal_request(method, path, access_token, body=None, request_id=None):
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if request_id:
        headers["PayPal-Request-Id"] = request_id
    response = requests.request(
        method,
        f"{PAYPAL_API_BASE}{path}",
        headers=headers,
        json=body,
        timeout=20,
    )
    response.raise_for_status()
    if response.status_code == 204:
        return {}
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("PayPal returned an invalid response.")
    return data


def normalized_money(value):
    try:
        raw_amount = Decimal(str(value))
        amount = raw_amount.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("The approved quotation has an invalid amount.")
    if not raw_amount.is_finite() or raw_amount != amount or amount <= 0:
        raise ValueError("The approved quotation has an invalid amount.")
    return format(amount, ".2f")


def paypal_approval_url(order):
    links = order.get("links") if isinstance(order, dict) else []
    for link in (links if isinstance(links, list) else []):
        if not isinstance(link, dict) or link.get("rel") not in {"approve", "payer-action"}:
            continue
        url = link.get("href")
        parsed = urlparse(url) if isinstance(url, str) else None
        if parsed and parsed.scheme == "https" and parsed.hostname and (
            parsed.hostname == "paypal.com" or parsed.hostname.endswith(".paypal.com")
        ):
            return url
    raise ValueError("PayPal did not return a valid approval link.")


def verify_paypal_purchase_unit(order, reference, amount, currency):
    units = order.get("purchase_units") if isinstance(order, dict) else None
    if not isinstance(units, list):
        return False
    for unit in units:
        if not isinstance(unit, dict) or str(unit.get("custom_id") or unit.get("reference_id") or "") != reference:
            continue
        details = unit.get("amount") or {}
        try:
            return (
                normalized_money(details.get("value")) == amount
                and details.get("currency_code") == currency
            )
        except (AttributeError, ValueError):
            return False
    return False


def verify_paypal_capture(order, reference, amount, currency):
    units = order.get("purchase_units") if isinstance(order, dict) else None
    if not isinstance(units, list):
        return False
    for unit in units:
        if not isinstance(unit, dict) or str(unit.get("custom_id") or unit.get("reference_id") or "") != reference:
            continue
        payments = unit.get("payments") or {}
        captures = payments.get("captures", []) if isinstance(payments, dict) else []
        for capture in captures if isinstance(captures, list) else []:
            details = capture.get("amount") or {} if isinstance(capture, dict) else {}
            try:
                if (
                    capture.get("status") == "COMPLETED"
                    and normalized_money(details.get("value")) == amount
                    and details.get("currency_code") == currency
                ):
                    return True
            except (AttributeError, ValueError):
                continue
    return False


def paypal_capture_statuses(order, reference):
    units = order.get("purchase_units") if isinstance(order, dict) else None
    if not isinstance(units, list):
        return []
    for unit in units:
        if not isinstance(unit, dict) or str(unit.get("custom_id") or unit.get("reference_id") or "") != reference:
            continue
        payments = unit.get("payments") or {}
        captures = payments.get("captures", []) if isinstance(payments, dict) else []
        if not isinstance(captures, list):
            return []
        return [
            capture.get("status")
            for capture in captures
            if isinstance(capture, dict) and isinstance(capture.get("status"), str)
        ]
    return []


def paystack_request(method, path, body=None):
    if not PAYSTACK_SECRET_KEY:
        raise RuntimeError("Paystack credentials are not configured.")
    response = requests.request(
        method,
        f"{PAYSTACK_API_BASE}{path}",
        headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}", "Accept": "application/json"},
        json=body,
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(result, dict) or result.get("status") is not True or not isinstance(data, dict):
        raise ValueError("Paystack returned an invalid response.")
    return data


def verify_paystack_transaction(transaction, reference, booking_reference, amount):
    metadata = transaction.get("metadata") if isinstance(transaction, dict) else None
    return (
        transaction.get("reference") == reference
        and transaction.get("status") == "success"
        and transaction.get("currency") == "GHS"
        and transaction.get("amount") == int(Decimal(amount) * 100)
        and transaction.get("channel") == "mobile_money"
        and isinstance(metadata, dict)
        and metadata.get("booking_reference") == booking_reference
    )


def to_dict(cur, row):
    if row is None:
        return None
    cols = [d[0].lower() for d in cur.description]
    return {key: lob_to_str(value) for key, value in zip(cols, row)}


def to_dicts(cur):
    cols = [d[0].lower() for d in cur.description]
    return [
        {key: lob_to_str(value) for key, value in zip(cols, row)}
        for row in cur.fetchall()
    ]


def lob_to_str(value):
    if isinstance(value, oracledb.LOB):
        return value.read()
    return value


def init_db():
    """Optionally seed an administrator from explicit environment credentials."""
    conn = db()
    try:
        with conn.cursor() as cur:
            email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
            password = os.environ.get("ADMIN_PASSWORD")
            if bool(email) != bool(password):
                raise RuntimeError("Set both ADMIN_EMAIL and ADMIN_PASSWORD to seed the administrator.")
            if email and password:
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(password) < 12:
                    raise RuntimeError("The administrator email must be valid and ADMIN_PASSWORD must have at least 12 characters.")
                cur.execute("SELECT id FROM administrators WHERE email = :1", (email,))
                if cur.fetchone():
                    return
                cur.execute(
                    "INSERT INTO administrators (name, email, password_hash, created_at) VALUES (:1, :2, :3, :4)",
                    ("Rhyno Administrator", email, generate_password_hash(password), now()),
                )
        conn.commit()
    finally:
        conn.close()


def payload():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def customer_id():
    return session.get("customer_id")


def admin_id():
    return session.get("admin_id")


def login_rate_limited(identifier):
    address = request.remote_addr or "unknown"
    identity_hash = hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()
    keys = (f"ip:{address}", f"identity:{identity_hash}")
    cutoff = time.time() - 900
    with LOGIN_ATTEMPTS_LOCK:
        for key in list(LOGIN_ATTEMPTS):
            LOGIN_ATTEMPTS[key] = [stamp for stamp in LOGIN_ATTEMPTS[key] if stamp > cutoff]
            if not LOGIN_ATTEMPTS[key]:
                del LOGIN_ATTEMPTS[key]
        if any(len(LOGIN_ATTEMPTS.get(key, [])) >= 5 for key in keys):
            return True
        current_time = time.time()
        for key in keys:
            LOGIN_ATTEMPTS.setdefault(key, []).append(current_time)
        while len(LOGIN_ATTEMPTS) > MAX_RATE_LIMIT_KEYS:
            oldest_key = min(LOGIN_ATTEMPTS, key=lambda key: LOGIN_ATTEMPTS[key][-1])
            del LOGIN_ATTEMPTS[oldest_key]
    return False


def clear_login_attempts(identifier):
    address = request.remote_addr or "unknown"
    identity_hash = hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()
    with LOGIN_ATTEMPTS_LOCK:
        LOGIN_ATTEMPTS.pop(f"ip:{address}", None)
        LOGIN_ATTEMPTS.pop(f"identity:{identity_hash}", None)


def flight_search_rate_limited():
    address = request.remote_addr or "unknown"
    cutoff = time.time() - 900
    with LOGIN_ATTEMPTS_LOCK:
        attempts = [stamp for stamp in FLIGHT_SEARCH_ATTEMPTS.get(address, []) if stamp > cutoff]
        if len(attempts) >= 15:
            FLIGHT_SEARCH_ATTEMPTS[address] = attempts
            return True
        attempts.append(time.time())
        FLIGHT_SEARCH_ATTEMPTS[address] = attempts
    return False


def new_reference(prefix):
    return f"RHY-{prefix}-{secrets.token_hex(4).upper()}"


def decode_payload(value):
    raw = lob_to_str(value) or "{}"
    return json.loads(raw)


def parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Provide a valid ISO-8601 date and time.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def positive_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def positive_integer(value):
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number.is_integer() and number >= 1


def one_of(value, choices):
    return isinstance(value, str) and value in choices


def validate_booking(category, data):
    if not isinstance(category, str) or category not in BOOKING_CATEGORIES or not isinstance(data, dict):
        raise ValueError("A valid booking category and booking details are required.")
    serialized = json.dumps(data)
    if len(serialized) > 20000:
        raise ValueError("Booking details are too large.")
    customer = data.get("sender") if category == "parcel" else data.get("customer")
    email = str(customer.get("email", "")).strip().lower() if isinstance(customer, dict) else ""
    if not email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("A valid customer email address is required.")
    if not isinstance(customer.get("name"), str) or not customer["name"].strip() or len(customer["name"]) > 255:
        raise ValueError("A valid customer name is required.")
    if len(email) > 255 or len(str(customer.get("phone", ""))) > 50:
        raise ValueError("Contact details exceed the allowed length.")
    def check_text_lengths(value):
        if isinstance(value, dict):
            for item in value.values():
                check_text_lengths(item)
        elif isinstance(value, list):
            for item in value:
                check_text_lengths(item)
        elif isinstance(value, str) and len(value) > 4000:
            raise ValueError("A booking field exceeds the maximum length.")
    check_text_lengths(data)
    if category == "flight":
        flight = data.get("flight")
        if not isinstance(flight, dict):
            raise ValueError("Flight details are required.")
        provider = flight.get("provider")
        offer_id = str(flight.get("offerId", ""))
        if provider == "Demo":
            if FLIGHT_SEARCH_MODE != "demo" or not re.fullmatch(r"demo-\d{3}", offer_id):
                raise ValueError("Select a demo itinerary from the current search before submitting a test request.")
            flight["demoMode"] = True
        elif provider == "Amadeus":
            if FLIGHT_SEARCH_MODE != "amadeus" or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", offer_id):
                raise ValueError("Select an Amadeus offer from the current search before submitting a request.")
            flight["demoMode"] = False
        else:
            raise ValueError("Select a flight offer from the current search before submitting a request.")
        origin = str(flight.get("origin", "")).strip()
        destination = str(flight.get("destination", "")).strip()
        if not origin or not destination or origin.casefold() == destination.casefold():
            raise ValueError("Departure and destination must be different locations.")
        try:
            departure = datetime.fromisoformat(str(flight.get("departureDate", "")))
        except (TypeError, ValueError):
            raise ValueError("Provide a valid departure date.")
        if departure.date() < now().date():
            raise ValueError("Departure date must be today or later.")
        trip_type = flight.get("tripType", "round-trip")
        return_date = flight.get("returnDate")
        if trip_type == "round-trip":
            try:
                parsed_return = datetime.fromisoformat(str(return_date))
            except (TypeError, ValueError):
                raise ValueError("Provide a valid return date.")
            if not return_date or parsed_return.date() < departure.date():
                raise ValueError("Return date must be on or after departure.")
        elif trip_type != "one-way":
            raise ValueError("Choose one-way or round-trip travel.")
        if not one_of(flight.get("travelClass"), {"Economy", "Business", "First Class"}):
            raise ValueError("Choose a valid travel class.")
        if not positive_integer(flight.get("passengers", 0)):
            raise ValueError("Passenger count must be between 1 and 9.")
        passengers = int(float(flight["passengers"]))
        if passengers < 1 or passengers > 9:
            raise ValueError("Passenger count must be between 1 and 9.")
        if not data.get("customer", {}).get("name"):
            raise ValueError("Passenger name is required.")
    else:
        required = {
            "parcel": ("sender", "recipient", "parcel"),
            "clearing": ("customer", "shipment"),
            "freight": ("customer", "cargo"),
        }[category]
        if any(not isinstance(data.get(key), dict) for key in required):
            raise ValueError(f"Required {category} details are missing.")
        if category == "parcel":
            sender, recipient, parcel = data["sender"], data["recipient"], data["parcel"]
            if not sender.get("name") or not isinstance(recipient.get("name"), str) or not recipient["name"].strip() or not parcel.get("description"):
                raise ValueError("Sender, recipient, and parcel description are required.")
            if not positive_number(parcel.get("weight")) or not positive_integer(parcel.get("quantity")):
                raise ValueError("Parcel weight and quantity must be greater than zero.")
            recipient_email = str(recipient.get("email", "")).strip().lower()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient_email):
                raise ValueError("A valid recipient email address is required.")
            if not one_of(data.get("option"), {"standard", "express", "international", "domestic"}):
                raise ValueError("Choose a valid delivery option.")
            if not one_of(parcel.get("deliveryType"), {"standard", "express", "international", "domestic"}):
                raise ValueError("Choose a valid parcel delivery type.")
        if category == "clearing":
            if not data["customer"].get("name") or not data["shipment"].get("description"):
                raise ValueError("Customer name and shipment description are required.")
            if not one_of(data["shipment"].get("direction"), {"Import", "Export"}):
                raise ValueError("Choose a valid shipment direction.")
            if not one_of(data["shipment"].get("method"), {"Sea freight", "Air freight", "Road freight", "Multimodal"}):
                raise ValueError("Choose a valid clearing shipping method.")
        if category == "freight":
            cargo = data["cargo"]
            if not data["customer"].get("name") or not isinstance(cargo.get("origin"), str) or not cargo["origin"].strip() or not isinstance(cargo.get("destination"), str) or not cargo["destination"].strip():
                raise ValueError("Customer name and cargo route are required.")
            if not positive_number(cargo.get("weight")) or not positive_integer(cargo.get("quantity")):
                raise ValueError("Cargo weight and quantity must be greater than zero.")
            if cargo["origin"].strip().casefold() == cargo["destination"].strip().casefold():
                raise ValueError("Cargo origin and destination must be different.")
            dimensions = re.split(r"[x×]", str(cargo.get("dimensions", "")), flags=re.IGNORECASE)
            if len(dimensions) != 3 or any(not positive_number(part.strip()) for part in dimensions):
                raise ValueError("Cargo dimensions must be three positive measurements in centimetres.")
            try:
                preferred_date = datetime.fromisoformat(str(cargo.get("date", ""))).date()
            except (TypeError, ValueError):
                raise ValueError("Provide a valid preferred shipping date.")
            if preferred_date < now().date():
                raise ValueError("Preferred shipping date must be today or later.")
            if not one_of(cargo.get("method"), {"Air freight", "Sea freight", "Road freight"}):
                raise ValueError("Choose a valid freight shipping method.")
    return data


def normalize_booking_rows(rows):
    bookings = []
    for row in rows:
        row.pop("id", None)
        row.pop("customer_id", None)
        if isinstance(row.get("created_at"), datetime):
            row["created_at"] = row["created_at"].isoformat()
        if row.get("quote_amount") is not None:
            row["quote_amount"] = str(row["quote_amount"])
        row["payload"] = decode_payload(row.get("payload"))
        bookings.append(row)
    return bookings


def validate_document(file_storage):
    filename = secure_filename(file_storage.filename or "")
    extension = Path(filename).suffix.lower()
    if not filename or len(filename) > 255 or extension not in ALLOWED_DOCUMENT_TYPES:
        raise ValueError("Upload only PDF, JPG, or PNG documents.")
    content = file_storage.read(10 * 1024 * 1024 + 1)
    file_storage.stream.seek(0)
    if not content or len(content) > 10 * 1024 * 1024:
        raise ValueError("Each document must be smaller than 10 MB.")
    if not any(content.startswith(signature) for signature in ALLOWED_DOCUMENT_TYPES[extension]):
        raise ValueError(f"The file content does not match its {extension} extension.")
    return filename, extension, content


@app.before_request
def enforce_csrf():
    if request.path.startswith("/api/") and customer_id():
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT active, session_version FROM customers WHERE id = :1", (customer_id(),))
                customer = cur.fetchone()
        finally:
            conn.close()
        if (
            not customer
            or customer[0] != 1
            or session.get("customer_session_version") != customer[1]
        ):
            session.clear()
            return jsonify(error="Customer session is no longer valid. Sign in again."), 401
    if request.path.startswith("/api/") and admin_id():
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT session_version FROM administrators WHERE id = :1",
                    (admin_id(),),
                )
                administrator = cur.fetchone()
        finally:
            conn.close()
        if not administrator or session.get("admin_session_version") != administrator[0]:
            session.clear()
            return jsonify(error="Administrator session is no longer valid. Sign in again."), 401
    if request.method in {"GET", "HEAD", "OPTIONS"} or not request.path.startswith("/api/"):
        return None
    token = session.get("csrf_token")
    if not token or request.headers.get("X-CSRF-Token") != token:
        return jsonify(error="Security token expired. Refresh the page and try again."), 403
    return None


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="The upload is too large. Keep each file under 10 MB and the complete request under 25 MB."), 413


@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/admin")
def admin_console():
    return send_from_directory(BASE_DIR, "admin.html")


@app.get("/<path:filename>")
def public_file(filename):
    if filename not in PUBLIC_FILES:
        abort(404)
    return send_from_directory(BASE_DIR, filename)


@app.get("/api/csrf-token")
def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return jsonify(token=token)


@app.post("/api/auth/register")
def register():
    data = payload()
    if not all(isinstance(data.get(field), str) and data[field].strip() for field in ("name", "email", "phone", "password")):
        return jsonify(error="Name, email, phone, and password are required."), 400
    if len(data["name"].strip()) > 255 or len(data["phone"].strip()) > 50 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", data["email"].strip()):
        return jsonify(error="Enter a valid name, email, and phone number."), 400
    if len(data["password"]) < 10:
        return jsonify(error="Use a password with at least 10 characters."), 400
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customers WHERE email = :1", (data["email"].strip().lower(),))
            if cur.fetchone():
                return jsonify(error="An account already exists for that email."), 409
            new_id = cur.var(oracledb.NUMBER)
            cur.execute(
                "INSERT INTO customers (name, email, phone, password_hash, created_at) VALUES (:1, :2, :3, :4, :5) RETURNING id INTO :6",
                (data["name"].strip(), data["email"].strip().lower(), data["phone"].strip(), generate_password_hash(data["password"]), now(), new_id),
            )
            cid = int(new_id.getvalue()[0])
        conn.commit()
        session.clear()
        session["customer_id"] = cid
        session["customer_session_version"] = 0
        session.permanent = True
        return jsonify(message="Account created.", customer={
            "id": cid, "name": data["name"].strip(), "email": data["email"].strip().lower(),
            "phone": data["phone"].strip(),
        })
    finally:
        conn.close()


@app.post("/api/contact")
def contact():
    if rate_limited(CONTACT_ATTEMPTS, 5, 3600):
        return jsonify(error="Too many contact submissions. Try again later."), 429
    data = payload()
    if (
        not all(isinstance(data.get(field), str) for field in ("name", "email", "message"))
    ):
        return jsonify(error="Enter a valid name, email address, and message."), 400
    name = data["name"].strip()
    email = data["email"].strip().lower()
    message = data["message"].strip()
    if (
        not name or len(name) > 255 or len(email) > 255
        or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
        or not message or len(message) > 5000
    ):
        return jsonify(error="Enter a valid name, email address, and message (up to 5,000 characters)."), 400
    if not CONTACT_RECIPIENT or not smtp_configured():
        return jsonify(error="Contact delivery is not configured. Please try again later."), 503
    try:
        send_email(
            CONTACT_RECIPIENT,
            "Rhyno Logistics website enquiry",
            f"From: {name}\nEmail: {email}\n\n{message}",
        )
    except (OSError, RuntimeError, smtplib.SMTPException, ValueError) as error:
        app.logger.warning("Contact message delivery failed (%s).", type(error).__name__)
        return jsonify(error="We could not deliver your message. Please try again later."), 502
    return jsonify(message="Your enquiry was sent. Our team will be in touch."), 201


@app.post("/api/auth/password-reset/request")
def request_password_reset():
    if rate_limited(PASSWORD_RESET_ATTEMPTS, 3, 900):
        return jsonify(error="Too many password-reset requests. Try again in 15 minutes."), 429
    email_value = payload().get("email")
    if not isinstance(email_value, str):
        return jsonify(error="Enter a valid email address."), 400
    email = email_value.strip().lower()
    if len(email) > 255 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify(error="Enter a valid email address."), 400
    if not smtp_configured():
        return jsonify(error="Password reset email is not configured. Please contact support."), 503

    reset_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(reset_token.encode("utf-8")).hexdigest()
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM customers WHERE email = :1 AND active = 1", (email,))
            customer = cur.fetchone()
            if customer:
                cur.execute(
                    "UPDATE customers SET password_reset_token_hash = :1, password_reset_expires_at = :2 WHERE id = :3",
                    (token_hash, now() + timedelta(minutes=30), customer[0]),
                )
        conn.commit()
    finally:
        conn.close()

    if customer:
        reset_url = f"{PUBLIC_BASE_URL}/portal.html#reset_token={quote(reset_token, safe='')}"
        try:
            send_email(
                email,
                "Reset your Rhyno Logistics password",
                f"Hello {customer[1]},\n\nUse this one-time link within 30 minutes to reset your password:\n{reset_url}\n\nIf you did not request this, you can ignore this message.",
            )
        except (OSError, RuntimeError, smtplib.SMTPException, ValueError) as error:
            conn = db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE customers SET password_reset_token_hash = NULL, password_reset_expires_at = NULL WHERE id = :1 AND password_reset_token_hash = :2",
                        (customer[0], token_hash),
                    )
                conn.commit()
            finally:
                conn.close()
            app.logger.warning("Password reset email delivery failed (%s).", type(error).__name__)
    return jsonify(message="If an active account matches that email, password-reset instructions will be sent."), 200


@app.post("/api/auth/password-reset/confirm")
def confirm_password_reset():
    data = payload()
    token = data.get("token")
    new_password = data.get("new_password")
    if not isinstance(token, str) or not token or len(token) > 200:
        return jsonify(error="The reset link is invalid or expired."), 400
    if not isinstance(new_password, str) or len(new_password) < 10:
        return jsonify(error="Use a new password with at least 10 characters."), 400
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM customers WHERE password_reset_token_hash = :1 AND password_reset_expires_at > :2 AND active = 1 FOR UPDATE",
                (token_hash, now()),
            )
            customer = cur.fetchone()
            if not customer:
                return jsonify(error="The reset link is invalid or expired."), 400
            cur.execute(
                "UPDATE customers SET password_hash = :1, password_reset_token_hash = NULL, password_reset_expires_at = NULL, session_version = session_version + 1 WHERE id = :2",
                (generate_password_hash(new_password), customer[0]),
            )
        conn.commit()
    finally:
        conn.close()
    session.clear()
    return jsonify(message="Password reset. Sign in with your new password.")


@app.post("/api/auth/login")
def login():
    data = payload()
    email = (data.get("email") or "").strip().lower() if isinstance(data.get("email"), str) else ""
    password = data.get("password") if isinstance(data.get("password"), str) else ""
    if login_rate_limited(email):
        return jsonify(error="Too many sign-in attempts. Try again in 15 minutes."), 429
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, email, phone, password_hash, active, session_version FROM customers WHERE email = :1", (email,))
            customer = to_dict(cur, cur.fetchone())
    finally:
        conn.close()
    if not customer or customer["active"] != 1 or not check_password_hash(customer["password_hash"], password):
        return jsonify(error="Invalid email or password."), 401
    clear_login_attempts(email)
    session.clear()
    session["customer_id"] = customer["id"]
    session["customer_session_version"] = customer["session_version"]
    session.permanent = True
    return jsonify(customer={k: customer[k] for k in ("id", "name", "email", "phone")})


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify(message="Signed out.")


@app.get("/api/notifications")
def notifications():
    if not customer_id():
        return jsonify(error="Sign in required."), 401
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, message, read_at, created_at FROM notifications WHERE customer_id = :1 ORDER BY id DESC", (customer_id(),))
            items = to_dicts(cur)
    finally:
        conn.close()
    for item in items:
        for key in ("read_at", "created_at"):
            if isinstance(item.get(key), datetime):
                item[key] = item[key].isoformat()
    return jsonify(notifications=items)


@app.patch("/api/notifications/<int:notification_id>")
def mark_notification_read(notification_id):
    if not customer_id():
        return jsonify(error="Sign in required."), 401
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE notifications SET read_at = :1 WHERE id = :2 AND customer_id = :3",
                (now(), notification_id, customer_id()))
            if not cur.rowcount:
                return jsonify(error="Notification not found."), 404
        conn.commit()
    finally:
        conn.close()
    return jsonify(message="Notification marked as read.")


@app.patch("/api/bookings/<reference>/cancel")
def cancel_booking(reference):
    if not customer_id():
        return jsonify(error="Sign in required."), 401
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, status FROM bookings WHERE reference = :1 AND customer_id = :2",
                (reference.upper(), customer_id()))
            booking = cur.fetchone()
            if not booking:
                return jsonify(error="Booking not found."), 404
            if booking[1] not in {"Pending", "Pending payment", "Pending quotation", "Documents Received", "Approved"}:
                return jsonify(error="This booking can no longer be cancelled online."), 409
            cur.execute("UPDATE bookings SET status = 'Cancelled' WHERE id = :1", (booking[0],))
            cur.execute(
                "UPDATE payments SET status = 'CANCELLED', updated_at = :1 WHERE booking_id = :2 AND status = 'PENDING'",
                (now(), booking[0]),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify(message="Booking cancelled.", status="Cancelled")


@app.get("/api/profile")
def profile():
    if not customer_id():
        return jsonify(error="Sign in required."), 401
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, email, phone, created_at FROM customers WHERE id = :1", (customer_id(),))
            customer = to_dict(cur, cur.fetchone())
    finally:
        conn.close()
    if not customer:
        session.clear()
        return jsonify(error="Customer account not found."), 404
    if isinstance(customer.get("created_at"), datetime):
        customer["created_at"] = customer["created_at"].isoformat()
    return jsonify(customer=customer)


@app.get("/api/auth/status")
def auth_status():
    return jsonify(authenticated=bool(customer_id()))


@app.patch("/api/profile")
def update_profile():
    if not customer_id():
        return jsonify(error="Sign in required."), 401
    data = payload()
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    phone = str(data.get("phone", "")).strip()
    if not name or len(name) > 255 or not phone or len(phone) > 50 or len(email) > 255 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify(error="Name, valid email address, and phone number are required."), 400
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customers WHERE email = :1 AND id <> :2", (email, customer_id()))
            if cur.fetchone():
                return jsonify(error="That email address is already associated with an account."), 409
            cur.execute("UPDATE customers SET name = :1, email = :2, phone = :3 WHERE id = :4",
                (name, email, phone, customer_id()))
        conn.commit()
    finally:
        conn.close()
    return jsonify(message="Profile updated.", customer={"id": customer_id(), "name": name, "email": email, "phone": phone})


@app.post("/api/profile/password")
def change_password():
    if not customer_id():
        return jsonify(error="Sign in required."), 401
    data = payload()
    current_password = data.get("current_password", "")
    new_password = data.get("new_password", "")
    if not isinstance(current_password, str):
        return jsonify(error="Current password is required."), 400
    if not isinstance(new_password, str) or len(new_password) < 10:
        return jsonify(error="Use a new password with at least 10 characters."), 400
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM customers WHERE id = :1 FOR UPDATE", (customer_id(),))
            row = cur.fetchone()
            if not row or not check_password_hash(row[0], current_password):
                return jsonify(error="Current password is incorrect."), 400
            cur.execute("UPDATE customers SET password_hash = :1, session_version = session_version + 1 WHERE id = :2",
                (generate_password_hash(new_password), customer_id()))
        conn.commit()
    finally:
        conn.close()
    return jsonify(message="Password changed.")


@app.post("/api/admin/login")
def admin_login():
    data = payload()
    email = (data.get("email") or "").strip().lower() if isinstance(data.get("email"), str) else ""
    password = data.get("password") if isinstance(data.get("password"), str) else ""
    if login_rate_limited(email):
        return jsonify(error="Too many sign-in attempts. Try again in 15 minutes."), 429
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, password_hash, session_version FROM administrators WHERE email = :1", (email,))
            administrator = to_dict(cur, cur.fetchone())
    finally:
        conn.close()
    if not administrator or not check_password_hash(administrator["password_hash"], password):
        return jsonify(error="Invalid administrator credentials."), 401
    clear_login_attempts(email)
    session.clear()
    session["admin_id"] = administrator["id"]
    session["admin_session_version"] = administrator["session_version"]
    session.permanent = True
    return jsonify(message="Administrator signed in.")


def amadeus_access_token():
    global _AMADEUS_ACCESS_TOKEN, _AMADEUS_ACCESS_TOKEN_EXPIRES_AT

    if _AMADEUS_ACCESS_TOKEN and time.monotonic() < _AMADEUS_ACCESS_TOKEN_EXPIRES_AT:
        return _AMADEUS_ACCESS_TOKEN
    with _AMADEUS_ACCESS_TOKEN_LOCK:
        if _AMADEUS_ACCESS_TOKEN and time.monotonic() < _AMADEUS_ACCESS_TOKEN_EXPIRES_AT:
            return _AMADEUS_ACCESS_TOKEN
        response = requests.post(
            f"{AMADEUS_API_BASE_URL}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": AMADEUS_CLIENT_ID,
                "client_secret": AMADEUS_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token") if isinstance(token_data, dict) else None
        expires_in = token_data.get("expires_in") if isinstance(token_data, dict) else None
        if (
            not isinstance(access_token, str)
            or not access_token
            or not isinstance(expires_in, (int, float))
            or expires_in <= 0
        ):
            raise ValueError("Amadeus token response was invalid.")
        _AMADEUS_ACCESS_TOKEN = access_token
        _AMADEUS_ACCESS_TOKEN_EXPIRES_AT = time.monotonic() + max(float(expires_in) - 60, 1)
        return _AMADEUS_ACCESS_TOKEN


def normalize_amadeus_offer(offer, dictionaries):
    if not isinstance(offer, dict) or not isinstance(offer.get("itineraries"), list) or not offer["itineraries"]:
        return None
    carriers = dictionaries.get("carriers", {}) if isinstance(dictionaries, dict) else {}
    if not isinstance(carriers, dict):
        carriers = {}
    slices = []
    for item in offer["itineraries"]:
        segments = item.get("segments", []) if isinstance(item, dict) else []
        if not segments or any(not isinstance(segment, dict) for segment in segments):
            return None
        first, last = segments[0], segments[-1]
        departure = first.get("departure") or {}
        arrival = last.get("arrival") or {}
        if not isinstance(departure, dict) or not isinstance(arrival, dict):
            return None
        flight_numbers = []
        for segment in segments:
            carrier_code = segment.get("carrierCode")
            flight_number = segment.get("number")
            if isinstance(carrier_code, str) and isinstance(flight_number, str):
                flight_numbers.append(f"{carrier_code}{flight_number}")
        slices.append({
            "origin": departure.get("iataCode", ""),
            "destination": arrival.get("iataCode", ""),
            "departure": departure.get("at", ""),
            "arrival": arrival.get("at", ""),
            "duration": item.get("duration", ""),
            "stops": max(len(segments) - 1, 0),
            "flightNumbers": flight_numbers,
        })
    price = offer.get("price") if isinstance(offer.get("price"), dict) else {}
    amount = price.get("grandTotal") or price.get("total")
    currency = price.get("currency")
    offer_id = offer.get("id")
    if (
        not isinstance(offer_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", offer_id)
        or not re.fullmatch(r"\d+(?:\.\d+)?", str(amount or ""))
        or not re.fullmatch(r"[A-Z]{3}", str(currency or ""))
    ):
        return None
    validating_carriers = offer.get("validatingAirlineCodes")
    airline_code = (
        validating_carriers[0]
        if isinstance(validating_carriers, list) and validating_carriers and isinstance(validating_carriers[0], str)
        else segments[0].get("carrierCode", "")
    )
    if not isinstance(airline_code, str):
        airline_code = ""
    airline_name = carriers.get(airline_code, airline_code or "Airline")
    if not isinstance(airline_name, str):
        airline_name = airline_code or "Airline"
    return {
        "id": offer_id,
        "airline": airline_name,
        "airlineCode": airline_code,
        "totalAmount": str(amount),
        "totalCurrency": currency,
        "expiresAt": offer.get("lastTicketingDateTime"),
        "slices": slices,
    }


def demo_flight_offers(origin, destination, departure_date, return_date, passengers):
    examples = [
        ("demo-001", "Demo Airways", "DM", Decimal("489.00"), "08:10", "13:35", "5h 25m", 0, ["DM101"]),
        ("demo-002", "Sample Airlines", "SA", Decimal("425.50"), "10:20", "16:30", "6h 10m", 1, ["SA204", "SA517"]),
        ("demo-003", "Example Flights", "EF", Decimal("615.75"), "14:00", "19:05", "5h 05m", 0, ["EF309"]),
    ]
    offers = []
    for offer_id, airline, airline_code, amount, departure_time, arrival_time, duration, stops, flight_numbers in examples:
        slices = [{
            "origin": origin,
            "destination": destination,
            "departure": f"{departure_date.isoformat()}T{departure_time}:00",
            "arrival": f"{departure_date.isoformat()}T{arrival_time}:00",
            "duration": duration,
            "stops": stops,
            "flightNumbers": flight_numbers,
        }]
        if return_date:
            slices.append({
                "origin": destination,
                "destination": origin,
                "departure": f"{return_date.isoformat()}T09:00:00",
                "arrival": f"{return_date.isoformat()}T14:15:00",
                "duration": "5h 15m",
                "stops": 0,
                "flightNumbers": [f"{airline_code}401"],
            })
        offers.append({
            "id": offer_id,
            "airline": airline,
            "airlineCode": airline_code,
            "totalAmount": f"{amount * passengers:.2f}",
            "totalCurrency": "USD",
            "expiresAt": None,
            "slices": slices,
        })
    return offers


@app.post("/api/flights/search")
def search_flights():
    if flight_search_rate_limited():
        return jsonify(error="Too many flight searches. Try again in 15 minutes."), 429
    data = payload()
    origin = str(data.get("origin", "")).strip().upper()
    destination = str(data.get("destination", "")).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", origin) or not re.fullmatch(r"[A-Z]{3}", destination):
        return jsonify(error="Enter valid three-letter airport or city codes."), 400
    if origin == destination:
        return jsonify(error="Departure and destination must be different."), 400
    trip_type = data.get("tripType")
    try:
        departure = datetime.strptime(str(data.get("departureDate", "")), "%Y-%m-%d").date()
        passenger_count = data.get("passengers", 0)
        if not positive_integer(passenger_count) or float(passenger_count) > 9:
            raise ValueError()
        passengers = int(float(passenger_count))
        return_date = None
        if trip_type == "round-trip":
            return_date = datetime.strptime(str(data.get("returnDate", "")), "%Y-%m-%d").date()
            if return_date < departure:
                raise ValueError()
        elif trip_type != "one-way":
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify(error="Provide valid travel dates, trip type, and passenger count."), 400
    if departure < now().date():
        return jsonify(error="Departure date must be today or later."), 400
    cabin_classes = {"Economy": "ECONOMY", "Business": "BUSINESS", "First Class": "FIRST"}
    cabin_class = cabin_classes.get(data.get("travelClass"))
    if not cabin_class:
        return jsonify(error="Choose a valid travel class."), 400
    if FLIGHT_SEARCH_MODE == "demo":
        return jsonify(
            offers=demo_flight_offers(origin, destination, departure, return_date, passengers),
            passengerCount=passengers,
            tripType=trip_type,
            demoMode=True,
        ), 200
    if not AMADEUS_CLIENT_ID or not AMADEUS_CLIENT_SECRET:
        return jsonify(error="Flight search is not configured. Set AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET on the server."), 503
    search_params = {
        "originLocationCode": origin,
        "destinationLocationCode": destination,
        "departureDate": departure.isoformat(),
        "adults": passengers,
        "travelClass": cabin_class,
        "max": 50,
    }
    if return_date:
        search_params["returnDate"] = return_date.isoformat()
    try:
        access_token = amadeus_access_token()
        response = requests.get(
            f"{AMADEUS_API_BASE_URL}/v2/shopping/flight-offers",
            params=search_params,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=30,
        )
        response.raise_for_status()
        provider_response = response.json()
        if not isinstance(provider_response, dict) or not isinstance(provider_response.get("data"), list):
            app.logger.error("Amadeus flight search response did not contain an offers list.")
            return jsonify(error="Flight provider returned an invalid response. Please try again."), 502
        raw_offers = provider_response["data"]
        dictionaries = provider_response.get("dictionaries", {})
    except (requests.RequestException, ValueError) as error:
        app.logger.warning("Amadeus flight search failed (%s).", type(error).__name__)
        return jsonify(error="Flight provider is temporarily unavailable. Please try again."), 502
    offers = []
    for offer in raw_offers:
        summary = normalize_amadeus_offer(offer, dictionaries)
        expected_slices = 2 if trip_type == "round-trip" else 1
        if summary and len(summary["slices"]) == expected_slices:
            offers.append(summary)
    return jsonify(offers=offers, passengerCount=passengers, tripType=trip_type, demoMode=False), 200


@app.route("/api/bookings", methods=["GET", "POST"])
def bookings():
    if request.method == "GET":
        if not customer_id() and not admin_id():
            return jsonify(error="Sign in required."), 401
        base_query = """SELECT b.id, b.reference, b.customer_id, b.category, b.status, b.payload, b.created_at,
                   q.status AS quote_status, q.amount AS quote_amount, q.currency AS quote_currency
            FROM bookings b LEFT JOIN (
                SELECT booking_id, status, amount, currency,
                       ROW_NUMBER() OVER (PARTITION BY booking_id ORDER BY created_at DESC, id DESC) AS rn
                FROM quotations
            ) q ON q.booking_id = b.id AND q.rn = 1"""
        if admin_id():
            query = base_query + " ORDER BY b.id DESC"
            params = []
        else:
            query = base_query + " WHERE b.customer_id = :1 ORDER BY b.id DESC"
            params = [customer_id()]
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = to_dicts(cur)
        finally:
            conn.close()
        return jsonify(bookings=normalize_booking_rows(rows))

    if not customer_id():
        return jsonify(error="Create an account or sign in before making a booking."), 401

    documents = []
    saved_files = []
    reference = None
    try:
        if request.mimetype == "multipart/form-data":
            category = request.form.get("category")
            try:
                details = json.loads(request.form.get("payload", "{}"))
            except json.JSONDecodeError:
                return jsonify(error="Booking details must be valid JSON."), 400
            uploads = []
            if category == "clearing":
                uploads.extend([("commercial-invoice", request.files.get("commercial-invoice"))])
                uploads.extend([("packing-list", request.files.get("packing-list"))])
                uploads.extend(("shipping-documents", item) for item in request.files.getlist("shipping-documents"))
                if any(not item for _, item in uploads) or not request.files.getlist("shipping-documents"):
                    return jsonify(error="Commercial invoice, packing list, and at least one shipping document are required."), 400
                if len(uploads) > 10:
                    return jsonify(error="Upload no more than 10 documents per request."), 400
            else:
                return jsonify(error="File uploads are only accepted for clearing requests."), 400
        else:
            data = payload()
            category = data.get("category")
            details = data.get("payload")
            uploads = []
        if category == "clearing" and not uploads:
            return jsonify(error="Clearing requests must include the required supporting documents."), 400
        details = validate_booking(category, details)
        if category == "clearing":
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            for field, upload in uploads:
                filename, extension, content = validate_document(upload)
                storage_name = f"{uuid.uuid4().hex}{extension}"
                saved_files.append(storage_name)
                (UPLOAD_DIR / storage_name).write_bytes(content)
                documents.append({"field": field, "filename": filename, "storage_name": storage_name})
            details["documents"] = documents
        reference = new_reference({"flight": "FLT", "parcel": "SHP", "clearing": "CLR", "freight": "FRT"}[category])
        initial_status = {"flight": "Pending payment", "freight": "Pending quotation", "clearing": "Documents Received"}.get(category, "Pending")
        payload_str = json.dumps(details)
        if len(payload_str) > 20000:
            raise ValueError("Booking details and document metadata are too large.")
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO bookings (reference, customer_id, category, status, payload, created_at) VALUES (:1, :2, :3, :4, :5, :6)",
                    (reference, customer_id(), category, initial_status, payload_str, now()),
                )
            conn.commit()
        finally:
            conn.close()
    except ValueError as error:
        for storage_name in saved_files:
            (UPLOAD_DIR / storage_name).unlink(missing_ok=True)
        return jsonify(error=str(error)), 400
    except Exception:
        for storage_name in saved_files:
            (UPLOAD_DIR / storage_name).unlink(missing_ok=True)
        raise
    return jsonify(message="Booking request saved.", reference=reference, status=initial_status), 201


@app.get("/api/bookings/<reference>")
def get_booking(reference):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT b.id, b.reference, b.customer_id, b.category, b.status, b.payload, b.created_at
                   FROM bookings b WHERE b.reference = :1""",
                (reference.upper(),),
            )
            booking = to_dict(cur, cur.fetchone())
    finally:
        conn.close()
    if not booking:
        return jsonify(error="Booking reference not found."), 404
    details = decode_payload(booking.get("payload"))
    if admin_id():
        pass
    elif booking.get("customer_id") and booking["customer_id"] == customer_id():
        pass
    elif not booking.get("customer_id"):
        supplied_email = (request.args.get("email") or "").strip().lower()
        if not supplied_email or supplied_email != str(details.get("customer", {}).get("email", "")).strip().lower() and supplied_email != str(details.get("sender", {}).get("email", "")).strip().lower():
            return jsonify(error="Enter the email address used for this request."), 403
    else:
        return jsonify(error="Sign in to the account that created this booking."), 403
    booking["payload"] = details
    if isinstance(booking.get("created_at"), datetime):
        booking["created_at"] = booking["created_at"].isoformat()
    if booking.get("category") in BOOKING_CATEGORIES:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status, amount, currency, notes FROM quotations WHERE booking_id = :1 ORDER BY created_at DESC FETCH FIRST 1 ROWS ONLY", (booking["id"],))
                quotation = to_dict(cur, cur.fetchone())
        finally:
            conn.close()
        if quotation:
            quotation["notes"] = lob_to_str(quotation.get("notes"))
            if quotation.get("amount") is not None:
                quotation["amount"] = str(quotation["amount"])
            booking["quotation"] = quotation
    booking.pop("customer_id", None)
    booking.pop("id", None)
    return jsonify(booking=booking)


@app.post("/api/bookings/<reference>/paypal-order")
def create_paypal_order(reference):
    if not customer_id():
        return jsonify(error="Sign in to pay for this booking."), 401
    if not PAYPAL_DEMO_MODE and (not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET):
        return jsonify(error="PayPal sandbox credentials are not configured on the server."), 503

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, category, payload FROM bookings WHERE reference = :1 AND customer_id = :2 FOR UPDATE",
                (reference.upper(), customer_id()),
            )
            booking = cur.fetchone()
            if not booking:
                return jsonify(error="Booking not found."), 404
            if booking[1] == "Paid":
                return jsonify(error="This booking has already been paid."), 409
            cur.execute(
                "SELECT id FROM payments WHERE booking_id = :1 AND status = 'COMPLETED' FETCH FIRST 1 ROWS ONLY",
                (booking[0],),
            )
            if cur.fetchone():
                return jsonify(error="This booking has already been paid."), 409
            cur.execute(
                "SELECT id FROM payments WHERE booking_id = :1 AND provider <> 'PayPal' AND status = 'PENDING' FETCH FIRST 1 ROWS ONLY",
                (booking[0],),
            )
            if cur.fetchone():
                return jsonify(error="Another payment method already has an active payment for this booking."), 409
            amount = currency = None
            if booking[2] == "flight":
                if booking[1] not in {"Pending", "Pending payment", "Approved"}:
                    return jsonify(error="This flight booking is not available for payment."), 409
                details = decode_payload(booking[3])
                flight = details.get("flight") if isinstance(details, dict) else None
                try:
                    amount = normalized_money(flight.get("totalAmount") if isinstance(flight, dict) else None)
                except ValueError:
                    amount = None
                currency = str((flight or {}).get("totalCurrency", "")).upper()
                if not amount or not re.fullmatch(r"[A-Z]{3}", currency):
                    return jsonify(error="This flight booking does not have a valid payable amount."), 409
            else:
                if booking[1] != "Approved":
                    return jsonify(error="The booking must be approved before payment."), 409
                cur.execute(
                    "SELECT status, amount, currency FROM quotations WHERE booking_id = :1 ORDER BY created_at DESC FETCH FIRST 1 ROWS ONLY",
                    (booking[0],),
                )
                quotation = cur.fetchone()
                if not quotation or quotation[0] != "Approved":
                    return jsonify(error="Only an approved quotation can be paid online."), 409
                try:
                    amount = normalized_money(quotation[1])
                except ValueError as error:
                    return jsonify(error=str(error)), 409
                currency = str(quotation[2] or "").upper()
                if not re.fullmatch(r"[A-Z]{3}", currency):
                    return jsonify(error="The approved quotation has an invalid currency."), 409

            cur.execute(
                """SELECT id, provider_reference, status, amount, currency FROM payments
                   WHERE booking_id = :1 AND provider = 'PayPal' AND status IN ('PENDING', 'COMPLETED')
                   ORDER BY id DESC FETCH FIRST 1 ROWS ONLY""",
                (booking[0],),
            )
            existing = cur.fetchone()
            if existing and existing[2] == "COMPLETED":
                return jsonify(error="This booking has already been paid."), 409
            if PAYPAL_DEMO_MODE:
                order_id = f"DEMO-PAYPAL-{uuid.uuid4().hex[:24].upper()}"
                approval_url = f"{PUBLIC_BASE_URL}/portal.html?paypal=return&token={order_id}"
                cur.execute(
                    """INSERT INTO payments
                       (booking_id, amount, currency, status, provider, provider_reference, created_at, updated_at)
                       VALUES (:1, :2, :3, 'PENDING', 'PayPal', :4, :5, :6)""",
                    (booking[0], amount, currency, order_id, now(), now()),
                )
                conn.commit()
                return jsonify(order_id=order_id, approval_url=approval_url, demoMode=True), 201
            access_token = paypal_access_token()
            if existing:
                try:
                    existing_amount = normalized_money(existing[3]) if existing[3] is not None else None
                except ValueError:
                    existing_amount = None
                if existing[1] and existing_amount == amount and existing[4] == currency:
                    order_id = existing[1]
                    order = paypal_request("GET", f"/v2/checkout/orders/{quote(order_id, safe='')}", access_token)
                    if (
                        order.get("status") == "COMPLETED"
                        and verify_paypal_purchase_unit(order, reference.upper(), amount, currency)
                        and verify_paypal_capture(order, reference.upper(), amount, currency)
                    ):
                        cur.execute(
                            "UPDATE payments SET status = 'COMPLETED', updated_at = :1 WHERE id = :2 AND status = 'PENDING'",
                            (now(), existing[0]),
                        )
                        cur.execute("UPDATE bookings SET status = 'Paid' WHERE id = :1", (booking[0],))
                        cur.execute(
                            "INSERT INTO notifications (customer_id, message, created_at) VALUES (:1, :2, :3)",
                            (customer_id(), f"Payment for booking {reference.upper()} was confirmed.", now()),
                        )
                        conn.commit()
                        return jsonify(message="Payment is already complete.", status="Paid"), 200
                    if order.get("status") == "COMPLETED":
                        return jsonify(error="PayPal reports a completed payment that does not match this quotation. Contact support."), 409
                    if (
                        order.get("status") in {"CREATED", "APPROVED"}
                        and verify_paypal_purchase_unit(order, reference.upper(), amount, currency)
                    ):
                        if order["status"] == "APPROVED":
                            return jsonify(order_id=order_id, already_approved=True), 200
                        return jsonify(order_id=order_id, approval_url=paypal_approval_url(order)), 200
                cur.execute(
                    "UPDATE payments SET status = 'CANCELLED', updated_at = :1 WHERE id = :2 AND status = 'PENDING'",
                    (now(), existing[0]),
                )

            create_body = {
                "intent": "CAPTURE",
                "purchase_units": [{
                    "reference_id": reference.upper(),
                    "custom_id": reference.upper(),
                    "description": f"Rhyno Logistics booking {reference.upper()}",
                    "amount": {"currency_code": currency, "value": amount},
                }],
                "application_context": {
                    "brand_name": "Rhyno Logistics",
                    "user_action": "PAY_NOW",
                    "return_url": f"{PUBLIC_BASE_URL}/portal.html?paypal=return",
                    "cancel_url": f"{PUBLIC_BASE_URL}/portal.html?paypal=cancel",
                },
            }
            order = paypal_request(
                "POST", "/v2/checkout/orders", access_token, create_body, str(uuid.uuid4())
            )
            order_id = order.get("id")
            if (
                not isinstance(order_id, str)
                or not re.fullmatch(r"[A-Za-z0-9-]{10,100}", order_id)
                or order.get("status") != "CREATED"
                or not verify_paypal_purchase_unit(order, reference.upper(), amount, currency)
            ):
                raise ValueError("PayPal returned an invalid order response.")
            approval_url = paypal_approval_url(order)
            cur.execute(
                """INSERT INTO payments
                   (booking_id, amount, currency, status, provider, provider_reference, created_at, updated_at)
                   VALUES (:1, :2, :3, 'PENDING', 'PayPal', :4, :5, :6)""",
                (booking[0], amount, currency, order_id, now(), now()),
            )
        conn.commit()
        return jsonify(order_id=order_id, approval_url=approval_url), 201
    except (requests.RequestException, RuntimeError, ValueError) as error:
        app.logger.warning("PayPal order creation failed (%s).", type(error).__name__)
        return jsonify(error="PayPal could not create the payment order. Please try again later."), 502
    finally:
        conn.close()


@app.post("/api/payments/paypal/capture")
def capture_paypal_order():
    if not customer_id():
        return jsonify(error="Sign in to complete this payment."), 401
    if not PAYPAL_DEMO_MODE and (not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET):
        return jsonify(error="PayPal sandbox credentials are not configured on the server."), 503
    order_id = payload().get("order_id")
    if not isinstance(order_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{10,100}", order_id):
        return jsonify(error="A valid PayPal order reference is required."), 400

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT booking_id FROM payments WHERE provider = 'PayPal' AND provider_reference = :1",
                (order_id,),
            )
            payment_booking = cur.fetchone()
            if not payment_booking:
                return jsonify(error="Payment order not found."), 404
            cur.execute(
                "SELECT id, reference, status, category, payload FROM bookings WHERE id = :1 AND customer_id = :2 FOR UPDATE",
                (payment_booking[0], customer_id()),
            )
            booking = cur.fetchone()
            if not booking:
                return jsonify(error="Payment order not found."), 404
            cur.execute(
                """SELECT id, booking_id, status, amount, currency
                   FROM payments WHERE provider = 'PayPal' AND provider_reference = :1
                     AND booking_id = :2 FOR UPDATE""",
                (order_id, booking[0]),
            )
            payment = cur.fetchone()
            if not payment:
                return jsonify(error="Payment order not found."), 404
            if payment[2] == "COMPLETED":
                return jsonify(message="Payment is already complete.", status="Paid"), 200
            if payment[2] != "PENDING":
                return jsonify(error="This payment order is no longer active."), 409
            try:
                amount = normalized_money(payment[3])
            except ValueError:
                return jsonify(error="The stored payment amount is invalid."), 409
            currency = str(payment[4] or "").upper()
            if booking[3] == "flight":
                if booking[2] == "Paid":
                    return jsonify(message="Payment is already complete.", status="Paid"), 200
                if booking[2] not in {"Pending", "Pending payment", "Approved"}:
                    return jsonify(error="This flight booking is not available for payment."), 409
                details = decode_payload(booking[4])
                flight = details.get("flight") if isinstance(details, dict) else None
                try:
                    expected_amount = normalized_money(flight.get("totalAmount") if isinstance(flight, dict) else None)
                except ValueError:
                    expected_amount = None
                expected_currency = str((flight or {}).get("totalCurrency", "")).upper()
                if expected_amount != amount or expected_currency != currency:
                    return jsonify(error="The booking amount has changed. Request a new payment order."), 409
            else:
                if booking[2] != "Approved":
                    return jsonify(error="The booking is no longer approved for payment."), 409
                cur.execute(
                    "SELECT status, amount, currency FROM quotations WHERE booking_id = :1 ORDER BY created_at DESC FETCH FIRST 1 ROWS ONLY",
                    (payment[1],),
                )
                quotation = cur.fetchone()
                try:
                    current_quote_amount = normalized_money(quotation[1]) if quotation else None
                except ValueError:
                    current_quote_amount = None
                if not quotation or quotation[0] != "Approved" or current_quote_amount != amount or str(quotation[2] or "").upper() != currency:
                    return jsonify(error="The approved quotation has changed. Request a new payment order."), 409

            if PAYPAL_DEMO_MODE and order_id.startswith("DEMO-PAYPAL-"):
                cur.execute(
                    "UPDATE payments SET status = 'COMPLETED', updated_at = :1 WHERE id = :2 AND status = 'PENDING'",
                    (now(), payment[0]),
                )
                cur.execute("UPDATE bookings SET status = 'Paid' WHERE id = :1", (payment[1],))
                cur.execute(
                    "INSERT INTO notifications (customer_id, message, created_at) VALUES (:1, :2, :3)",
                    (customer_id(), f"Payment for booking {booking[1]} was confirmed.", now()),
                )
                conn.commit()
                return jsonify(message="Payment confirmed.", status="Paid", demoMode=True), 200
            access_token = paypal_access_token()
            path = f"/v2/checkout/orders/{quote(order_id, safe='')}"
            provider_order = paypal_request("GET", path, access_token)
            if (
                provider_order.get("id") != order_id
                or not verify_paypal_purchase_unit(provider_order, booking[1], amount, currency)
            ):
                return jsonify(error="PayPal order details do not match this booking."), 409
            if provider_order.get("status") == "APPROVED":
                provider_order = paypal_request("POST", f"{path}/capture", access_token, {})
            order_matches = (
                provider_order.get("id") == order_id
                and provider_order.get("status") == "COMPLETED"
                and verify_paypal_purchase_unit(provider_order, booking[1], amount, currency)
            )
            capture_statuses = paypal_capture_statuses(provider_order, booking[1])
            if not order_matches or "COMPLETED" not in capture_statuses or any(status != "COMPLETED" for status in capture_statuses):
                cur.execute(
                    "UPDATE payments SET status = 'FAILED', updated_at = :1 WHERE id = :2 AND status = 'PENDING'",
                    (now(), payment[0]),
                )
                conn.commit()
                return jsonify(error="PayPal has not confirmed the full captured payment."), 409
            if not verify_paypal_capture(provider_order, booking[1], amount, currency):
                return jsonify(error="PayPal capture details do not match this booking."), 409
            cur.execute(
                "UPDATE payments SET status = 'COMPLETED', updated_at = :1 WHERE id = :2 AND status = 'PENDING'",
                (now(), payment[0]),
            )
            cur.execute("UPDATE bookings SET status = 'Paid' WHERE id = :1", (payment[1],))
            cur.execute(
                "INSERT INTO notifications (customer_id, message, created_at) VALUES (:1, :2, :3)",
                (customer_id(), f"Payment for booking {booking[1]} was confirmed.", now()),
            )
        conn.commit()
        return jsonify(message="Payment confirmed.", status="Paid"), 200
    except (requests.RequestException, RuntimeError, ValueError) as error:
        app.logger.warning("PayPal payment capture failed (%s).", type(error).__name__)
        return jsonify(error="PayPal could not verify the payment. Please try again or contact support."), 502
    finally:
        conn.close()


@app.post("/api/bookings/<reference>/paystack-mobile-money")
def create_paystack_mobile_money(reference):
    if not customer_id():
        return jsonify(error="Sign in to pay for this booking."), 401
    if not PAYSTACK_DEMO_MODE and not PAYSTACK_SECRET_KEY:
        return jsonify(error="Paystack is not configured on the server."), 503

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, category, payload FROM bookings WHERE reference = :1 AND customer_id = :2 FOR UPDATE",
                (reference.upper(), customer_id()),
            )
            booking = cur.fetchone()
            if not booking:
                return jsonify(error="Booking not found."), 404
            if booking[1] == "Paid":
                return jsonify(error="This booking has already been paid."), 409
            amount = currency = None
            if booking[2] == "flight":
                if booking[1] not in {"Pending", "Pending payment", "Approved"}:
                    return jsonify(error="This flight booking is not available for payment."), 409
                details = decode_payload(booking[3])
                flight = details.get("flight") if isinstance(details, dict) else None
                try:
                    amount = normalized_money(flight.get("totalAmount") if isinstance(flight, dict) else None)
                except ValueError:
                    amount = None
                currency = str((flight or {}).get("totalCurrency", "")).upper()
                if not amount or not re.fullmatch(r"[A-Z]{3}", currency):
                    return jsonify(error="This flight booking does not have a valid payable amount."), 409
            else:
                if booking[1] != "Approved":
                    return jsonify(error="The booking must be approved before payment."), 409
                cur.execute(
                    "SELECT status, amount, currency FROM quotations WHERE booking_id = :1 ORDER BY created_at DESC FETCH FIRST 1 ROWS ONLY",
                    (booking[0],),
                )
                quotation = cur.fetchone()
                if not quotation or quotation[0] != "Approved":
                    return jsonify(error="Only an approved quotation can be paid online."), 409
                try:
                    amount = normalized_money(quotation[1])
                except ValueError as error:
                    return jsonify(error=str(error)), 409
                currency = str(quotation[2] or "").upper()
                if not re.fullmatch(r"[A-Z]{3}", currency):
                    return jsonify(error="The approved quotation has an invalid currency."), 409
            if currency != "GHS":
                return jsonify(error="Mobile money checkout is available only for GHS payments."), 409

            cur.execute(
                "SELECT id FROM payments WHERE booking_id = :1 AND status = 'COMPLETED' FETCH FIRST 1 ROWS ONLY",
                (booking[0],),
            )
            if cur.fetchone():
                return jsonify(error="This booking has already been paid."), 409
            cur.execute(
                "SELECT id FROM payments WHERE booking_id = :1 AND provider <> 'Paystack' AND status = 'PENDING' FETCH FIRST 1 ROWS ONLY",
                (booking[0],),
            )
            if cur.fetchone():
                return jsonify(error="Another payment method already has an active payment for this booking."), 409

            cur.execute(
                """SELECT id, provider_reference, status, amount, currency, provider_checkout_url FROM payments
                   WHERE booking_id = :1 AND provider = 'Paystack'
                     AND status IN ('PENDING', 'COMPLETED')
                   ORDER BY id DESC FETCH FIRST 1 ROWS ONLY""",
                (booking[0],),
            )
            existing = cur.fetchone()
            if existing and existing[2] == "COMPLETED":
                return jsonify(error="This booking has already been paid."), 409
            if existing:
                try:
                    existing_amount = normalized_money(existing[3]) if existing[3] is not None else None
                except ValueError:
                    existing_amount = None
                existing_matches_quote = (
                    existing_amount == amount and str(existing[4] or "").upper() == "GHS"
                )
                if not existing[1]:
                    return jsonify(error="The pending Paystack payment is missing its transaction reference. Contact support."), 409
                if PAYSTACK_DEMO_MODE and existing[1].startswith("DEMO-PAYSTACK-"):
                    return jsonify(checkout_url=existing[5], demoMode=True), 200
                transaction = paystack_request(
                    "GET",
                    f"/transaction/verify/{quote(existing[1], safe='')}",
                )
                if transaction.get("status") == "success":
                    if not existing_matches_quote or not verify_paystack_transaction(
                        transaction, existing[1], reference.upper(), amount
                    ):
                        return jsonify(error="The completed Paystack transaction does not match this quotation. Contact support."), 409
                    cur.execute(
                        "UPDATE payments SET status = 'COMPLETED', updated_at = :1 WHERE id = :2 AND status = 'PENDING'",
                        (now(), existing[0]),
                    )
                    cur.execute("UPDATE bookings SET status = 'Paid' WHERE id = :1", (booking[0],))
                    cur.execute(
                        "INSERT INTO notifications (customer_id, message, created_at) VALUES (:1, :2, :3)",
                        (customer_id(), f"Payment for booking {reference.upper()} was confirmed.", now()),
                    )
                    conn.commit()
                    return jsonify(message="Payment is already complete.", status="Paid"), 200
                if transaction.get("status") not in {"failed", "abandoned"}:
                    if existing_matches_quote and existing[5]:
                        return jsonify(checkout_url=existing[5]), 200
                    return jsonify(error="A mobile money payment for this booking is already in progress. Contact support before starting another."), 409
                cur.execute(
                    "UPDATE payments SET status = 'FAILED', updated_at = :1 WHERE id = :2 AND status = 'PENDING'",
                    (now(), existing[0]),
                )

            cur.execute("SELECT email FROM customers WHERE id = :1", (customer_id(),))
            customer = cur.fetchone()
            if not customer or not customer[0]:
                return jsonify(error="A valid customer email is required for mobile money checkout."), 409

            if PAYSTACK_DEMO_MODE:
                provider_reference = f"DEMO-PAYSTACK-{uuid.uuid4().hex.upper()}"
                checkout_url = f"{PUBLIC_BASE_URL}/portal.html?paystack=return&reference={provider_reference}"
            else:
                provider_reference = f"RHY-PAY-{uuid.uuid4().hex.upper()}"
                transaction = paystack_request(
                    "POST",
                    "/transaction/initialize",
                    {
                        "email": customer[0],
                        "amount": int(Decimal(amount) * 100),
                        "currency": "GHS",
                        "reference": provider_reference,
                        "callback_url": f"{PUBLIC_BASE_URL}/portal.html?paystack=return",
                        "channels": ["mobile_money"],
                        "metadata": {"booking_reference": reference.upper()},
                    },
                )
                checkout_url = transaction.get("authorization_url")
                checkout_host = urlparse(checkout_url).hostname if isinstance(checkout_url, str) else None
                if (
                    transaction.get("reference") != provider_reference
                    or not isinstance(checkout_url, str)
                    or not checkout_url.startswith("https://")
                    or checkout_host != "checkout.paystack.com"
                ):
                    raise ValueError("Paystack returned an invalid checkout response.")
            cur.execute(
                """INSERT INTO payments
                   (booking_id, amount, currency, status, provider, provider_reference, provider_checkout_url, created_at, updated_at)
                   VALUES (:1, :2, 'GHS', 'PENDING', 'Paystack', :3, :4, :5, :6)""",
                (booking[0], amount, provider_reference, checkout_url, now(), now()),
            )
        conn.commit()
        return jsonify(checkout_url=checkout_url, demoMode=PAYSTACK_DEMO_MODE), 201
    except (requests.RequestException, RuntimeError, ValueError) as error:
        app.logger.warning("Paystack mobile money initialization failed (%s).", type(error).__name__)
        return jsonify(error="Paystack could not start mobile money checkout. Please try again later."), 502
    finally:
        conn.close()


@app.post("/api/payments/paystack/verify")
def verify_paystack_payment():
    if not customer_id():
        return jsonify(error="Sign in to verify this payment."), 401
    if not PAYSTACK_DEMO_MODE and not PAYSTACK_SECRET_KEY:
        return jsonify(error="Paystack is not configured on the server."), 503
    reference = payload().get("reference")
    if not isinstance(reference, str) or not re.fullmatch(r"(?:RHY-PAY-[A-F0-9]{32}|DEMO-PAYSTACK-[A-F0-9]{32,64})", reference):
        return jsonify(error="A valid Paystack transaction reference is required."), 400

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT booking_id FROM payments WHERE provider = 'Paystack' AND provider_reference = :1",
                (reference,),
            )
            payment_booking = cur.fetchone()
            if not payment_booking:
                return jsonify(error="Payment transaction not found."), 404
            cur.execute(
                "SELECT id, reference, status, category, payload FROM bookings WHERE id = :1 AND customer_id = :2 FOR UPDATE",
                (payment_booking[0], customer_id()),
            )
            booking = cur.fetchone()
            if not booking:
                return jsonify(error="Payment transaction not found."), 404
            cur.execute(
                """SELECT id, booking_id, status, amount, currency FROM payments
                   WHERE provider = 'Paystack' AND provider_reference = :1
                     AND booking_id = :2 FOR UPDATE""",
                (reference, booking[0]),
            )
            payment = cur.fetchone()
            if not payment:
                return jsonify(error="Payment transaction not found."), 404
            if payment[2] == "COMPLETED":
                return jsonify(message="Payment is already complete.", status="Paid"), 200
            if payment[2] != "PENDING":
                return jsonify(error="This payment transaction is no longer active."), 409
            try:
                amount = normalized_money(payment[3])
            except ValueError:
                return jsonify(error="The stored payment amount is invalid."), 409
            currency = str(payment[4] or "").upper()
            if booking[3] == "flight":
                if booking[2] == "Paid":
                    return jsonify(message="Payment is already complete.", status="Paid"), 200
                if booking[2] not in {"Pending", "Pending payment", "Approved"}:
                    return jsonify(error="This flight booking is not available for payment."), 409
                details = decode_payload(booking[4])
                flight = details.get("flight") if isinstance(details, dict) else None
                try:
                    expected_amount = normalized_money(flight.get("totalAmount") if isinstance(flight, dict) else None)
                except ValueError:
                    expected_amount = None
                expected_currency = str((flight or {}).get("totalCurrency", "")).upper()
                if expected_currency != "GHS" or currency != "GHS" or expected_amount != amount:
                    return jsonify(error="The booking amount or currency has changed. Request a new payment."), 409
            else:
                if booking[2] != "Approved":
                    return jsonify(error="The booking is no longer approved for payment."), 409
                if currency != "GHS":
                    return jsonify(error="The stored mobile money currency is invalid."), 409
                cur.execute(
                    "SELECT status, amount, currency FROM quotations WHERE booking_id = :1 ORDER BY created_at DESC FETCH FIRST 1 ROWS ONLY",
                    (payment[1],),
                )
                quotation = cur.fetchone()
                try:
                    current_quote_amount = normalized_money(quotation[1]) if quotation else None
                except ValueError:
                    current_quote_amount = None
                if not quotation or quotation[0] != "Approved" or current_quote_amount != amount or str(quotation[2] or "").upper() != "GHS":
                    return jsonify(error="The approved GHS quotation has changed. Request a new payment."), 409

            if PAYSTACK_DEMO_MODE and reference.startswith("DEMO-PAYSTACK-"):
                cur.execute(
                    "UPDATE payments SET status = 'COMPLETED', updated_at = :1 WHERE id = :2 AND status = 'PENDING'",
                    (now(), payment[0]),
                )
                cur.execute("UPDATE bookings SET status = 'Paid' WHERE id = :1", (payment[1],))
                cur.execute(
                    "INSERT INTO notifications (customer_id, message, created_at) VALUES (:1, :2, :3)",
                    (customer_id(), f"Mobile money payment for booking {booking[1]} was confirmed.", now()),
                )
                conn.commit()
                return jsonify(message="Mobile money payment confirmed.", status="Paid", demoMode=True), 200
            transaction = paystack_request(
                "GET",
                f"/transaction/verify/{quote(reference, safe='')}",
            )
            if transaction.get("status") in {"failed", "abandoned"}:
                cur.execute(
                    "UPDATE payments SET status = 'FAILED', updated_at = :1 WHERE id = :2 AND status = 'PENDING'",
                    (now(), payment[0]),
                )
                conn.commit()
                return jsonify(error="Paystack reports that this mobile money payment did not complete."), 409
            if not verify_paystack_transaction(transaction, reference, booking[1], amount):
                return jsonify(error="Paystack has not confirmed the full GHS mobile money payment."), 409
            cur.execute(
                "UPDATE payments SET status = 'COMPLETED', updated_at = :1 WHERE id = :2 AND status = 'PENDING'",
                (now(), payment[0]),
            )
            cur.execute("UPDATE bookings SET status = 'Paid' WHERE id = :1", (payment[1],))
            cur.execute(
                "INSERT INTO notifications (customer_id, message, created_at) VALUES (:1, :2, :3)",
                (customer_id(), f"Mobile money payment for booking {booking[1]} was confirmed.", now()),
            )
        conn.commit()
        return jsonify(message="Mobile money payment confirmed.", status="Paid"), 200
    except (requests.RequestException, RuntimeError, ValueError) as error:
        app.logger.warning("Paystack payment verification failed (%s).", type(error).__name__)
        return jsonify(error="Paystack could not verify the mobile money payment. Please try again or contact support."), 502
    finally:
        conn.close()


@app.post("/api/payments/paypal/cancel")
def cancel_paypal_order():
    if not customer_id():
        return jsonify(error="Sign in to cancel this payment."), 401
    order_id = payload().get("order_id")
    if not isinstance(order_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{10,100}", order_id):
        return jsonify(error="A valid PayPal order reference is required."), 400
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT booking_id FROM payments WHERE provider = 'PayPal' AND provider_reference = :1",
                (order_id,),
            )
            payment_booking = cur.fetchone()
            if not payment_booking:
                return jsonify(error="Payment order not found."), 404
            cur.execute(
                "SELECT id FROM bookings WHERE id = :1 AND customer_id = :2 FOR UPDATE",
                (payment_booking[0], customer_id()),
            )
            if not cur.fetchone():
                return jsonify(error="Payment order not found."), 404
            cur.execute(
                "SELECT id, status FROM payments WHERE provider = 'PayPal' AND provider_reference = :1 AND booking_id = :2 FOR UPDATE",
                (order_id, payment_booking[0]),
            )
            payment = cur.fetchone()
            if not payment:
                return jsonify(error="Payment order not found."), 404
            if payment[1] == "CANCELLED":
                return jsonify(message="Payment was already cancelled.", status="CANCELLED"), 200
            if payment[1] != "PENDING":
                return jsonify(error="This payment order can no longer be cancelled."), 409
            cur.execute(
                "UPDATE payments SET status = 'CANCELLED', updated_at = :1 WHERE id = :2 AND status = 'PENDING'",
                (now(), payment[0]),
            )
        conn.commit()
        return jsonify(message="Payment cancelled. The booking request remains unpaid.", status="CANCELLED"), 200
    finally:
        conn.close()


@app.get("/api/bookings/<reference>/receipt")
def booking_receipt(reference):
    if not customer_id():
        return jsonify(error="Sign in to download a booking receipt."), 401
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT reference, category, status, payload, created_at FROM bookings WHERE reference = :1 AND customer_id = :2",
                (reference.upper(), customer_id()))
            booking = to_dict(cur, cur.fetchone())
    finally:
        conn.close()
    if not booking:
        return jsonify(error="Booking not found."), 404
    details = decode_payload(booking.get("payload"))
    contact = details.get("customer") or details.get("sender") or {}
    lines = [
        "Rhyno Logistics booking request",
        f"Reference: {booking['reference']}",
        f"Category: {booking['category']}",
        f"Status: {booking['status']}",
        f"Customer: {contact.get('name', '')}",
        f"Email: {contact.get('email', '')}",
        f"Created: {booking.get('created_at')}",
        "",
        "This is a booking request receipt, not a payment receipt or confirmed airline ticket.",
    ]
    return Response("\n".join(lines), mimetype="text/plain", headers={"Content-Disposition": f"attachment; filename={booking['reference']}-receipt.txt"})


@app.get("/api/admin/customers")
def admin_customers():
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, email, phone, active, created_at FROM customers ORDER BY id DESC")
            customers = to_dicts(cur)
    finally:
        conn.close()
    for customer in customers:
        customer["active"] = customer.get("active") == 1
        if isinstance(customer.get("created_at"), datetime):
            customer["created_at"] = customer["created_at"].isoformat()
    return jsonify(customers=customers)


@app.patch("/api/admin/customers/<int:customer_id>")
def update_customer(customer_id):
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    data = payload()
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    phone = str(data.get("phone", "")).strip()
    active_value = data.get("active")
    if active_value in (True, 1, "1", "true"):
        active = 1
    elif active_value in (False, 0, "0", "false"):
        active = 0
    else:
        return jsonify(error="Choose whether the customer account is active."), 400
    if (
        not name or len(name) > 255 or not phone or len(phone) > 50
        or len(email) > 255 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
    ):
        return jsonify(error="Enter a valid customer name, email address, and phone number."), 400
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customers WHERE id = :1", (customer_id,))
            if not cur.fetchone():
                return jsonify(error="Customer not found."), 404
            cur.execute("SELECT id FROM customers WHERE email = :1 AND id <> :2", (email, customer_id))
            if cur.fetchone():
                return jsonify(error="That email address is already associated with another account."), 409
            cur.execute(
                "UPDATE customers SET name = :1, email = :2, phone = :3, active = :4 WHERE id = :5",
                (name, email, phone, active, customer_id),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify(message="Customer account updated.", active=bool(active))


@app.get("/api/admin/bookings")
def admin_bookings():
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT b.id, b.reference, b.customer_id, b.category, b.status, b.payload, b.created_at
                FROM bookings b ORDER BY b.id DESC""")
            rows = to_dicts(cur)
    finally:
        conn.close()
    return jsonify(bookings=normalize_booking_rows(rows))


@app.get("/api/admin/payments")
def admin_payments():
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT p.id, b.reference, b.category, p.amount, p.currency, p.status,
                                  p.provider, p.provider_reference, p.created_at
                FROM payments p JOIN bookings b ON b.id = p.booking_id ORDER BY p.id DESC""")
            payments = to_dicts(cur)
    finally:
        conn.close()
    for payment in payments:
        if payment.get("amount") is not None:
            payment["amount"] = str(payment["amount"])
        if isinstance(payment.get("created_at"), datetime):
            payment["created_at"] = payment["created_at"].isoformat()
    return jsonify(payments=payments)


@app.get("/api/admin/shipments")
def admin_shipments():
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT s.tracking_number, b.reference AS booking_reference, s.origin, s.destination,
                s.status, s.eta, s.location, s.updated_at FROM shipments s
                LEFT JOIN bookings b ON b.id = s.booking_id ORDER BY s.id DESC""")
            shipments = to_dicts(cur)
    finally:
        conn.close()
    for shipment in shipments:
        for key in ("eta", "updated_at"):
            if isinstance(shipment.get(key), datetime):
                shipment[key] = shipment[key].isoformat()
    return jsonify(shipments=shipments)


@app.get("/api/admin/documents/<reference>/<storage_name>")
def admin_document(reference, storage_name):
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    if not re.fullmatch(r"[a-f0-9]{32}\.(pdf|jpg|jpeg|png)", storage_name):
        abort(404)
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM bookings WHERE reference = :1 AND category = 'clearing'", (reference.upper(),))
            booking = to_dict(cur, cur.fetchone())
    finally:
        conn.close()
    if not booking:
        abort(404)
    documents = decode_payload(booking.get("payload")).get("documents", [])
    document = next((item for item in documents if item.get("storage_name") == storage_name), None)
    path = UPLOAD_DIR / storage_name
    if not document or not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=document["filename"], mimetype=mimetypes.guess_type(document["filename"])[0] or "application/octet-stream")


@app.patch("/api/admin/bookings/<reference>")
def update_booking(reference):
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    status = payload().get("status")
    if not isinstance(status, str) or status not in BOOKING_STATUSES:
        return jsonify(error="Choose a valid booking status."), 400
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, customer_id FROM bookings WHERE reference = :1 FOR UPDATE", (reference.upper(),))
            booking = cur.fetchone()
            if not booking:
                return jsonify(error="Booking reference not found."), 404
            cur.execute("UPDATE bookings SET status = :1 WHERE id = :2", (status, booking[0]))
            if status != "Approved":
                cur.execute(
                    "UPDATE payments SET status = 'CANCELLED', updated_at = :1 WHERE booking_id = :2 AND status = 'PENDING'",
                    (now(), booking[0]),
                )
            if booking[1] is not None:
                cur.execute("INSERT INTO notifications (customer_id, message, created_at) VALUES (:1, :2, :3)",
                    (booking[1], f"Booking {reference.upper()} status updated to {status}.", now()))
        conn.commit()
    finally:
        conn.close()
    return jsonify(message="Booking status updated.", status=status)


@app.post("/api/admin/shipments")
def create_shipment():
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    data = payload()
    required = ("booking_reference", "origin", "destination", "location", "status")
    if any(not isinstance(data.get(key), str) or not data[key].strip() for key in required):
        return jsonify(error="Booking reference, route, current location, and status are required."), 400
    if any(len(str(data[key]).strip()) > limit for key, limit in {
        "booking_reference": 50, "origin": 255, "destination": 255, "location": 255, "status": 50
    }.items()):
        return jsonify(error="Shipment details exceed the allowed length."), 400
    if data["origin"].strip().casefold() == data["destination"].strip().casefold():
        return jsonify(error="Shipment origin and destination must differ."), 400
    eta = None
    if data.get("eta"):
        try:
            eta = parse_datetime(data["eta"])
        except ValueError as error:
            return jsonify(error=str(error)), 400
    reference = new_reference("TRK")
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM bookings WHERE reference = :1", (data["booking_reference"].strip().upper(),))
            row = cur.fetchone()
            if not row:
                return jsonify(error="Booking reference not found."), 404
            shipment_id = cur.var(oracledb.NUMBER)
            cur.execute("""INSERT INTO shipments (tracking_number, booking_id, origin, destination, status, eta, location, updated_at)
                VALUES (:1, :2, :3, :4, :5, :6, :7, :8) RETURNING id INTO :9""",
                (reference, row[0], data["origin"].strip(), data["destination"].strip(), data["status"].strip(), eta,
                 data["location"].strip(), now(), shipment_id))
            cur.execute("INSERT INTO tracking_history (shipment_id, status, location, occurred_at) VALUES (:1, :2, :3, :4)",
                (int(shipment_id.getvalue()[0]), data["status"].strip(), data["location"].strip(), now()))
        conn.commit()
    finally:
        conn.close()
    return jsonify(message="Shipment created.", tracking_number=reference), 201


@app.patch("/api/admin/shipments/<tracking_number>")
def update_shipment(tracking_number):
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    data = payload()
    status = str(data.get("status", "")).strip()
    location = str(data.get("location", "")).strip()
    if not status or not location:
        return jsonify(error="Status and current location are required."), 400
    if len(status) > 50 or len(location) > 255:
        return jsonify(error="Status or location exceeds the allowed length."), 400
    try:
        eta = parse_datetime(data.get("eta"))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM shipments WHERE tracking_number = :1", (tracking_number.upper(),))
            shipment = cur.fetchone()
            if not shipment:
                return jsonify(error="Tracking number not found."), 404
            cur.execute("UPDATE shipments SET status = :1, location = :2, eta = :3, updated_at = :4 WHERE id = :5",
                (status, location, eta, now(), shipment[0]))
            cur.execute("INSERT INTO tracking_history (shipment_id, status, location, occurred_at) VALUES (:1, :2, :3, :4)",
                (shipment[0], status, location, now()))
        conn.commit()
    finally:
        conn.close()
    return jsonify(message="Shipment status updated.")


@app.get("/api/admin/quotations")
def admin_quotations():
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT q.id, b.reference, b.category, q.status, q.amount, q.currency, q.notes, q.created_at
                FROM quotations q JOIN bookings b ON b.id = q.booking_id ORDER BY q.id DESC""")
            quotations = to_dicts(cur)
    finally:
        conn.close()
    for quotation in quotations:
        quotation["notes"] = lob_to_str(quotation.get("notes"))
        if quotation.get("amount") is not None:
            quotation["amount"] = str(quotation["amount"])
        if isinstance(quotation.get("created_at"), datetime):
            quotation["created_at"] = quotation["created_at"].isoformat()
    return jsonify(quotations=quotations)


@app.post("/api/admin/quotations")
def save_quotation():
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    data = payload()
    reference = str(data.get("reference", "")).strip().upper()
    status = data.get("status")
    if not reference or len(reference) > 50 or not isinstance(status, str) or status not in {"Pending", "Approved", "Rejected"}:
        return jsonify(error="Booking reference and a valid quotation status are required."), 400
    amount = data.get("amount")
    currency = str(data.get("currency", "USD")).strip().upper()
    if status == "Approved":
        try:
            amount = Decimal(normalized_money(amount))
            if amount > Decimal("100000000"):
                raise ValueError()
        except (TypeError, ValueError, InvalidOperation):
            return jsonify(error="Approved quotations require a positive amount."), 400
    else:
        amount = None
    if not re.fullmatch(r"[A-Z]{3}", currency):
        return jsonify(error="Currency must be a three-letter code."), 400
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, customer_id, category FROM bookings WHERE reference = :1 FOR UPDATE", (reference,))
            booking = cur.fetchone()
            if not booking:
                return jsonify(error="Booking reference not found."), 404
            cur.execute("SELECT id FROM payments WHERE booking_id = :1 AND status = 'COMPLETED'", (booking[0],))
            if cur.fetchone():
                return jsonify(error="A quotation cannot be changed after payment is completed."), 409
            cur.execute(
                "UPDATE payments SET status = 'CANCELLED', updated_at = :1 WHERE booking_id = :2 AND status = 'PENDING'",
                (now(), booking[0]),
            )
            cur.execute("SELECT id FROM quotations WHERE booking_id = :1 ORDER BY created_at DESC FETCH FIRST 1 ROWS ONLY", (booking[0],))
            existing = cur.fetchone()
            if existing:
                cur.execute("UPDATE quotations SET status = :1, amount = :2, currency = :3, notes = :4 WHERE id = :5",
                    (status, amount, currency, str(data.get("notes", ""))[:4000], existing[0]))
            else:
                cur.execute("INSERT INTO quotations (booking_id, status, amount, currency, notes, created_at) VALUES (:1, :2, :3, :4, :5, :6)",
                    (booking[0], status, amount, currency, str(data.get("notes", ""))[:4000], now()))
            booking_status = {"Pending": "Pending quotation", "Approved": "Approved", "Rejected": "Rejected"}[status]
            cur.execute("UPDATE bookings SET status = :1 WHERE id = :2", (booking_status, booking[0]))
            if booking[1] is not None:
                cur.execute("INSERT INTO notifications (customer_id, message, created_at) VALUES (:1, :2, :3)",
                    (booking[1], f"Quotation for {reference} is {status.lower()}.", now()))
        conn.commit()
    finally:
        conn.close()
    return jsonify(message="Quotation saved.")


@app.get("/api/track/<tracking_number>")
def track(tracking_number):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM shipments WHERE tracking_number = :1", (tracking_number.upper(),))
            shipment = to_dict(cur, cur.fetchone())
            if not shipment:
                return jsonify(error="Tracking number not found."), 404
            cur.execute(
                "SELECT status, location, occurred_at FROM tracking_history WHERE shipment_id = :1 ORDER BY id",
                (shipment["id"],),
            )
            history = to_dicts(cur)
    finally:
        conn.close()
    for k, v in list(shipment.items()):
        if isinstance(v, datetime):
            shipment[k] = v.isoformat()
    shipment.pop("id", None)
    shipment.pop("booking_id", None)
    for h in history:
        if isinstance(h.get("occurred_at"), datetime):
            h["occurred_at"] = h["occurred_at"].isoformat()
    return jsonify(shipment=shipment, history=history)


@app.get("/api/admin/overview")
def overview():
    if not admin_id():
        return jsonify(error="Administrator sign-in required."), 401
    conn = db()
    try:
        with conn.cursor() as cur:
            counts = {}
            cur.execute("SELECT COUNT(*) FROM customers"); counts["customers"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM bookings"); counts["bookings"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM bookings WHERE status NOT IN ('Completed', 'Cancelled')"); counts["pending_bookings"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM bookings WHERE status = 'Completed'"); counts["completed_bookings"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM shipments WHERE status <> 'Delivered'"); counts["active_shipments"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM bookings WHERE category = 'clearing' AND status <> 'Completed'"); counts["pending_clearing"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM bookings WHERE category = 'freight' AND status <> 'Completed'"); counts["pending_freight"] = cur.fetchone()[0]
    finally:
        conn.close()
    return jsonify(counts=counts)


if __name__ == "__main__":
    init_db()
    app.run(debug=FLASK_ENV != "production" and os.environ.get("FLASK_DEBUG") == "1")
