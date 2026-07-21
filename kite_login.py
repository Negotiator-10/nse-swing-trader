"""
kite_login.py - the USER-run daily login helper (Phase 4+).

Kite Connect access tokens expire early every morning (SEBI session rules), so each
trading day starts with this 2-minute ritual, run BY THE USER in their own terminal:

    C:/Users/Admin/anaconda3/python.exe kite_login.py

  1. It reads KITE_API_KEY / KITE_API_SECRET from the environment, or -- if absent --
     from the gitignored secrets.env next to this file (never from source code).
  2. It prints the Kite login URL. Open it in a browser, log in (Client ID + password
     + 2FA -- entered ONLY on Zerodha's own page, never here).
  3. The browser redirects to your app's redirect URL with ?request_token=XXXX in it.
     Paste that request_token here when prompted (it is single-use, ~few minutes TTL).
  4. The script exchanges it for the day's access token and WRITES it into secrets.env
     (never printed, never logged), then verifies the session with a profile call.

SECURITY: this file contains NO secrets and never prints one. The access token lands
only in secrets.env, which is gitignored. Claude/automation only ever checks that the
env vars EXIST -- values are never read into chat or logs.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SECRETS = ROOT / "secrets.env"


def load_secrets_env() -> dict:
    """Parse secrets.env into a dict (values stay in-process, never printed)."""
    vals = {}
    if SECRETS.exists():
        for line in SECRETS.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([^#=]+)=(.*)$", line)
            if m:
                vals[m.group(1).strip()] = m.group(2).strip()
    return vals


def save_token(token: str) -> None:
    """Write/replace KITE_ACCESS_TOKEN in secrets.env, preserving everything else."""
    lines = SECRETS.read_text(encoding="utf-8").splitlines() if SECRETS.exists() else []
    out, done = [], False
    for line in lines:
        if re.match(r"^\s*KITE_ACCESS_TOKEN\s*=", line):
            out.append(f"KITE_ACCESS_TOKEN={token}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"KITE_ACCESS_TOKEN={token}")
    SECRETS.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    from kiteconnect import KiteConnect

    env = load_secrets_env()
    api_key = os.environ.get("KITE_API_KEY") or env.get("KITE_API_KEY", "")
    api_secret = os.environ.get("KITE_API_SECRET") or env.get("KITE_API_SECRET", "")
    if not api_key or not api_secret or "your_api" in api_key:
        print("ERROR: fill KITE_API_KEY and KITE_API_SECRET in secrets.env first")
        print(f"       (copy secrets.env.example -> {SECRETS.name} and edit it)")
        return 1

    kite = KiteConnect(api_key=api_key)
    print("=" * 64)
    print("  KITE DAILY LOGIN")
    print("=" * 64)
    print("\n  1. Open this URL in your browser and log in:\n")
    print(f"     {kite.login_url()}\n")
    print("  2. After login you are redirected to your app's redirect URL;")
    print("     copy the request_token=... value from that URL.\n")
    request_token = input("  3. Paste request_token here: ").strip()
    if not request_token:
        print("ERROR: empty request_token")
        return 1

    try:
        session = kite.generate_session(request_token, api_secret=api_secret)
    except Exception as e:
        print(f"ERROR: token exchange failed: {type(e).__name__}: {e}")
        print("       (request tokens are single-use and expire in minutes --")
        print("        redo the browser login and paste a FRESH token)")
        return 1

    token = session["access_token"]
    save_token(token)
    print(f"\n  access token saved to {SECRETS.name} (not printed).")

    # verify the session actually works (read-only profile call)
    kite.set_access_token(token)
    prof = kite.profile()
    print(f"  session verified for user_id={prof.get('user_id','?')} "
          f"({prof.get('user_name','?')})")
    print("  token is valid until early tomorrow morning; rerun this script daily.")
    print("\n  Before running the paper trader, load the env in your shell:")
    print("    PowerShell: Get-Content secrets.env | ForEach-Object { if ($_ -match "
          "'^\\s*([^#=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1].Trim(), "
          "$matches[2].Trim()) } }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
