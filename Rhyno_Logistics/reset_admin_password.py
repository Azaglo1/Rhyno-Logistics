import argparse
import getpass
import re
import sys

import app
from werkzeug.security import generate_password_hash


def reset_admin_password(email, password, confirmation):
    normalized_email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized_email):
        raise ValueError("Enter a valid administrator email address.")
    if password != confirmation:
        raise ValueError("The passwords do not match.")
    if len(password) < 12:
        raise ValueError("Use a password with at least 12 characters.")

    conn = app.db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM administrators WHERE email = :1", (normalized_email,))
            administrator = cur.fetchone()
            if not administrator:
                raise ValueError("No administrator account exists for that email.")
            cur.execute(
                "UPDATE administrators SET password_hash = :1, session_version = session_version + 1 WHERE id = :2",
                (generate_password_hash(password), administrator[0]),
            )
        conn.commit()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Reset an existing Rhyno administrator password.")
    parser.add_argument("email", help="Email address of the existing administrator account")
    args = parser.parse_args()

    try:
        password = getpass.getpass("New administrator password (12+ characters): ")
        confirmation = getpass.getpass("Confirm new administrator password: ")
        reset_admin_password(args.email, password, confirmation)
    except (ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print("Administrator password updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
