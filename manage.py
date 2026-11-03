"""
Comenzi utile din terminal (pe server):

  python manage.py public-key                  -> afișează cheia publică (o punem în aplicație)
  python manage.py create 30 zile [nr] [notă]  -> creează chei fără pagina web
  python manage.py list                        -> listează utilizatorii și licențele
"""

import sys

import app as server


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]

    if cmd == "public-key":
        print(server.public_key_b64())

    elif cmd == "create":
        if len(sys.argv) < 4:
            print("Folosire: python manage.py create <durată> <ore|zile|luni|permanent> [câte] [notă]")
            return
        amount, unit = int(sys.argv[2]), sys.argv[3]
        count = int(sys.argv[4]) if len(sys.argv) > 4 else 1
        note = " ".join(sys.argv[5:])
        duration = server.parse_duration(amount, unit)
        for key in server.create_keys(duration, count, note, "cli"):
            print(key)

    elif cmd == "list":
        with server.db() as conn:
            for u in conn.execute("SELECT * FROM users ORDER BY expires_at DESC"):
                state = "REVOCATĂ" if u["revoked"] else server.remaining(u["expires_at"])
                print(f"{u['discord_id']:<20} {u['username']:<24} expiră {server.fmt_ts(u['expires_at'])}  ({state})")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
