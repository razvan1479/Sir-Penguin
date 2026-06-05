# 🤖 Sir-Penguin — Bot de Discord modular + Dashboard

Bot de Discord cu mai multe module independente și un panou web (dashboard) de unde
configurezi totul, cu login prin Discord. Fiecare funcție e un modul separat (un „cog"),
deci poți adăuga sau modifica funcții fără să strici restul.

## ✨ Ce știe să facă

- **Bun venit** — mesaj de întâmpinare cu avatar, banner și cine a invitat membrul
- **Invitații** — sistem complet de tip „invite tracker" cu leaderboard pe perioade
- **Embed builder** — creezi mesaje frumoase (regulament, anunțuri) și le postezi
- **Giveaway** — concursuri cu buton de înscriere, counter live, recurență, restricție pe rol
- **Notificări** — anunță când un creator postează/intră live pe YouTube, Twitch, Kick, TikTok
- **Avatar & Banner** — afișează avatarul/bannerul oricui, cu linkuri de descărcare
- **Dashboard web** — configurezi tot din browser, cu login prin contul tău de Discord

---

## 🚀 Instalare și pornire

### 1. Pregătește botul pe Discord
1. Intră pe https://discord.com/developers/applications și creează o aplicație.
2. La **Bot**, activează *Privileged Gateway Intents*: **SERVER MEMBERS INTENT** și **MESSAGE CONTENT INTENT**.
3. Copiază tokenul botului (Reset Token).
4. Când inviți botul pe server, dă-i permisiunea **Manage Server** (necesară pentru invitații)
   și permisiunea de a trimite mesaje/embed-uri.

### 2. Configurează fișierul `.env`
Creează un fișier `.env` în folderul proiectului:

```
DISCORD_TOKEN=tokenul_botului

# Pentru dashboard (login cu Discord)
DISCORD_CLIENT_ID=id_aplicatie
DISCORD_CLIENT_SECRET=secret_oauth2
DISCORD_REDIRECT_URI=http://localhost:5000/callback
FLASK_SECRET=orice_text_random_lung

# Opțional — pentru notificările Twitch (gratuit de pe dev.twitch.tv/console)
TWITCH_CLIENT_ID=
TWITCH_CLIENT_SECRET=
```

> `.env` NU se urcă pe GitHub (e în `.gitignore`). Pe un server de hosting,
> pui aceste valori în variabilele de mediu ale platformei.

### 3. Instalează dependențele
```
pip install -r requirements.txt
```

### 4. Pornește
Cel mai simplu — botul și dashboardul deodată:
```
python run.py
```
Sau separat, în două terminale:
```
python main.py            # botul
python dashboard/app.py   # dashboardul -> http://localhost:5000
```

Comenzile slash se sincronizează **automat** la pornire (și se re-sincronizează singure
doar când adaugi/modifici comenzi). Nu trebuie să rulezi nimic manual.

---

## 📁 Structura proiectului

```
discord-bot/
├── main.py              # pornește botul + încarcă automat tot din cogs/
├── run.py               # pornește bot + dashboard împreună
├── .env                 # tokenul și secretele (nu se urcă pe GitHub)
├── requirements.txt
├── cogs/                # fiecare modul = un fișier independent
│   ├── welcome.py
│   ├── invites.py
│   ├── embeds.py
│   ├── giveaway.py
│   ├── notifications.py
│   ├── avatar.py
│   └── dashboard_sync.py
├── utils/
│   └── storage.py       # depozit comun de date (bot + dashboard)
├── data/
│   └── store.json       # se creează automat; aici se salvează totul
└── dashboard/
    ├── app.py           # serverul web (Flask) + login OAuth2
    └── templates/       # paginile dashboardului
```

---

## 💬 Comenzile, pe module

> Comenzile marcate cu 🔒 cer permisiunea **Manage Server** (sau Admin).

### 👋 Bun venit
Trimite un embed când intră cineva pe server: mesaj configurabil, avatarul (thumbnail),
bannerul (imaginea mare) și opțional cine l-a invitat. Se configurează din dashboard.

| Comandă | Ce face |
|---|---|
| `/welcome test` 🔒 | Trimite un mesaj de bun venit de test |
| `/welcome channel <canal>` 🔒 | Setează rapid canalul de bun venit |

Mesajul acceptă placeholdere: `{user}` (mention), `{username}`, `{server}`, `{count}` (nr. membri).

### 📨 Invitații
Urmărește cine pe cine invită, prin compararea folosirilor invitațiilor. Distinge între
invitație **personală**, **link personalizat al serverului (vanity)** și **necunoscut**.
Invitațiile se împart pe categorii:
- **Reale** — au intrat și au rămas
- **Plecate** — au intrat dar au plecat
- **False** — conturi prea noi (sub 7 zile), anti-trișare
- **Bonus** — adăugate manual de admin

**Total = reale + bonus − plecate − false**

| Comandă | Ce face |
|---|---|
| `/invites [membru]` | Numărul și detalierea invitațiilor cuiva |
| `/inviter <membru>` | Cine a invitat membrul respectiv |
| `/invitedlist [membru]` | Lista celor invitați de cineva |
| `/invitecodes [membru]` | Codurile de invitație + folosiri |
| `/findlink` | Unul dintre linkurile tale de invitație |
| `/leaderboard [perioadă] [rol]` | Clasament: tot timpul / săptămână / lună, opțional filtrat pe rol |
| `/addinvites <membru> <număr>` 🔒 | Adaugă invitații bonus |
| `/removeinvites <membru> <număr>` 🔒 | Scade invitații bonus |
| `/resetinvites [membru]` 🔒 | Resetează tot serverul sau un singur membru |

> Necesită permisiunea **Manage Server** pe bot ca să poată citi invitațiile.

### 🧩 Embed builder
Creezi embed-uri custom în dashboard (titlu, text cu markdown, imagine/gif, footer, culoare),
le dai un nume, apoi le postezi. Util pentru regulament, anunțuri, info.

| Comandă | Ce face |
|---|---|
| `/embed send <nume> [canal]` 🔒 | Postează un embed salvat pe server |
| `/embed preview <nume>` 🔒 | Îl vezi doar tu, fără să-l postezi |
| `/embed list` | Lista embed-urilor salvate |
| `/embed delete <nume>` 🔒 | Șterge un embed |

Imaginile se pun prin link (urci poza pe Discord/imgur și copiezi linkul direct).

### 🎁 Giveaway
Postezi un embed cu buton; cine apasă intră în tragere. Are counter live de participanți,
se încheie singur exact la timp, alege câștigătorii automat. Configurabil din dashboard:
canal, premiu, durată, nr. câștigători, text buton, culoare, ping `@everyone`,
restricție pe un anumit rol, și postare automată recurentă la interval.

| Comandă | Ce face |
|---|---|
| `/giveaway start` 🔒 | Postează acum un giveaway (folosește config din dashboard) |
| `/giveaway end <message_id>` 🔒 | Încheie un giveaway mai devreme |
| `/giveaway reroll <message_id>` 🔒 | Alege alt câștigător pentru unul încheiat |

> ID-ul mesajului: click dreapta pe mesaj → Copiază ID mesaj (cu Mod Developer pornit).

### 🔔 Notificări
Urmărește creatori și anunță când apare conținut nou. Se adaugă din dashboard
(platformă, URL canal, canal Discord, mesaj, rol de ping). Verifică la ~5 minute.

| Comandă | Ce face |
|---|---|
| `/notify list` | Creatorii urmăriți pe acest server |
| `/notify test <id>` 🔒 | Trimite o notificare de test |

Platforme: **YouTube** (merge direct), **Twitch** (necesită credențiale în `.env`),
**Kick** (API neoficial), **TikTok** (experimental — TikTok nu are API public).

### 🖼️ Avatar & Banner
| Comandă | Ce face |
|---|---|
| `/avatar [user]` | Avatarul cuiva, cu linkuri de descărcare (PNG/JPG/WEBP, GIF dacă e animat) |
| `/banner [user]` | Bannerul cuiva, cu aceleași formate |

### 🔧 Comenzi de owner (în chat, cu prefix `!`)
| Comandă | Ce face |
|---|---|
| `!reload <modul>` | Reîncarcă un cog fără să repornești botul (ex: `!reload giveaway`) |

---

## 🖥️ Dashboardul web

Pornește pe http://localhost:5000. Te loghezi cu **contul tău de Discord** și vezi
**doar serverele tale** (unde ești admin/owner) în care e și botul, cu nume și poze reale.

Pagini disponibile pentru fiecare server (meniu în stânga):
- **Bun venit** — mesajul de întâmpinare, cu previzualizare live
- **Invitații** — leaderboard pe tot timpul / săptămână / lună, cu nume reale
- **Embed builder** — creezi și editezi embed-uri, cu previzualizare
- **Giveaway** — configurezi giveaway-urile
- **Notificări** — adaugi creatori de urmărit

Pentru login trebuie configurat OAuth2 (vezi `.env`) și, în Developer Portal →
OAuth2 → Redirects, trebuie adăugată exact adresa din `DISCORD_REDIRECT_URI`.

---

## ➕ Cum adaugi un modul nou

Pui un fișier în `cogs/` (ex: `cogs/moderare.py`) cu structura:

```python
from discord.ext import commands
from discord import app_commands
import discord

class Moderare(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="kick", description="...")
    async def kick(self, interaction: discord.Interaction):
        ...

async def setup(bot):
    await bot.add_cog(Moderare(bot))
```

`main.py` îl încarcă automat. Comenzile noi se sincronizează singure la pornire.

---

## ⚠️ Lucruri de știut / probleme frecvente

- **Eroare SSL la pornire** (`CERTIFICATE_VERIFY_FAILED`) pe rețele cu antivirus/proxy +
  Python 3.13+: codul conține deja un fix care relaxează doar verificarea strictă. Alternativ,
  folosește Python 3.12.
- **Comenzile slash nu apar imediat:** prima sincronizare poate dura puțin. Dă **Ctrl+R**
  în Discord dacă nu le vezi.
- **Bannerul nu apare:** majoritatea userilor nu au banner (e feature de Nitro). E normal.
- **Datele** se țin în `data/store.json` (JSON). Perfect pentru servere mici. La scară mare,
  se înlocuiește doar `utils/storage.py` cu o bază de date, restul codului rămâne la fel.
- **`.env` nu se urcă niciodată pe GitHub.** Tokenul e secret — dacă se expune, resetează-l.

---

## 🏠 Hosting

Botul trebuie să ruleze non-stop. Opțiuni:
- **VPS** (ex. Hetzner) sau **Oracle Cloud Free Tier** — rulează 24/7, fără întreruperi.
  Recomandat pentru producție (botul pornit ca serviciu `systemd`).
- **Render / Railway gratuit** — bun pentru testat, dar planurile gratuite pot „adormi"
  serviciul, ceea ce face botul să apară offline.

Pe orice host: codul vine din GitHub (`git clone`), iar `.env` se pune manual pe server
(secretele nu sunt în repo).
