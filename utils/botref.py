"""
utils/botref.py — legatura dintre bot si dashboard (care ruleaza in acelasi proces).

Botul si dashboard-ul ruleaza impreuna (vezi run.py), dar pana acum dashboard-ul
n-avea nicio referinta directa la obiectul `bot` — de-aia pagina "Toate comenzile"
era o lista scrisa de mana in app.py, care ramanea in urma de fiecare data cand
se adauga un modul nou.

Acest fisier e doar un "cui" pe care main.py agata botul, iar dashboard/app.py
il citeste. Nu e nevoie de nimic special (nu e proces separat, nu e API) —
doar o variabila de modul, vazuta la fel din orice fisier care face
`from utils import botref`.
"""

bot = None  # setat de main.py imediat ce botul e creat
