# Server de licențe · Discord Community Manager

Rulează pe Oracle, separat de Sir Penguin. Face trei lucruri:

1. **Login cu Discord pentru aplicație.** Schimbul cod → token se face aici, deci `client_secret` nu ajunge niciodată pe PC-ul utilizatorilor.
2. **Licențe cu durată personalizată.** Tu creezi chei, timpul curge de la activare, cheia se leagă de contul Discord.
3. **Licențe semnate.** Aplicația le verifică și offline, cu cheia publică.

## Structură

```
license-server/
├── app.py                  # serverul (API + pagina de admin)
├── manage.py               # comenzi din terminal (cheia publică, creare chei, listă)
├── templates/              # paginile de admin
├── requirements.txt
├── .env.example            # copiezi ca .env
├── deploy/
│   ├── license-server.service   # serviciu systemd
│   └── Caddyfile                # HTTPS automat
└── data/                   # se creează singur: baza de date + cheia de semnare
```

## ⚠️ Cel mai important fișier: `data/signing_key.pem`

Se generează singur la prima pornire. **Fă-i backup** (pe PC-ul tău, nu pe GitHub).
Dacă îl pierzi, toate licențele date devin invalide și trebuie refăcute.
Dacă cineva ți-l fură, poate genera licențe false.

## Configurare în Discord Developer Portal

Aplicația Discord Community Manager → **OAuth2** → **Redirects**, adaugi:
- `http://127.0.0.1:53134/callback` (aplicația desktop)

„Public Client” rămâne **dezactivat** (secretul stă pe server).

## Legătura cu dashboard-ul Sir Penguin

Licențele le administrezi din dashboard-ul Sir Penguin, pagina **🎫 Licențe** (vizibilă doar pentru owner).
Dashboard-ul vorbește cu serverul ăsta intern, pe `127.0.0.1:8100`, prin API-ul `/internal/*`,
protejat cu un token secret:

- în `.env`-ul serverului de licențe: `INTERNAL_ADMIN_TOKEN=...`
- în `.env`-ul botului Sir Penguin: `LICENSE_ADMIN_TOKEN=...` (**același text**)

Din internet, `/internal/*` e blocat oricum de Caddy.

Dacă serverul de licențe e oprit, botul și dashboard-ul merg normal; doar pagina Licențe afișează un mesaj.

Pagina de admin proprie (`/admin`) există ca rezervă, dar e **oprită implicit** (`WEB_ADMIN_ENABLED=false`).

## Pornire pe Oracle (pe scurt, te ghidez pas cu pas separat)

```bash
cd ~/license-server
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env          # completezi
venv/bin/python manage.py public-key        # o notezi, o punem în aplicație
sudo cp deploy/license-server.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now license-server
curl http://127.0.0.1:8100/health           # trebuie să răspundă {"ok":true}
```

Pentru HTTPS: Caddy + `deploy/Caddyfile` (cu un nume gratuit de tip `licente.<ip-cu-liniuțe>.sslip.io`).

## Reguli pentru licențe

- Timpul unei chei curge de la **activare**.
- Dacă cineva activează o a doua cheie, timpul **se adaugă** peste cel rămas.
- O cheie se poate folosi o singură dată.
- Durate: ore / zile / luni (30 de zile) / permanent.

## Din terminal

```bash
venv/bin/python manage.py public-key
venv/bin/python manage.py create 30 zile 5 "pentru grupul X"
venv/bin/python manage.py list
```

## API (pentru aplicația desktop, Etapa 3)

Toate răspunsurile sunt JSON. La eroare: `{"error": "..."}` cu cod HTTP potrivit.

| Rută | Trimite | Primește |
|---|---|---|
| `POST /api/auth/exchange` | `{code, redirect_uri}` | `{ticket, user:{id, name, username, avatar}}` |
| `POST /api/license/status` | `{ticket}` | stare licență (vezi mai jos) |
| `POST /api/license/activate` | `{ticket, key}` | stare licență; 404 cheie invalidă, 409 deja folosită, 403 revocat |
| `POST /api/license/refresh` | `{license}` | stare licență actualizată, fără login |

Stare licență: `{"status": "no_license" | "expired" | "revoked" | "active", "expires_at": <unix>, "license": "<token>"}`
(`license` apare doar la `active`).

`ticket` e valabil 15 minute după login, cât să activezi cheia.

**Formatul licenței:** `<payload base64url>.<semnătură base64url>`, semnătură Ed25519 peste textul payload-ului.
Payload: `{"v":1, "sub":"<discord id>", "name":"...", "iat":<unix>, "exp":<unix>}`.
Aplicația verifică semnătura cu cheia publică (`manage.py public-key`), apoi `exp`.

## Limitări, pe față

- Verificarea offline se bazează pe ceasul PC-ului utilizatorului. Aplicația va ține minte ultima oră văzută
  și va refuza dacă ceasul „merge înapoi”, dar nu e protecție perfectă.
- O revocare ajunge la utilizator doar la următorul `refresh` online.
- Orice aplicație desktop poate fi modificată de cineva priceput ca să sară peste verificare. Asta reduce mult
  riscul, dar nu-l elimină complet.
