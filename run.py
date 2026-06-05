"""
run.py — porneste botul SI dashboardul deodata, dintr-un singur proces.

Local:  python run.py   -> dashboard pe http://localhost:5000
Pe Render: ruleaza la fel; portul e luat automat din variabila PORT.

Botul ruleaza in firul principal, dashboardul intr-un thread separat.
Ca sa opresti tot (local): Ctrl + C.
"""

import os
import asyncio
import threading

# Render (si alte hosturi) dau portul prin variabila de mediu PORT.
# Local, daca PORT lipseste, folosim 5000.
PORT = int(os.getenv("PORT", "50000"))


# --- dashboardul (Flask) ruleaza intr-un thread separat ---
def start_dashboard():
    from dashboard.app import app
    # debug=False si use_reloader=False sunt obligatorii cand rulezi in thread
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


# --- botul ruleaza in firul principal ---
def start_bot():
    import main
    asyncio.run(main.main())


if __name__ == "__main__":
    print(f"Pornesc dashboardul pe portul {PORT} ...")
    threading.Thread(target=start_dashboard, daemon=True).start()

    print("Pornesc botul ...")
    start_bot()
