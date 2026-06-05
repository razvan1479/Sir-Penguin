# 🤖 Bot Discord Modular — Welcome + Invitatii

Bot Discord structurat pe module (Cogs). Momentan are:
- **Welcome** — mesaj de bun venit cu avatar, banner si cine a invitat membrul
- **Invitatii** — sistem complet de tip „Invite Tracker” cu leaderboard pe perioade
- **Dashboard web** — configurare welcome + vizualizare leaderboard

## 📁 Structura

```
discord-bot/
├── main.py                 # porneste botul + incarca automat tot din cogs/
├── .env                    # tokenul (secret!)
├── requirements.txt
├── cogs/                   # fiecare modul = un fisier independent
│   ├── welcome.py
│   └── invites.py
├── utils/
│   └── storage.py          # depozit comun de date (bot + dashboard)
├── data/
│   └── store.json          # se creeaza automat; aici se salveaza tot
└── dashboard/
    ├── app.py
    └── templates/          # index, guild (setari), leaderboard
```

## 🚀 Pornire

1. Creeaza botul pe https://discord.com/developers/applications
2. La **Bot → Privileged Gateway Intents**, activeaza **SERVER MEMBERS** si **MESSAGE CONTENT**.
3. La invitarea botului pe server, da-i permisiunea **Manage Server** (obligatoriu pentru invitatii!).
4. Pune tokenul real in `.env`.
5. Instaleaza: `pip install -r requirements.txt`
6. Porneste botul: `python main.py`
7. (Optional) Dashboard, in alt terminal: `python dashboard/app.py` → http://localhost:5000

## 💬 Comenzi

### Welcome
| Comanda | Ce face |
|---|---|
| `/welcome test` | Trimite un mesaj de test |
| `/welcome channel <canal>` | Seteaza rapid canalul de bun venit |

(Configurarea completa — mesaj, culoare, avatar, banner, invitator — se face din dashboard.)

### Invitatii — user
| Comanda | Ce face |
|---|---|
| `/invites [membru]` | Numarul + detalierea (reale / plecate / false / bonus) |
| `/inviter <membru>` | Cine a invitat membrul respectiv |
| `/invitedlist [membru]` | Lista celor invitati de cineva |
| `/invitecodes [membru]` | Codurile de invitatie + folosiri |
| `/findlink` | Unul dintre linkurile tale de invitatie |
| `/leaderboard [perioada] [rol]` | Clasament: **tot timpul / saptamana / luna**, optional filtrat pe rol |

### Invitatii — admin
| Comanda | Ce face |
|---|---|
| `/addinvites <membru> <numar>` | Adauga invitatii bonus |
| `/removeinvites <membru> <numar>` | Scade invitatii bonus |
| `/resetinvites [membru]` | Reseteaza tot serverul sau un singur membru |

## 🧮 Cum se numara invitatiile

Categorii (ca la Invite Tracker):
- ✅ **Reale** — au intrat prin tine si sunt inca pe server
- ❌ **Plecate** — au intrat prin tine dar au plecat
- 🚫 **False** — cont prea nou (sub 7 zile) — anti-trisare
- 🎁 **Bonus** — adaugate manual de admin

**Total = reale + bonus − plecate − false**

Leaderboardul pe **saptamana / luna** numara doar intrarile reale (non-false, care nu au plecat)
din ultimele 7 / 30 zile, folosind istoricul cu data salvat la fiecare intrare.

## ➕ Cum adaugi un modul nou

Pui un fisier in `cogs/` (ex: `cogs/giveaway.py`) cu structura:
```python
from discord.ext import commands

class NumeModul(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

async def setup(bot):
    await bot.add_cog(NumeModul(bot))
```
`main.py` il incarca automat. Hot reload din Discord: `!reload numemodul`.

## ⚠️ De stiut

- Botul are nevoie de **Manage Server** ca sa citeasca invitatiile. Fara ea, totul iese „necunoscut”.
- Detectarea invitatiei e ~99% sigura. Cazuri rare (intrari simultane, invitatie stearsa imediat
  dupa ce atinge limita) pot iesi „necunoscut”.
- Cache-ul de invitatii se reconstruieste la pornire (`on_ready`); intrarile din primele secunde
  dupa start pot iesi nedetectate.
- Datele sunt in JSON (`data/store.json`) — perfect pentru servere mici. Cand creste, se inlocuieste
  doar `utils/storage.py` cu SQLite, restul codului ramane neatins.

## 🔒 Eroare SSL la pornire? (Python 3.13+ pe retele corporate)

Daca la pornire vezi `CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier`,
cauza e un antivirus/proxy care inspecteaza HTTPS, combinat cu verificarea stricta
din Python 3.13+. Doua solutii:
  1. Foloseste **Python 3.12** (cel mai simplu).
  2. Sau ramai pe 3.14 — `main.py` din proiect contine deja un fix care relaxeaza
     DOAR verificarea stricta a extensiilor (restul validarii ramane activ).
