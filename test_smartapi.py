"""One-off SmartAPI sanity check.

Run this after filling in ANGELONE_* in .env. It exercises the four parts
of the integration that can fail independently:

  1. Login (api_key + client_code + pin + TOTP)
  2. Instrument master download (Symbol -> Token mapping)
  3. A single historical candle fetch (RELIANCE, last ~10 days)
  4. Pipeline integration through our normalized DataFrame shape

If all four succeed, your creds are good and you can switch to Angel One
in production. If any step fails, the error message tells you which
piece to fix.
"""

from __future__ import annotations

import sys

# Windows cp1252 console can't print INR symbol etc. Force UTF-8 on stdout.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from breakout.config import load_config
from breakout.data.fetcher import AngelOneFetcher


def main() -> None:
    cfg = load_config()
    creds = cfg.credentials
    if not creds.has_angelone:
        print("[FAIL] ANGELONE_* env vars are missing — fill in .env first")
        print("  See .env.example for the four required keys.")
        return

    print(f"[*] Using Client Code: {creds.angelone_client_code}")
    fetcher = AngelOneFetcher(
        api_key=creds.angelone_api_key,
        client_code=creds.angelone_client_code,
        pin=creds.angelone_pin,
        totp_secret=creds.angelone_totp_secret,
    )

    print("[*] Logging in (this generates a TOTP token automatically)...")
    fetcher._connect()
    print("  [OK] login OK")

    print("[*] Loading instrument master (~5 MB JSON)...")
    instruments = fetcher._load_instruments()
    print(f"  [OK] loaded {len(instruments)} NSE equity tokens")
    if "RELIANCE" not in instruments:
        print("  [FAIL] unexpected: RELIANCE token not found")
        return

    print("[*] Fetching RELIANCE history (10 days)...")
    df = fetcher.fetch_history("RELIANCE", days=10)
    print(f"  [OK] got {len(df)} bars, last close ₹{df['close'].iloc[-1]:.2f}")
    print(df.tail(3))

    print("\n[OK] SmartAPI integration verified. Safe to use in production.")


if __name__ == "__main__":
    main()
