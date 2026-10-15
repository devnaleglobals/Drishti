
import os
import smtplib
import socket
import ssl
from email.message import EmailMessage
from getpass import getpass

print("=== SMTP Server Diagnostic ===")

# Enter your SMTP settings
SMTP_HOST = input("SMTP host (e.g. smtp.gmail.com): ").strip()
SMTP_PORT = int(input("SMTP port (587 or 465): ").strip())
SMTP_USER = input("SMTP username/email: ").strip()
SMTP_PASSWORD = getpass("SMTP password/app password: ")

# Set SMTP_USE_SSL=true for port 465.
# Port 587 normally uses STARTTLS.
USE_SSL = SMTP_PORT == 465

try:
    print(f"\n1. Connecting to {SMTP_HOST}:{SMTP_PORT}...")

    if USE_SSL:
        server = smtplib.SMTP_SSL(
            SMTP_HOST,
            SMTP_PORT,
            timeout=15,
            context=ssl.create_default_context()
        )
    else:
        server = smtplib.SMTP(
            SMTP_HOST,
            SMTP_PORT,
            timeout=15
        )

    with server:
        server.ehlo()
        print("SUCCESS: SMTP server reachable")

        if not USE_SSL:
            print("2. Starting TLS...")
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            print("SUCCESS: TLS established")

        print("3. Authenticating...")
        server.login(SMTP_USER, SMTP_PASSWORD)
        print("SUCCESS: SMTP credentials accepted")

        send_test = input(
            "\nSend a test email? (y/n): "
        ).strip().lower()

        if send_test == "y":
            recipient = input("Recipient email: ").strip()

            msg = EmailMessage()
            msg["Subject"] = "SMTP Configuration Test"
            msg["From"] = SMTP_USER
            msg["To"] = recipient
            msg.set_content(
                "SMTP connection, authentication and email "
                "submission test successful."
            )

            server.send_message(msg)
            print(f"SUCCESS: Test email submitted to {recipient}")

except smtplib.SMTPAuthenticationError as e:
    print(f"AUTHENTICATION FAILED: {e}")
    print("Check username, password and provider requirements.")

except (socket.timeout, TimeoutError):
    print("TIMEOUT: SMTP server did not respond.")
    print("Check host, port and outbound network restrictions.")

except socket.gaierror as e:
    print(f"DNS ERROR: Cannot resolve SMTP hostname: {e}")

except OSError as e:
    print(f"NETWORK/SSL ERROR: {type(e).__name__}: {e}")
    print("Check internet connectivity, port access and TLS settings.")

except smtplib.SMTPException as e:
    print(f"SMTP ERROR: {type(e).__name__}: {e}")

except ValueError as e:
    print(f"CONFIGURATION ERROR: {e}")
