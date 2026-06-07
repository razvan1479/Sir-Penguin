# 🤖 Sir-Penguin — Bot de Discord modular + Dashboard

Bot de Discord cu module independente și un panou web (dashboard) de unde
configurezi totul, cu login prin Discord. Fiecare funcție e un modul separat
(un „cog"), deci poți adăuga sau modifica funcții fără să strici restul.
Hostat 24/7, cu auto-deploy din GitHub.

## ✨ Module

- **Bun venit** — mesaj de întâmpinare cu avatar, banner și cine a invitat membrul
- **Invitații** — invite tracker complet cu leaderboard pe perioade
- **Embed builder** — creezi mesaje frumoase (regulament, anunțuri) și le postezi
- **Giveaway** — concursuri cu buton, counter live, recurență, restricție pe rol
- **Notificări** — anunță când un creator postează/intră live (YouTube, Twitch, Kick, TikTok)
- **Avatar & Banner** — afișează avatarul/bannerul oricui, cu linkuri de descărcare
- **Rank-uri auto** — ranguri după vechime, cu nickname și roluri automate
- **Joc numere** — „ghicește cel mai aproape de numărul random"
- **Piatra-Foarfeca-Hartie** — meci 1v1 cu buton de rematch
- **Roluri în masă** — dă/scoate roluri la tot serverul sau pe condiții
- **Dashboard web** — configurezi tot din browser, cu login prin contul tău de Discord
- **Permisiuni** — alegi din dashboard ce roluri (mai multe) pot folosi comenzile botului
- **Mesaje DM în masă** — trimite un mesaj în DM membrilor, eșalonat (1/minut), cu preview și progres

---

## 🚀 Instalare și pornire

### 1. Pregătește botul pe Discord
1. https://discord.com/developers/applications → creează o aplicație.
2. La **Bot**, activează *Privileged Gateway Intents*: **SERVER MEMBERS INTENT** și **MESSAGE CONTENT INTENT**.
3. Copiază tokenul botului (Reset Token).
4. La invitare, dă-i permisiunile: **Manage Server** (invitații), **Manage Roles** și **Manage Nicknames** (rank-uri/roluri), plus trimitere mesaje/embed-uri.
5. La **OAuth2**, asigură-te că **„Requires OAuth2 Code Grant" e DEZACTIVAT**.

### 2. Configurează fișierul `.env`
```
DISCORD_TOKEN=tokenul_botului

# Dashboard (login cu Discord)
DISCORD_CLIENT_ID=id_aplicatie
DISCORD_CLIENT_SECRET=secret_oauth2
DISCORD_REDIRECT_URI=http://localhost:5000/callback
FLASK_SECRET=orice_text_random_lung

# Opțional — notificări Twitch (gratuit de pe dev.twitch.tv/console)
TWITCH_CLIENT_ID=
TWITCH_CLIENT_SECRET=
```
> `.env` NU se urcă pe GitHub. Pe server, valorile se pun direct acolo (manual),
> iar la `DISCORD_REDIRECT_URI` se folosește adresa publică + `/callback`,
> adăugată identic și în Developer Portal → OAuth2 → Redirects.

### 3. Instalează dependențele
```
pip install -r requirements.txt
```

### 4. Pornește
```
python run.py            # botul + dashboardul împreună
```
Sau separat: `python main.py` (botul) și `python dashboard/app.py` (dashboard → http://localhost:5000).

Comenzile slash se sincronizează **automat** la pornire și când intri pe un server
nou (chiar dacă botul era oprit când a fost adăugat). Nu trebuie nimic manual.

---

## 📁 Structura proiectului

```
discord-bot/
├── main.py              # pornește botul + încarcă automat tot din cogs/
├── run.py               # pornește bot + dashboard împreună (citește PORT din mediu)
├── .env                 # tokenul și secretele (nu se urcă pe GitHub)
├── requirements.txt
├── cogs/                # fiecare modul = un fișier independent
│   ├── welcome.py        invites.py     embeds.py      giveaway.py
│   ├── notifications.py  avatar.py      rankup.py      game.py
│   ├── rps.py            massrole.py    dashboard_sync.py
├── utils/storage.py     # depozit comun de date (bot + dashboard)
├── data/store.json      # se creează automat; aici se salvează totul
└── dashboard/
    ├── app.py           # serverul web (Flask) + login OAuth2
    └── templates/       # paginile dashboardului
```

---

## 💬 Comenzile, pe module

> 🔒 = cere permisiunea **Manage Server**. 🎭 = cere **Manage Roles**.

### 👋 Bun venit
Embed la intrarea unui membru: mesaj configurabil, avatar (thumbnail), banner și
opțional cine l-a invitat. Se configurează din dashboard.

| Comandă | Ce face |
|---|---|
| `/welcome test` 🔒 | Trimite un mesaj de bun venit de test |
| `/welcome channel <canal>` 🔒 | Setează rapid canalul de bun venit |

Placeholdere în mesaj: `{user}`, `{username}`, `{server}`, `{count}`.

### 📨 Invitații
Urmărește cine pe cine invită. Categorii: **reale** (au rămas), **plecate**,
**false** (conturi sub 7 zile), **bonus** (manuale). **Total = reale + bonus − plecate − false.**

| Comandă | Ce face |
|---|---|
| `/invites [membru]` | Numărul și detalierea invitațiilor |
| `/inviter <membru>` | Cine a invitat membrul |
| `/invitedlist [membru]` | Lista celor invitați de cineva |
| `/invitecodes [membru]` | Codurile de invitație + folosiri |
| `/findlink` | Unul dintre linkurile tale de invitație |
| `/leaderboard [perioadă] [rol]` | Clasament: tot timpul / săptămână / lună |
| `/addinvites <membru> <nr>` 🔒 | Adaugă invitații bonus |
| `/removeinvites <membru> <nr>` 🔒 | Scade invitații bonus |
| `/resetinvites [perioadă] [membru]` 🔒 | Resetează clasamentul: tot timpul / 7 zile / 30 zile, opțional doar un membru |

**🏁 Concurs de invitații** (sub-comenzi `/concurs`, sau din dashboard → „Concurs invite"):

| Comandă | Ce face |
|---|---|
| `/concurs start [nume]` 🔒 | Pornește un concurs simplu — numără invitațiile de acum încolo (fără durată fixă) |
| `/concurs clasament` 🔒 | Afișează clasamentul concursului curent (top 10) |
| `/concurs stop` 🔒 | Încheie concursul acum și anunță câștigătorul |
| `/concurs status` 🔒 | Starea concursului (programat / activ / timp rămas) |

**Concurs cu durată și anunț automat (din dashboard → „Concurs invite"):**
În dashboard poți configura un concurs complet:
- **Numele** concursului
- **Când începe** — acum sau programat peste X ore
- **Durata** — în zile + ore (ex: 7 zile); 0 = fără limită, se oprește manual
- **Canalul** unde se anunță rezultatul
- **Câți câștigători** se anunță

Botul pornește singur concursul programat la ora stabilită și, când expiră durata,
**anunță automat câștigătorii + clasamentul** în canalul ales. Vezi clasamentul
live și în dashboard pe durata concursului.

Concursul numără doar invitațiile reale (exclude conturile plecate/false) strânse
de la pornire, fără să afecteze clasamentul „tot timpul". Ideal pentru competiții
cu start și final clar (spre deosebire de clasamentul pe 7/30 zile, care e o
fereastră glisantă).

### 🧩 Embed builder
Creezi embed-uri în dashboard (titlu, text, imagine, footer, culoare), le dai un nume, le postezi.

| Comandă | Ce face |
|---|---|
| `/embed send <nume> [canal]` 🔒 | Postează un embed salvat |
| `/embed preview <nume>` 🔒 | Îl vezi doar tu, fără să-l postezi |
| `/embed list` | Lista embed-urilor salvate |
| `/embed delete <nume>` 🔒 | Șterge un embed |

### 🎁 Giveaway
Embed cu buton de înscriere, counter live, se încheie singur la timp, alege
câștigătorii automat. Afișează cine a organizat giveaway-ul („Organizat de @…",
cel care dă `/giveaway start"), cronometrul live (`se termină în…", actualizat
automat de Discord) și ora exactă de final. Configurabil din dashboard (canal,
premiu, durată, nr. câștigători, text buton, culoare, ping `@everyone`,
restricție pe rol, recurență).

| Comandă | Ce face |
|---|---|
| `/giveaway start` 🔒 | Postează acum un giveaway |
| `/giveaway end <message_id>` 🔒 | Încheie un giveaway mai devreme |
| `/giveaway reroll <message_id>` 🔒 | Alege alt câștigător |

### 🔔 Notificări
Anunță conținut nou de la creatori urmăriți. Se adaugă din dashboard. Verifică la ~5 min.

| Comandă | Ce face |
|---|---|
| `/notify list` | Creatorii urmăriți |
| `/notify test <id>` 🔒 | Trimite o notificare de test |

Platforme: YouTube (direct), Twitch (cere credențiale în `.env`), Kick, TikTok (experimental).

### 🖼️ Avatar & Banner
| Comandă | Ce face |
|---|---|
| `/avatar [user]` | Avatarul cuiva, cu linkuri PNG/JPG/WEBP (și GIF dacă e animat) |
| `/banner [user]` | Bannerul cuiva, aceleași formate |
| `/serveravatar` | Iconița (avatarul) serverului, cu linkuri de descărcare |
| `/serverbanner` | Bannerul serverului, cu linkuri de descărcare |

### ⏫ Rank-uri auto
Rang după vechimea pe server. Definești **câte trepte vrei** în dashboard, fiecare cu
pragul ei de **zile**, un **emoji** (pus la finalul nickname-ului) și un **rol**.
Membrul primește rolul + emoji-ul celei mai înalte trepte atinse și pierde rolul
treptei anterioare (promovare). Verifică la 24h și la pornire.

| Comandă | Ce face |
|---|---|
| `/rankup run` 🔒 | Aplică acum rangurile pe tot serverul (fără să aștepți 24h) |
| `/rankup status` | Arată configurația curentă |

### 🎲 Joc numere
Ghicești cel mai aproape de un număr random. Admin pornește runda (panou cu
butoane), jucătorii aleg privat, la final câștigă cel mai apropiat. Configurabil:
canal, interval de numere, durata countdown-ului.

| Comandă | Ce face |
|---|---|
| `/randome` 🔒 | Pregătește o rundă (panou Start/Stop/Reset) |
| `/alege <număr>` | Alegi privat un număr (cât timp runda e activă) |

### ✊ Piatra-Foarfeca-Hartie
Meci 1v1 cu meniu de alegere și buton de rematch. Configurabil: canalul permis (sau oriunde).

| Comandă | Ce face |
|---|---|
| `/rps <adversar>` | Pornește un meci cu cineva |

### 🎭 Roluri în masă
Dă sau scoate roluri în masă. Se poate folosi din comenzi SAU din dashboard
(care trimite o „comandă" pe care botul o execută în câteva secunde).

| Comandă | Ce face |
|---|---|
| `/massrole give_all <rol>` 🎭 | Dă rolul tuturor membrilor |
| `/massrole remove_all <rol>` 🎭 | Scoate rolul de la toți |
| `/massrole give_to <rol> <condiție>` 🎭 | Dă rolul celor care au deja un anumit rol |
| `/massrole remove_from <rol> <condiție>` 🎭 | Scoate rolul de la cei care au un anumit rol |

Reguli: nu se poate folosi `@everyone`, roluri de integrări, sau roluri mai sus
decât rolul botului. Rolul botului trebuie să fie deasupra rolurilor gestionate.

### 🔧 Owner (în chat, prefix `!`)
| Comandă | Ce face |
|---|---|
| `!reload <modul>` | Reîncarcă un cog fără restart (ex: `!reload giveaway`) |

---

## 🖥️ Dashboardul web

Te loghezi cu Discord și vezi **doar serverele tale** (unde ești admin/owner) în
care e și botul, cu nume și poze reale. Pentru fiecare server, în meniul din
stânga ai câte o pagină pentru fiecare modul configurabil: Bun venit, Invitații
(leaderboard cu nume reale), Embed builder, Giveaway, Notificări, Rank-uri auto,
Joc numere, Piatra-foarfeca și Roluri în masă.

Login-ul cere OAuth2 configurat (vezi `.env`) și adresa din `DISCORD_REDIRECT_URI`
adăugată identic în Developer Portal → OAuth2 → Redirects.

---

## ✉️ Mesaje DM în masă

Trimite un mesaj privat (DM) membrilor serverului, **eșalonat** ca să reducă riscul de spam.
Se configurează din dashboard (pagina „Mesaje DM"):
- scrii mesajul, cu **previzualizare** cum va arăta în DM
- alegi pauza între mesaje (implicit 60 sec = 1/minut, minim 10 sec) și limita pe zi
- opțional, doar către un anumit rol
- vezi progresul live (trimise / sărite / azi) și ai buton Start/Stop

Comenzi: `/dm_masa` (pornește) și `/dm_stop` (oprește). Botul sare automat boții și
pe cei cu DM închise (nu reîncearcă).

> ⚠️ **Important:** DM-urile în masă nesolicitate sunt considerate spam de Discord și
> pot duce la ban-ul botului. Eșalonarea reduce riscul, dar nu îl elimină. Folosește doar
> pentru mesaje relevante membrilor și pe propria răspundere.

## 🔑 Cine poate folosi botul (permisiuni)

În dashboard, pagina **„Permisiuni"** (sus în meniu), per server, alegi ce roluri pot
folosi comenzile de management (welcome, giveaway, roluri în masă, concurs, embed, etc.).
Poți bifa **mai multe roluri** deodată.

- **Administratorii** și **proprietarul** serverului au mereu acces, chiar dacă nu bifezi nimic.
- Oricine are **unul** dintre rolurile bifate poate folosi comenzile.
- Cine nu are acces primește un mesaj scurt când încearcă o comandă de management.
- Comenzile publice (ex: `/avatar`, `/alege`, `/rps`, `/leaderboard`) rămân pentru toți.

## ➕ Cum adaugi un modul nou

Pui un fișier în `cogs/` (ex: `cogs/moderare.py`):

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

## ⚠️ Probleme frecvente

- **Comenzile nu apar pe un server:** la invitare, botul trebuie să aibă scope-ul `applications.commands` (din OAuth2 → URL Generator). Botul sincronizează automat la intrare și la pornire.
- **Rank-uri/roluri nu se aplică:** botul are nevoie de **Manage Roles** și **Manage Nicknames**, iar rolul lui trebuie să fie **mai sus** decât rolurile/membrii gestionați. Pe owner nu se poate schimba nickname-ul (limitare Discord).
- **Eroare SSL la pornire** (rețele cu proxy + Python 3.13+): codul are deja un fix care relaxează doar verificarea strictă. Alternativ, Python 3.12.
- **Bannerul nu apare:** majoritatea userilor nu au banner (e feature de Nitro). E normal.
- **Datele** stau în `data/store.json`. La scară mare se înlocuiește doar `utils/storage.py` cu o bază de date.
- **`.env` nu se urcă niciodată pe GitHub.** Dacă tokenul se expune, resetează-l.

---

## 🏠 Hosting

Botul trebuie să ruleze non-stop. Recomandat: **VPS** sau **Oracle Cloud Free Tier**
(rulează 24/7, gratuit), cu botul pornit ca serviciu `systemd` (pornire automată +
repornire la crash). Planurile gratuite de tip „web service" pot adormi serviciul,
ceea ce face botul să apară offline — bune doar pentru testat.

Pe orice host: codul vine din GitHub (`git clone` / `git pull`), iar `.env` se pune
manual pe server. Cu auto-deploy (GitHub Actions), un `git push` actualizează
serverul singur și repornește botul.
