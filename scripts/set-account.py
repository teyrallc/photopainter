#!/usr/bin/env python3
"""Vignette — set the sign-in address and password from the device itself.

Settings → Account does the same thing through the console, and that is the
normal way. This is the way in when the console cannot be reached: the address
was mistyped during pairing, the password is gone, or the panel is not in front
of whoever needs to sign in, so the on-screen code that guards the console's
own flow is no use.

It has to be run on the device, as the account that owns the checkout:

    cd /home/soongweng/photopainter
    sudo -u vignette python3 scripts/set-account.py --email you@example.com
    sudo systemctl restart vignette

The password is asked for interactively and never appears in a shell history.
Leave it blank to keep the current one.
"""
import argparse
import getpass
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "web"))


def main():
    parser = argparse.ArgumentParser(
        description="Set the Vignette sign-in address and password.")
    parser.add_argument("--email", help="new sign-in address")
    parser.add_argument("--password",
                        help="new password (omit to be prompted; blank keeps "
                             "the current one)")
    parser.add_argument("--show", action="store_true",
                        help="print the current address and exit")
    args = parser.parse_args()

    from services.config import Config
    from werkzeug.security import generate_password_hash

    config = Config(os.path.join(REPO, "config.json"))

    if args.show or (not args.email and args.password is None):
        print(f"Sign-in address: {config.get('admin_email') or '(not set)'}")
        if args.show:
            return 0
        print("\nNothing to change. Pass --email and/or --password.")
        return 0

    if args.email:
        if "@" not in args.email:
            print(f"error: {args.email!r} is not an email address", file=sys.stderr)
            return 1
        config.set("admin_email", args.email)
        print(f"Sign-in address → {args.email}")

    password = args.password
    if password is None:
        password = getpass.getpass("New password (blank = keep current): ")
    if password:
        if len(password) < 6:
            print("error: password must be at least 6 characters", file=sys.stderr)
            return 1
        again = args.password if args.password is not None else getpass.getpass("Again: ")
        if again != password:
            print("error: passwords do not match", file=sys.stderr)
            return 1
        config.set("admin_password_hash", generate_password_hash(password))
        print("Password changed.")

    # The running service holds the config in memory, so the change only shows
    # up on the next start.
    print("\nDone. Restart the service for it to take effect:")
    print("    sudo systemctl restart vignette")
    return 0


if __name__ == "__main__":
    sys.exit(main())
