# Metin2 ⇆ Discord — Specificație API pentru puntea de tickete

Tu (Metin2) implementezi endpoint-urile de mai jos. Botul le apelează periodic
ca să sincronizeze ticketele cu Discord. Botul NU ține baza de date — tu ești
sursa de adevăr; botul doar oglindește pe Discord.

---

## Autentificare

Fiecare cerere de la bot trimite un header:

```
Authorization: Bearer <TOKEN>
```

`<TOKEN>` = un secret pe care îl alegi tu (ex. un șir lung random). Îl pui și în
`.env`-ul botului (`METIN2_API_TOKEN=...`). Dacă tokenul nu se potrivește,
răspunzi cu `401`.

Base URL: tu îmi dai adresa (ex. `https://jocultau.ro/api` sau `http://IP:PORT/api`).
O pun în `.env` ca `METIN2_API_BASE`.

Toate răspunsurile sunt **JSON**. Datele/orele în format ISO 8601 UTC
(ex. `2026-08-21T14:30:00Z`).

---

## 1. Tickete noi (botul citește)

Botul întreabă periodic ce tickete au fost create în joc și încă n-au fost puse
pe Discord.

```
GET /tickets/pending
```

Răspuns:
```json
{
  "tickets": [
    {
      "id": 123,
      "player_name": "Razvan",
      "player_id": "acc_4571",
      "title": "Nu pot intra pe cont",
      "description": "Îmi zice parolă greșită deși e corectă.",
      "category": "Account Issues",
      "status": "open",
      "created_at": "2026-08-21T14:30:00Z"
    }
  ]
}
```

- Întorci doar ticketele care **nu au încă `discord_channel_id`** (adică n-au fost
  încă preluate de bot). Cum știe botul că le-a preluat → vezi punctul 2.
- Dacă nu e niciunul nou: `{"tickets": []}`.

## 2. Confirmare că botul a creat ticketul pe Discord (botul scrie)

După ce botul creează canalul/mesajul pe Discord, îți trimite ID-ul lui, ca să
nu-ți mai trimită acel ticket ca „pending" din nou.

```
POST /tickets/{id}/link
```
Body:
```json
{ "discord_channel_id": "1122334455667788" }
```
Tu salvezi `discord_channel_id` la ticketul respectiv. Răspuns: `{"ok": true}`.

---

## 3. Mesaje noi de la jucători (botul citește)

Botul întreabă periodic ce mesaje noi au scris jucătorii în joc, ca să le pună
în ticketul de Discord.

```
GET /messages/pending
```
Răspuns:
```json
{
  "messages": [
    {
      "id": 9001,
      "ticket_id": 123,
      "sender": "player",
      "sender_name": "Razvan",
      "text": "Am încercat și de pe alt PC, la fel.",
      "image_url": null,
      "created_at": "2026-08-21T14:40:00Z"
    }
  ]
}
```

- `sender` e mereu `"player"` aici (mesajele de la staff le trimite botul, vezi
  punctul 4).
- `image_url` = link direct la imagine (sau `null`). Dacă jucătorul atașează
  imagini, tu le găzduiești și pui link-ul aici.
- Întorci doar mesajele **nepreluate încă** de bot.

## 4. Confirmare mesaje preluate (botul scrie)

După ce botul a pus mesajele pe Discord, îți spune care le-a preluat, ca să nu
ți le mai trimită.

```
POST /messages/ack
```
Body:
```json
{ "message_ids": [9001, 9002] }
```
Tu marchezi acele mesaje ca „trimise pe Discord". Răspuns: `{"ok": true}`.

---

## 5. Răspuns de la staff (din Discord → joc) (botul scrie)

Când staff-ul scrie în ticketul de Discord, botul îți trimite mesajul, ca să
apară în joc.

```
POST /tickets/{id}/message
```
Body:
```json
{
  "sender": "staff",
  "sender_name": "iDeaL",
  "text": "Ți-am resetat parola, încearcă acum.",
  "image_url": null,
  "created_at": "2026-08-21T14:45:00Z"
}
```
Tu salvezi mesajul în `ticket_message` cu `sender = staff` și îl arăți
jucătorului în joc. Răspuns: `{"ok": true}`.

---

## 6. Schimbare status (din Discord → joc) (botul scrie)

Când staff-ul închide/marchează ticketul pe Discord (ex. „rezolvat"), botul îți
spune.

```
POST /tickets/{id}/status
```
Body:
```json
{ "status": "resolved" }
```
Statusuri: `"open"`, `"in_progress"`, `"resolved"`. Răspuns: `{"ok": true}`.

*(Opțional, invers: dacă vrei ca și jocul să poată schimba statusul și botul
să-l reflecte pe Discord, îl întorci în `GET /tickets/pending` sau facem un
`GET /tickets/updated`. Îți zic când ajungem acolo.)*

---

## 7. Categorii (botul citește, opțional)

Ca botul să știe ce categorii există (pentru afișare/filtrare):

```
GET /categories
```
Răspuns:
```json
{ "categories": ["Account Issues", "Bug Reports", "Payment", "Other"] }
```

---

## Structura de date pe care o ții tu (recomandare)

**ticket_category**
- `id`, `name`

**ticket**
- `id`, `player_name`, `player_id`, `title`, `description`,
  `category` (sau `category_id`), `status` (open/in_progress/resolved),
  `discord_channel_id` (gol până îl preia botul), `created_at`

**ticket_message**
- `id`, `ticket_id`, `sender` (player/staff), `sender_name`, `text`,
  `image_url` (poate fi gol), `created_at`,
  `sent_to_discord` (bool — ca să știi ce a preluat botul)

---

## Rezumat flux

1. Jucător deschide ticket în joc → tu îl salvezi (fără `discord_channel_id`).
2. Botul cheamă `GET /tickets/pending` (la câteva secunde) → vede ticketul nou →
   creează canalul pe Discord → `POST /tickets/{id}/link` cu channel_id.
3. Jucător scrie în joc → tu salvezi mesaj (player, `sent_to_discord=false`).
4. Botul cheamă `GET /messages/pending` → pune mesajele pe Discord →
   `POST /messages/ack` cu id-urile.
5. Staff scrie pe Discord → botul cheamă `POST /tickets/{id}/message` (staff) →
   tu îl arăți în joc.
6. Staff închide pe Discord → botul cheamă `POST /tickets/{id}/status`.

Atât. Șase rute simple (4 pe care le citește botul, restul le scrie). Când le ai
gata (sau chiar și doar câteva), îmi zici base URL-ul și tokenul, și construiesc
partea de bot să se potrivească exact.
