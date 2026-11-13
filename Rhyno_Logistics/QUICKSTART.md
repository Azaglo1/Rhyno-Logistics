# Quick Start Guide

## 1. Install Dependencies
```powershell
cd "C:\Users\LENOVO\OneDrive\Desktop\Rhyo tech logistics\Rhyno_Logistics"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Configure Environment
```powershell
copy .env.example .env
```
Edit `.env` with your values. For local setup, also set a unique administrator email and password:
- `ORACLE_USER` - Oracle database user/schema
- `ORACLE_PASSWORD` - Oracle password
- `ORACLE_DSN` - EZ Connect string (e.g. `localhost:1521/XEPDB1` or `host:1521/service_name`)
- `RHYNO_SECRET_KEY` - A long random secret; required in production
- `FLIGHT_SEARCH_MODE` - Defaults to `demo`, which serves fictional sample offers and allows test requests to be saved locally; no airline is contacted or ticket reserved
- `AMADEUS_CLIENT_ID` and `AMADEUS_CLIENT_SECRET` - Optional Amadeus Self-Service credentials, required only when `FLIGHT_SEARCH_MODE=amadeus`
- `AMADEUS_API_BASE_URL` - Optional API host; defaults to the Amadeus test environment (`https://test.api.amadeus.com`)
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, and `CONTACT_RECIPIENT` - SMTP delivery for contact and password-reset messages
- `PUBLIC_BASE_URL` - Public origin used in password-reset and PayPal return URLs (default `http://127.0.0.1:5000`)
- `PAYPAL_MODE=sandbox`, `PAYPAL_CLIENT_ID`, and `PAYPAL_CLIENT_SECRET` - PayPal sandbox checkout credentials
- `PAYSTACK_SECRET_KEY` - Paystack test or live secret key for GHS mobile-money checkout
- `ADMIN_EMAIL` and `ADMIN_PASSWORD` - Optional administrator account created on first startup

## 3. Set Up Database (SQL*Plus)
Create the schema in your empty Oracle database using SQL*Plus. The bootstrap script refuses to run if Rhyno tables already exist, to protect existing records:
```powershell
sqlplus your_user/your_password@//host:1521/service_name @schema_oracle.sql
```
(Or: `sqlplus your_user/your_password@tns_alias @schema_oracle.sql`)

For an existing Rhyno schema, back up the database and run `schema_upgrade.sql` instead. It adds account recovery, PayPal metadata, and password-change session revocation without dropping existing data.

## 4. Run the App
```powershell
python app.py
```
Server starts at **http://localhost:5000** and creates the configured administrator if one does not exist.

## 5. Open in Browser
- **Main site**: http://localhost:5000
- **Customer portal**: http://localhost:5000/portal.html

## Administrator
Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` before the first run, then visit **http://localhost:5000/admin**. The administration console is separate from customer-facing navigation. There is no built-in default administrator password.

To reset the password for an existing administrator, run this from the project directory. The password is entered locally without being echoed:
```powershell
python reset_admin_password.py azaglogracelove@gmail.com
```
Replace the email with the administrator account you need to recover. The password must be at least 12 characters.
Password changes and resets invalidate existing customer and administrator sessions.

Flight search defaults to clearly labeled, fictional demo itineraries. You can submit a test request, which is saved in Rhyno as a demo record; no flight API is called, no airline is contacted, and no ticket is reserved. The Amadeus Self-Service integration remains available by explicitly setting `FLIGHT_SEARCH_MODE=amadeus` and adding credentials. Its default API host is Amadeus' test environment, where results may be limited or differ from live fares. Keep credentials in the server `.env` and never share them publicly. Amadeus submissions create booking requests only; they do not reserve seats or issue tickets. PayPal runs in sandbox mode by default and accepts only admin-approved quotations. Contact delivery and password reset require valid SMTP settings. Clearing documents are saved outside the public static directory and can only be downloaded by an administrator.

Paystack mobile-money checkout is available for administrator-approved quotations in GHS. Set `PAYSTACK_SECRET_KEY` to a Paystack test secret for testing, then use a live secret only in production. Customers complete payment in Paystack-hosted checkout; the server verifies the transaction before marking the booking paid. Existing schemas need the `schema_upgrade.sql` migration to add the saved checkout URL column.
