"""
run.py — porneste botul SI dashboardul deodata, dintr-un singur terminal.

Foloseste:  python run.py

Botul ruleaza in firul principal, iar dashboardul intr-un thread separat.
Ca sa opresti tot: Ctrl + C.
"""

import asyncio
import threading

# --- dashboardul (Flask) ruleaza intr-un thread separat ---
def start_dashboard():
    from dashboard.app import app
    # debug=False si use_reloader=False sunt obligatorii cand rulezi in thread
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


# --- botul ruleaza in firul principal ---
def start_bot():
    import main
    asyncio.run(main.main())


if __name__ == "__main__":
    print("Pornesc dashboardul pe http://localhost:5000 ...")
    threading.Thread(target=start_dashboard, daemon=True).start()

    print("Pornesc botul ...")
    start_bot()
