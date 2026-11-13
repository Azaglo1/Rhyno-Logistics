import io
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import app as application
import reset_admin_password


class FakeCursor:
    def __init__(self):
        self.statements = []
        self.description = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.statements.append((statement, params))
        if statement.lstrip().upper().startswith("SELECT"):
            self.rowcount = 0

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.last_cursor = None

    def cursor(self):
        self.last_cursor = FakeCursor()
        return self.last_cursor

    def commit(self):
        pass

    def close(self):
        pass


class FakeLob:
    def __init__(self, value):
        self.value = value
        self.read_count = 0

    def read(self):
        self.read_count += 1
        return self.value


class FakeDuffelResponse:
    def __init__(self, data, error=None, status_code=200):
        self.data = data
        self.error = error
        self.status_code = status_code

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.data


class ScriptedCursor(FakeCursor):
    def __init__(self, select_results):
        super().__init__()
        self.select_results = iter(select_results)
        self.current_result = None

    def execute(self, statement, params=None):
        super().execute(statement, params)
        if statement.lstrip().upper().startswith("SELECT"):
            self.current_result = next(self.select_results, None)
            self.rowcount = 1 if self.current_result else 0

    def fetchone(self):
        return self.current_result


class ScriptedConnection(FakeConnection):
    def __init__(self, select_results):
        super().__init__()
        self.scripted_cursor = ScriptedCursor(select_results)

    def cursor(self):
        self.last_cursor = self.scripted_cursor
        return self.last_cursor


class BookingApiTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = application.app.test_client()
        token = self.client.get("/api/csrf-token").get_json()["token"]
        self.headers = {"X-CSRF-Token": token}
        application.FLIGHT_SEARCH_ATTEMPTS.clear()

    def sign_in_customer(self, customer_id=3):
        with self.client.session_transaction() as browser_session:
            browser_session["customer_id"] = customer_id
            browser_session["customer_session_version"] = 0

    def test_booking_pages_serve_country_and_flight_airport_options(self):
        response = self.client.get("/assets/data/location-data.json")
        self.assertEqual(response.status_code, 200)
        location_data = response.get_json()
        response.close()
        countries = {country["code"]: country["name"] for country in location_data["countries"]}
        airports = {(airport["countryCode"], airport["code"]) for airport in location_data["airports"]}
        self.assertEqual(countries["GH"], "Ghana")
        self.assertEqual(countries["GB"], "United Kingdom")
        self.assertIn(("GH", "ACC"), airports)
        self.assertIn(("GB", "LHR"), airports)
        response = self.client.get("/js/location-options.js")
        self.assertEqual(response.status_code, 200)
        response.close()

        for page in ("flights.html", "parcels.html", "clearing.html", "freight.html"):
            with self.subTest(page=page):
                response = self.client.get(f"/{page}")
                html = response.get_data(as_text=True)
                response.close()
                self.assertIn("data-country-select", html)
        response = self.client.get("/flights.html")
        flight_html = response.get_data(as_text=True)
        response.close()
        self.assertIn('data-airport-select', flight_html)

    def test_database_row_helpers_materialize_lobs_before_connection_closes(self):
        one = FakeLob('{"booking":1}')
        many = FakeLob('{"booking":2}')
        single_cursor = FakeCursor()
        single_cursor.description = [("PAYLOAD",)]
        multiple_cursor = FakeCursor()
        multiple_cursor.description = [("PAYLOAD",)]
        multiple_cursor.fetchall = lambda: [(many,)]
        with patch.object(application.oracledb, "LOB", FakeLob):
            single = application.to_dict(single_cursor, (one,))
            multiple = application.to_dicts(multiple_cursor)
        self.assertEqual(single["payload"], '{"booking":1}')
        self.assertEqual(multiple[0]["payload"], '{"booking":2}')
        self.assertEqual(one.read_count, 1)
        self.assertEqual(many.read_count, 1)

    def test_booking_reference_is_created_and_saved_by_server(self):
        self.sign_in_customer()
        connection = FakeConnection()
        payload = {
            "sender": {"name": "Sender", "email": "sender@example.com", "phone": "+123"},
            "recipient": {"name": "Recipient", "email": "recipient@example.com"},
            "parcel": {"description": "Documents", "weight": "1.5", "quantity": "1", "deliveryType": "standard"},
            "option": "standard",
        }
        with patch.object(application, "db", side_effect=[ScriptedConnection([(1, 0)]), connection]):
            response = self.client.post(
                "/api/bookings",
                json={"category": "parcel", "payload": payload},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 201)
        reference = response.get_json()["reference"]
        self.assertRegex(reference, re.compile(r"^RHY-SHP-[A-F0-9]{8}$"))
        statement, binds = connection.last_cursor.statements[0]
        self.assertIn("INSERT INTO bookings", statement)
        self.assertEqual(binds[0], reference)
        self.assertEqual(binds[2], "parcel")
        self.assertEqual(json.loads(binds[4])["sender"]["email"], "sender@example.com")

    def test_flight_clearing_and_freight_requests_are_accepted(self):
        self.sign_in_customer()
        departure = (datetime.now(timezone.utc) + timedelta(days=14)).date()
        return_date = departure + timedelta(days=7)
        requests = [
            ("flight", {
                "customer": {"name": "Passenger", "email": "passenger@example.com"},
                "flight": {
                    "origin": "Accra", "destination": "London", "departureDate": departure.isoformat(),
                    "returnDate": return_date.isoformat(), "tripType": "round-trip", "passengers": 1,
                    "travelClass": "Economy", "provider": "Demo", "offerId": "demo-001",
                },
            }),
            ("freight", {
                "customer": {"name": "Customer", "email": "customer@example.com"},
                "cargo": {
                    "origin": "Accra", "destination": "London", "weight": "100", "quantity": "1",
                    "dimensions": "100 x 50 x 40", "method": "Air freight",
                    "date": (datetime.now(timezone.utc) + timedelta(days=14)).date().isoformat(),
                },
            }),
        ]
        for category, details in requests:
            connection = FakeConnection()
            with self.subTest(category=category), patch.object(
                application, "db", side_effect=[ScriptedConnection([(1, 0)]), connection]
            ), patch.object(application, "FLIGHT_SEARCH_MODE", "demo"):
                response = self.client.post(
                    "/api/bookings",
                    json={"category": category, "payload": details},
                    headers=self.headers,
                )
                self.assertEqual(response.status_code, 201, response.get_json())
                prefix = "FLT" if category == "flight" else "FRT"
                self.assertTrue(response.get_json()["reference"].startswith(f"RHY-{prefix}-"))
                if category == "flight":
                    saved_payload = json.loads(connection.last_cursor.statements[0][1][4])
                    self.assertTrue(saved_payload["flight"]["demoMode"])

    def test_booking_mutation_requires_csrf_token(self):
        response = self.client.post("/api/bookings", json={"category": "invalid", "payload": {}})
        self.assertEqual(response.status_code, 403)

    def test_booking_requires_customer_account(self):
        with patch.object(application, "db") as database:
            response = self.client.post(
                "/api/bookings",
                json={"category": "parcel", "payload": {}},
                headers=self.headers,
            )
        database.assert_not_called()
        self.assertEqual(response.status_code, 401)
        self.assertIn("Create an account or sign in", response.get_json()["error"])

    def test_anonymous_customer_status_allows_public_browsing(self):
        response = self.client.get("/api/auth/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["authenticated"])

    def test_admin_password_reset_updates_only_the_matching_existing_account(self):
        connection = ScriptedConnection([(17,)])
        with patch.object(reset_admin_password.app, "db", return_value=connection), patch.object(
            reset_admin_password, "generate_password_hash", return_value="stored-hash"
        ):
            reset_admin_password.reset_admin_password(
                "  AZAGLOGRACELOVE@GMAIL.COM ", "a-long-admin-password", "a-long-admin-password"
            )

        self.assertEqual(connection.scripted_cursor.statements[0][1], ("azaglogracelove@gmail.com",))
        self.assertEqual(
            connection.scripted_cursor.statements[1],
            (
                "UPDATE administrators SET password_hash = :1, session_version = session_version + 1 WHERE id = :2",
                ("stored-hash", 17),
            ),
        )

    def test_admin_password_reset_rejects_mismatch_and_unknown_account(self):
        with patch.object(reset_admin_password.app, "db") as database:
            with self.assertRaisesRegex(ValueError, "do not match"):
                reset_admin_password.reset_admin_password("admin@example.com", "a-long-admin-password", "different")
            database.assert_not_called()

        connection = ScriptedConnection([None])
        with patch.object(reset_admin_password.app, "db", return_value=connection):
            with self.assertRaisesRegex(ValueError, "No administrator account"):
                reset_admin_password.reset_admin_password(
                    "admin@example.com", "a-long-admin-password", "a-long-admin-password"
                )
        self.assertEqual(len(connection.scripted_cursor.statements), 1)

    def test_flight_booking_rejects_identical_locations(self):
        self.sign_in_customer()
        with patch.object(application, "db", return_value=ScriptedConnection([(1, 0)])), patch.object(
            application, "FLIGHT_SEARCH_MODE", "demo"
        ):
            response = self.client.post(
                "/api/bookings",
                json={
                    "category": "flight",
                    "payload": {
                        "customer": {"name": "Passenger", "email": "passenger@example.com"},
                        "flight": {
                            "origin": "Accra",
                            "destination": "accra",
                            "departureDate": "2026-11-20",
                            "returnDate": "2026-11-22",
                            "tripType": "round-trip",
                            "passengers": 1,
                            "provider": "Demo",
                            "offerId": "demo-001",
                        },
                    },
                },
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("must be different", response.get_json()["error"])

    def test_flight_search_sends_validated_request_and_normalizes_amadeus_offers(self):
        departure = (datetime.now(timezone.utc) + timedelta(days=14)).date().isoformat()
        return_date = (datetime.now(timezone.utc) + timedelta(days=21)).date().isoformat()
        provider_response = {
            "data": [{
                "id": "1",
                "price": {"grandTotal": "1250.50", "currency": "USD"},
                "validatingAirlineCodes": ["XY"],
                "itineraries": [
                    {"duration": "PT8H", "segments": [{
                        "departure": {"iataCode": "ACC", "at": "2030-01-10T09:00:00"},
                        "arrival": {"iataCode": "LHR", "at": "2030-01-10T17:00:00"},
                        "carrierCode": "XY",
                        "number": "123",
                    }]},
                    {"duration": "PT8H", "segments": [{
                        "departure": {"iataCode": "LHR", "at": "2030-01-20T09:00:00"},
                        "arrival": {"iataCode": "ACC", "at": "2030-01-20T17:00:00"},
                        "carrierCode": "XY",
                        "number": "124",
                    }]},
                ],
            }],
            "dictionaries": {"carriers": {"XY": "Example Airways"}},
        }
        with patch.object(application, "FLIGHT_SEARCH_MODE", "amadeus"), patch.object(
            application, "AMADEUS_CLIENT_ID", "test-client"
        ), patch.object(
            application, "AMADEUS_CLIENT_SECRET", "test-secret"
        ), patch.object(application, "AMADEUS_API_BASE_URL", "https://test.api.amadeus.com"), patch.object(
            application, "_AMADEUS_ACCESS_TOKEN", ""
        ), patch.object(application, "_AMADEUS_ACCESS_TOKEN_EXPIRES_AT", 0), patch.object(
            application.requests, "post", return_value=FakeDuffelResponse({
                "access_token": "test-token", "expires_in": 1800,
            })
        ) as post, patch.object(
            application.requests, "get", return_value=FakeDuffelResponse(provider_response)
        ) as get:
            response = self.client.post(
                "/api/flights/search",
                json={
                    "origin": "ACC", "destination": "LHR", "passengers": 2, "travelClass": "Economy",
                    "tripType": "round-trip", "departureDate": departure, "returnDate": return_date,
                },
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        offer = response.get_json()["offers"][0]
        self.assertEqual(offer["airline"], "Example Airways")
        self.assertEqual(offer["totalAmount"], "1250.50")
        self.assertEqual(len(offer["slices"]), 2)
        self.assertEqual(offer["slices"][0]["flightNumbers"], ["XY123"])
        token_args = post.call_args.kwargs
        self.assertEqual(token_args["data"]["client_id"], "test-client")
        self.assertEqual(token_args["data"]["client_secret"], "test-secret")
        request_args = get.call_args.kwargs
        self.assertEqual(request_args["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(request_args["params"]["travelClass"], "ECONOMY")
        self.assertEqual(request_args["params"]["adults"], 2)
        self.assertEqual(request_args["params"]["returnDate"], return_date)

    def test_flight_search_uses_demo_offers_without_calling_provider(self):
        departure = (datetime.now(timezone.utc) + timedelta(days=14)).date().isoformat()
        return_date = (datetime.now(timezone.utc) + timedelta(days=21)).date().isoformat()
        with patch.object(application, "FLIGHT_SEARCH_MODE", "demo"), patch.object(
            application.requests, "post"
        ) as post, patch.object(application.requests, "get") as get:
            response = self.client.post(
                "/api/flights/search",
                json={
                    "origin": "ACC",
                    "destination": "LHR",
                    "passengers": 2,
                    "travelClass": "Economy",
                    "tripType": "round-trip",
                    "departureDate": departure,
                    "returnDate": return_date,
                },
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        self.assertTrue(result["demoMode"])
        self.assertEqual(len(result["offers"]), 3)
        self.assertEqual(result["offers"][0]["totalAmount"], "978.00")
        self.assertEqual(result["offers"][0]["slices"][0]["origin"], "ACC")
        self.assertEqual(result["offers"][0]["slices"][1]["destination"], "ACC")
        post.assert_not_called()
        get.assert_not_called()

    def test_flight_search_requires_amadeus_configuration_and_valid_routes(self):
        with patch.object(application, "FLIGHT_SEARCH_MODE", "amadeus"), patch.object(
            application, "AMADEUS_CLIENT_ID", ""
        ), patch.object(
            application, "AMADEUS_CLIENT_SECRET", ""
        ):
            response = self.client.post(
                "/api/flights/search",
                json={
                    "origin": "ACC",
                    "destination": "LHR",
                    "passengers": 1,
                    "travelClass": "Economy",
                    "tripType": "one-way",
                    "departureDate": (datetime.now(timezone.utc) + timedelta(days=14)).date().isoformat(),
                },
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("AMADEUS_CLIENT_ID", response.get_json()["error"])

        with patch.object(application, "FLIGHT_SEARCH_MODE", "amadeus"), patch.object(
            application, "AMADEUS_CLIENT_ID", "test-client"
        ), patch.object(
            application, "AMADEUS_CLIENT_SECRET", "test-secret"
        ), patch.object(application.requests, "post") as post:
            response = self.client.post(
                "/api/flights/search",
                json={"origin": "ACC", "destination": "ACC", "passengers": 1},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()

    def test_flight_search_handles_provider_errors_without_exposing_provider_body(self):
        with patch.object(application, "FLIGHT_SEARCH_MODE", "amadeus"), patch.object(
            application, "AMADEUS_CLIENT_ID", "test-client"
        ), patch.object(
            application, "AMADEUS_CLIENT_SECRET", "test-secret"
        ), patch.object(application, "amadeus_access_token", return_value="test-token"), patch.object(
            application.requests,
            "get",
            return_value=FakeDuffelResponse({}, application.requests.HTTPError("provider secret details")),
        ):
            response = self.client.post(
                "/api/flights/search",
                json={
                    "origin": "ACC",
                    "destination": "LHR",
                    "passengers": 1,
                    "travelClass": "Economy",
                    "tripType": "one-way",
                    "departureDate": (datetime.now(timezone.utc) + timedelta(days=14)).date().isoformat(),
                },
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("provider secret details", response.get_json()["error"])

    def test_contact_form_delivers_validated_message_by_email(self):
        with patch.object(application, "CONTACT_RECIPIENT", "support@example.com"), patch.object(
            application, "SMTP_HOST", "smtp.example.com"
        ), patch.object(application, "SMTP_FROM_EMAIL", "no-reply@example.com"), patch.object(
            application, "send_email"
        ) as send_email:
            response = self.client.post(
                "/api/contact",
                json={"name": "Customer", "email": "customer@example.com", "message": "Please call me."},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(send_email.call_args.args[0], "support@example.com")
        self.assertIn("customer@example.com", send_email.call_args.args[2])

    def test_password_reset_request_is_non_enumerating_and_uses_hashed_token(self):
        connection = ScriptedConnection([(21, "Customer")])
        with patch.object(application, "smtp_configured", return_value=True), patch.object(
            application, "db", return_value=connection
        ), patch.object(application, "send_email") as send_email:
            response = self.client.post(
                "/api/auth/password-reset/request",
                json={"email": "customer@example.com"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("If an active account", response.get_json()["message"])
        self.assertIn("UPDATE customers SET password_reset_token_hash", connection.last_cursor.statements[1][0])
        token_hash = connection.last_cursor.statements[1][1][0]
        email_body = send_email.call_args.args[2]
        token = re.search(r"reset_token=([A-Za-z0-9_-]+)", email_body).group(1)
        self.assertNotEqual(token, token_hash)
        self.assertEqual(len(token_hash), 64)

    def test_password_reset_token_can_only_update_account_once(self):
        token = "test-one-time-reset-token"
        connection = ScriptedConnection([(21,)])
        with patch.object(application, "db", return_value=connection):
            response = self.client.post(
                "/api/auth/password-reset/confirm",
                json={"token": token, "new_password": "a-new-long-password"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        update = connection.last_cursor.statements[1]
        self.assertIn("password_reset_token_hash = NULL", update[0])
        self.assertIn("session_version = session_version + 1", update[0])
        self.assertNotEqual(update[1][0], "a-new-long-password")

    def test_admin_can_edit_and_deactivate_customer_without_deleting_it(self):
        connection = ScriptedConnection([(8,), None])
        with self.client.session_transaction() as browser_session:
            browser_session["admin_id"] = 1
            browser_session["admin_session_version"] = 0
        with patch.object(
            application, "db", side_effect=[ScriptedConnection([(0,)]), connection]
        ):
            response = self.client.patch(
                "/api/admin/customers/8",
                json={"name": "Customer", "email": "customer@example.com", "phone": "+123", "active": "0"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["active"])
        self.assertIn("UPDATE customers SET name", connection.last_cursor.statements[2][0])
        self.assertEqual(connection.last_cursor.statements[2][1][-2], 0)

    def test_deactivated_customer_session_is_invalidated(self):
        connection = ScriptedConnection([(0, 0)])
        with self.client.session_transaction() as browser_session:
            browser_session["customer_id"] = 9
            browser_session["customer_session_version"] = 0
        with patch.object(application, "db", return_value=connection):
            response = self.client.get("/api/profile")
        self.assertEqual(response.status_code, 401)

    def test_customer_session_is_invalidated_after_password_change(self):
        connection = ScriptedConnection([(1, 1)])
        with self.client.session_transaction() as browser_session:
            browser_session["customer_id"] = 9
            browser_session["customer_session_version"] = 0
        with patch.object(application, "db", return_value=connection):
            response = self.client.get("/api/profile")
        self.assertEqual(response.status_code, 401)
        self.assertIn("no longer valid", response.get_json()["error"])

    def test_admin_session_is_invalidated_after_password_change(self):
        connection = ScriptedConnection([(1,)])
        with self.client.session_transaction() as browser_session:
            browser_session["admin_id"] = 9
            browser_session["admin_session_version"] = 0
        with patch.object(application, "db", return_value=connection):
            response = self.client.get("/api/admin/customers")
        self.assertEqual(response.status_code, 401)
        self.assertIn("no longer valid", response.get_json()["error"])

    def test_paypal_order_uses_only_locked_approved_oracle_quote(self):
        booking_ref = "RHY-FRT-AB12CD34"
        customer_state = ScriptedConnection([(1, 0)])
        payment_connection = ScriptedConnection([
            (4, "Approved", "freight", {"cargo": {}}),
            None,
            None,
            ("Approved", Decimal("19.99"), "USD"),
            None,
        ])
        order_response = FakeDuffelResponse({
            "access_token": "sandbox-access-token",
        })
        paypal_order = FakeDuffelResponse({
            "id": "ORDER123456",
            "status": "CREATED",
            "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER123456"}],
            "purchase_units": [{
                "custom_id": booking_ref,
                "amount": {"currency_code": "USD", "value": "19.99"},
            }],
        })
        with self.client.session_transaction() as browser_session:
            browser_session["customer_id"] = 3
            browser_session["customer_session_version"] = 0
        with patch.object(application, "PAYPAL_CLIENT_ID", "sandbox-client"), patch.object(
            application, "PAYPAL_CLIENT_SECRET", "sandbox-secret"
        ), patch.object(application, "PAYPAL_DEMO_MODE", False), patch.object(
            application, "db", side_effect=[customer_state, payment_connection]
        ), patch.object(
            application.requests, "post", return_value=order_response
        ) as paypal_auth, patch.object(
            application.requests, "request", return_value=paypal_order
        ) as paypal_request:
            response = self.client.post(
                f"/api/bookings/{booking_ref}/paypal-order",
                json={},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["order_id"], "ORDER123456")
        self.assertEqual(paypal_auth.call_args.kwargs["auth"], ("sandbox-client", "sandbox-secret"))
        self.assertEqual(paypal_request.call_args.kwargs["json"]["purchase_units"][0]["amount"]["value"], "19.99")
        self.assertIn("INSERT INTO payments", payment_connection.last_cursor.statements[-1][0])

    def test_paystack_mobile_money_order_uses_approved_ghs_quote(self):
        booking_ref = "RHY-FRT-AB12CD34"
        customer_state = ScriptedConnection([(1, 0)])
        payment_connection = ScriptedConnection([
            (4, "Approved", "freight", {"cargo": {}}),
            ("Approved", Decimal("70.00"), "GHS"),
            None,
            None,
            None,
            ("customer@example.com",),
        ])
        paystack_response = FakeDuffelResponse({
            "status": True,
            "data": {
                "reference": "RHY-PAY-0123456789ABCDEF0123456789ABCDEF",
                "authorization_url": "https://checkout.paystack.com/checkout-token",
            },
        })
        with self.client.session_transaction() as browser_session:
            browser_session["customer_id"] = 3
            browser_session["customer_session_version"] = 0
        with patch.object(application, "PAYSTACK_SECRET_KEY", "sk_test_example"), patch.object(
            application, "PAYSTACK_DEMO_MODE", False
        ), patch.object(application, "db", side_effect=[customer_state, payment_connection]), patch.object(
            application.uuid, "uuid4",
            return_value=type("GeneratedUUID", (), {
                "hex": "0123456789abcdef0123456789abcdef"
            })(),
        ), patch.object(
            application.requests, "request", return_value=paystack_response
        ) as paystack_request:
            response = self.client.post(
                f"/api/bookings/{booking_ref}/paystack-mobile-money",
                json={},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["checkout_url"], "https://checkout.paystack.com/checkout-token")
        request_body = paystack_request.call_args.kwargs["json"]
        self.assertEqual(request_body["amount"], 7000)
        self.assertEqual(request_body["currency"], "GHS")
        self.assertEqual(request_body["channels"], ["mobile_money"])
        self.assertEqual(request_body["metadata"]["booking_reference"], booking_ref)
        insert = next(
            binds for statement, binds in payment_connection.last_cursor.statements
            if "INSERT INTO payments" in statement
        )
        self.assertEqual(insert[1], "70.00")
        self.assertEqual(insert[2], "RHY-PAY-0123456789ABCDEF0123456789ABCDEF")

    def test_paystack_mobile_money_cannot_overlap_another_pending_provider(self):
        customer_state = ScriptedConnection([(1, 0)])
        payment_connection = ScriptedConnection([
            (4, "Approved", "freight", {"cargo": {}}),
            ("Approved", Decimal("70.00"), "GHS"),
            None,
            (12,),
        ])
        with self.client.session_transaction() as browser_session:
            browser_session["customer_id"] = 3
            browser_session["customer_session_version"] = 0
        with patch.object(application, "PAYSTACK_SECRET_KEY", "sk_test_example"), patch.object(
            application, "db", side_effect=[customer_state, payment_connection]
        ), patch.object(application.requests, "request") as paystack_request:
            response = self.client.post(
                "/api/bookings/RHY-FRT-AB12CD34/paystack-mobile-money",
                json={},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 409)
        paystack_request.assert_not_called()

    def test_paystack_verification_marks_paid_only_for_matching_mobile_money_capture(self):
        booking_ref = "RHY-FRT-AB12CD34"
        transaction_reference = "RHY-PAY-0123456789ABCDEF0123456789ABCDEF"
        customer_state = ScriptedConnection([(1, 0)])
        payment_connection = ScriptedConnection([
            (4,),
            (4, booking_ref, "Approved", "freight", {"cargo": {}}),
            (10, 4, "PENDING", Decimal("70.00"), "GHS"),
            ("Approved", Decimal("70.00"), "GHS"),
        ])
        paystack_response = FakeDuffelResponse({
            "status": True,
            "data": {
                "reference": transaction_reference,
                "status": "success",
                "amount": 7000,
                "currency": "GHS",
                "channel": "mobile_money",
                "metadata": {"booking_reference": booking_ref},
            },
        })
        with self.client.session_transaction() as browser_session:
            browser_session["customer_id"] = 3
            browser_session["customer_session_version"] = 0
        with patch.object(application, "PAYSTACK_SECRET_KEY", "sk_test_example"), patch.object(
            application, "db", side_effect=[customer_state, payment_connection]
        ), patch.object(
            application.requests, "request", return_value=paystack_response
        ) as paystack_request:
            response = self.client.post(
                "/api/payments/paystack/verify",
                json={"reference": transaction_reference},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(paystack_request.call_args.args[0], "GET")
        self.assertIn(transaction_reference, paystack_request.call_args.args[1])
        statements = [statement for statement, _ in payment_connection.last_cursor.statements]
        self.assertTrue(any("UPDATE payments SET status = 'COMPLETED'" in sql for sql in statements))
        self.assertTrue(any("UPDATE bookings SET status = 'Paid'" in sql for sql in statements))

    def test_paystack_transaction_rejects_amount_mismatch(self):
        transaction = {
            "reference": "RHY-PAY-0123456789ABCDEF0123456789ABCDEF",
            "status": "success",
            "amount": 6999,
            "currency": "GHS",
            "channel": "mobile_money",
            "metadata": {"booking_reference": "RHY-FRT-AB12CD34"},
        }
        self.assertFalse(application.verify_paystack_transaction(
            transaction,
            "RHY-PAY-0123456789ABCDEF0123456789ABCDEF",
            "RHY-FRT-AB12CD34",
            "70.00",
        ))

    def test_paypal_capture_marks_paid_only_after_matching_completed_capture(self):
        booking_ref = "RHY-FRT-AB12CD34"
        customer_state = ScriptedConnection([(1, 0)])
        payment_connection = ScriptedConnection([
            (4,),
            (4, booking_ref, "Approved", "freight", {"cargo": {}}),
            (10, 4, "PENDING", Decimal("19.99"), "USD"),
            ("Approved", Decimal("19.99"), "USD"),
        ])
        paypal_order = {
            "id": "ORDER123456",
            "status": "APPROVED",
            "purchase_units": [{
                "custom_id": booking_ref,
                "amount": {"currency_code": "USD", "value": "19.99"},
            }],
        }
        paypal_capture = {
            "id": "ORDER123456",
            "status": "COMPLETED",
            "purchase_units": [{
                "custom_id": booking_ref,
                "amount": {"currency_code": "USD", "value": "19.99"},
                "payments": {"captures": [{
                    "status": "COMPLETED",
                    "amount": {"currency_code": "USD", "value": "19.99"},
                }]},
            }],
        }
        with self.client.session_transaction() as browser_session:
            browser_session["customer_id"] = 3
            browser_session["customer_session_version"] = 0
        with patch.object(application, "PAYPAL_CLIENT_ID", "sandbox-client"), patch.object(
            application, "PAYPAL_CLIENT_SECRET", "sandbox-secret"
        ), patch.object(application, "db", side_effect=[customer_state, payment_connection]), patch.object(
            application.requests, "post", return_value=FakeDuffelResponse({"access_token": "sandbox-token"})
        ), patch.object(
            application.requests, "request",
            side_effect=[FakeDuffelResponse(paypal_order), FakeDuffelResponse(paypal_capture)],
        ):
            response = self.client.post(
                "/api/payments/paypal/capture",
                json={"order_id": "ORDER123456"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["status"], "Paid")
        statements = [statement for statement, _ in payment_connection.last_cursor.statements]
        self.assertTrue(any("UPDATE payments SET status = 'COMPLETED'" in sql for sql in statements))
        self.assertTrue(any("UPDATE bookings SET status = 'Paid'" in sql for sql in statements))

    def test_paypal_decline_marks_payment_failed_without_marking_booking_paid(self):
        booking_ref = "RHY-FRT-AB12CD34"
        customer_state = ScriptedConnection([(1, 0)])
        payment_connection = ScriptedConnection([
            (4,),
            (4, booking_ref, "Approved", "freight", {"cargo": {}}),
            (10, 4, "PENDING", Decimal("19.99"), "USD"),
            ("Approved", Decimal("19.99"), "USD"),
        ])
        purchase_unit = {
            "custom_id": booking_ref,
            "amount": {"currency_code": "USD", "value": "19.99"},
        }
        paypal_order = {"id": "ORDER123456", "status": "APPROVED", "purchase_units": [purchase_unit]}
        paypal_capture = {"id": "ORDER123456", "status": "VOIDED", "purchase_units": [purchase_unit]}
        with self.client.session_transaction() as browser_session:
            browser_session["customer_id"] = 3
            browser_session["customer_session_version"] = 0
        with patch.object(application, "PAYPAL_CLIENT_ID", "sandbox-client"), patch.object(
            application, "PAYPAL_CLIENT_SECRET", "sandbox-secret"
        ), patch.object(application, "db", side_effect=[customer_state, payment_connection]), patch.object(
            application.requests, "post", return_value=FakeDuffelResponse({"access_token": "sandbox-token"})
        ), patch.object(
            application.requests, "request",
            side_effect=[FakeDuffelResponse(paypal_order), FakeDuffelResponse(paypal_capture)],
        ):
            response = self.client.post(
                "/api/payments/paypal/capture",
                json={"order_id": "ORDER123456"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 409)
        statements = [statement for statement, _ in payment_connection.last_cursor.statements]
        self.assertTrue(any("UPDATE payments SET status = 'FAILED'" in sql for sql in statements))
        self.assertFalse(any("UPDATE bookings SET status = 'Paid'" in sql for sql in statements))

    def test_customer_can_cancel_pending_paypal_order(self):
        customer_state = ScriptedConnection([(1, 0)])
        payment_connection = ScriptedConnection([(4,), (4,), (10, "PENDING")])
        with self.client.session_transaction() as browser_session:
            browser_session["customer_id"] = 3
            browser_session["customer_session_version"] = 0
        with patch.object(application, "db", side_effect=[customer_state, payment_connection]):
            response = self.client.post(
                "/api/payments/paypal/cancel",
                json={"order_id": "ORDER123456"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["status"], "CANCELLED")
        statements = [statement for statement, _ in payment_connection.last_cursor.statements]
        self.assertTrue(any("UPDATE payments SET status = 'CANCELLED'" in sql for sql in statements))

    def test_admin_data_is_not_available_without_admin_session(self):
        response = self.client.get("/api/admin/customers")
        self.assertEqual(response.status_code, 401)

    def test_portal_sign_in_links_to_separate_registration_page(self):
        portal = self.client.get("/portal.html")
        portal_html = portal.get_data(as_text=True)
        portal.close()
        self.assertIn('id="login-form"', portal_html)
        self.assertNotIn('id="register-form"', portal_html)
        self.assertIn('href="register.html"', portal_html)

        registration = self.client.get("/register.html")
        registration_html = registration.get_data(as_text=True)
        registration.close()
        self.assertIn('id="register-form"', registration_html)
        self.assertIn('href="portal.html"', registration_html)

    def test_admin_login_is_not_linked_from_customer_portal(self):
        portal = self.client.get("/portal.html")
        portal_html = portal.get_data(as_text=True)
        portal.close()
        self.assertNotIn("Administrator sign in", portal_html)
        self.assertNotIn('href="admin.html"', portal_html)

        admin = self.client.get("/admin")
        self.assertEqual(admin.status_code, 200)
        self.assertIn("Administrator sign in", admin.get_data(as_text=True))
        admin.close()

    def test_only_allowlisted_static_files_are_public(self):
        for path, expected_status in (
            ("/css/style.css", 200), ("/admin", 200), ("/admin.html", 404), ("/register.html", 200),
            ("/js/booking-access.js", 200), ("/js/register.js", 200),
            ("/.env", 404), ("/app.py", 404),
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, expected_status)
            response.close()

    def test_clearing_documents_are_saved_outside_public_root(self):
        self.sign_in_customer()
        connection = FakeConnection()
        customer_state = ScriptedConnection([(1, 0)])
        details = {
            "customer": {"name": "Customer", "email": "custom@example.com", "phone": "+123"},
            "shipment": {"description": "Equipment", "origin": "Accra", "destination": "London", "direction": "Import", "method": "Air freight"},
        }
        with tempfile.TemporaryDirectory() as storage:
            with patch.object(application, "db", side_effect=[customer_state, connection]), patch.object(application, "UPLOAD_DIR", Path(storage)):
                response = self.client.post(
                    "/api/bookings",
                    data={
                        "category": "clearing",
                        "payload": json.dumps(details),
                        "commercial-invoice": (io.BytesIO(b"%PDF-invoice"), "invoice.pdf"),
                        "packing-list": (io.BytesIO(b"%PDF-packing"), "packing.pdf"),
                        "shipping-documents": (io.BytesIO(b"%PDF-shipping"), "shipping.pdf"),
                    },
                    headers=self.headers,
                    content_type="multipart/form-data",
                )
                self.assertEqual(response.status_code, 201, response.get_json())
                names = [path.name for path in Path(storage).iterdir()]
                self.assertEqual(len(names), 3)
                self.assertTrue(all(path.suffix == ".pdf" for path in map(Path, names)))
                self.assertFalse(Path(storage).is_relative_to(application.BASE_DIR))

    def test_unknown_tracking_number_uses_database_and_returns_not_found(self):
        connection = FakeConnection()
        with patch.object(application, "db", return_value=connection):
            response = self.client.get("/api/track/RHY-NOTFOUND")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Tracking number not found.")

    def test_bad_document_signature_is_rejected_without_leaving_partial_uploads(self):
        self.sign_in_customer()
        details = {
            "customer": {"name": "Customer", "email": "custom@example.com"},
            "shipment": {"description": "Equipment", "direction": "Import", "method": "Air freight"},
        }
        with tempfile.TemporaryDirectory() as storage, patch.object(
            application, "db", return_value=ScriptedConnection([(1, 0)])
        ), patch.object(application, "UPLOAD_DIR", Path(storage)):
            response = self.client.post(
                "/api/bookings",
                data={
                    "category": "clearing",
                    "payload": json.dumps(details),
                    "commercial-invoice": (io.BytesIO(b"%PDF-invoice"), "invoice.pdf"),
                    "packing-list": (io.BytesIO(b"%PDF-packing"), "packing.pdf"),
                    "shipping-documents": (io.BytesIO(b"not a pdf"), "shipping.pdf"),
                },
                headers=self.headers,
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(list(Path(storage).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
