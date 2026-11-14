"""
utils/contest_i18n.py — texte RO / EN pentru concursul de invitatii.

Limba se alege din dashboard (pagina Concurs) si se salveaza PER SERVER in
storage, sub cheia "contest_lang". Aceeasi limba e folosita pentru:
  - pagina de concurs din dashboard
  - mesajele botului pe Discord (anunt start, clasament live, rezultat final,
    raspunsurile comenzilor /concurs ...)

Pentru a adauga o limba noua: adauga un dict nou in STRINGS cu ACELEASI chei.
"""

from utils import storage

DEFAULT_LANG = "ro"
LANGS = {"ro": "Română", "en": "English"}


def get_lang(guild_id) -> str:
    lang = storage.get(guild_id, "contest_lang", DEFAULT_LANG)
    return lang if lang in LANGS else DEFAULT_LANG


def set_lang(guild_id, lang: str) -> None:
    if lang in LANGS:
        storage.set(guild_id, "contest_lang", lang)


def strings(lang: str) -> dict:
    return STRINGS.get(lang, STRINGS[DEFAULT_LANG])


def t(guild_id_or_lang, key: str, **kw) -> str:
    """t(guild_id, 'key', name=...) sau t('en', 'key'). Cade pe RO daca lipseste cheia."""
    lang = guild_id_or_lang if guild_id_or_lang in LANGS else get_lang(guild_id_or_lang)
    s = STRINGS.get(lang, {}).get(key)
    if s is None:
        s = STRINGS[DEFAULT_LANG].get(key, key)
    return s.format(**kw) if kw else s


STRINGS = {
    "ro": {
        # ---------------- dashboard ----------------
        "page_title": "Concurs invite",
        "lang_label": "Limba concursului (dashboard + Discord)",
        "lang_save": "Salvează limba",
        "scheduled_badge": "🕒 Programat: {name}",
        "starts_in": "Începe în {h} ore",
        "active_badge": "🏁 Activ: {name}",
        "remaining": "Mai sunt {h} ore",
        "no_fixed_duration": "Fără durată fixă — se oprește manual.",
        "stop_btn": "⏹️ Oprește acum",
        "stop_hint": "La final, botul anunță automat câștigătorii și clasamentul în canalul setat. Clasamentul se actualizează automat.",
        "live_enable": "📊 Postează clasamentul live pe un canal",
        "live_channel": "ID canal clasament live",
        "live_channel_ph": "unde apare clasamentul",
        "live_interval": "La câte minute se actualizează",
        "board_count": "Câți apar în clasament (max 25)",
        "live_hint_running": "Se actualizează singur (editează același mesaj). Poți schimba asta chiar și în timpul concursului.",
        "live_save": "💾 Salvează clasamentul live",
        "excl_title": "🚫 Exclude persoane din clasament",
        "excl_hint": "Cine e exclus nu apare în clasament (ex. tu / owner-ul). Invitațiile lor reale rămân neatinse în restul botului.",
        "excl_remove": "✕ scoate din listă",
        "excl_none": "Nimeni exclus deocamdată.",
        "excl_id_label": "ID-ul persoanei de exclus",
        "excl_btn": "➕ Exclude",
        "excl_howto": "Click dreapta pe persoană în Discord → Copiază ID utilizator. (Ai nevoie de „Mod dezvoltator” pornit în Discord.)",
        "no_contest": "Niciun concurs activ",
        "name_label": "Numele concursului",
        "name_ph": "ex: Concurs August",
        "default_name": "Concurs invitatii",
        "start_delay": "Începe peste (ore) — 0 = acum",
        "dur_days": "Durată — zile",
        "dur_hours": "+ ore",
        "dur_hint": "Durată 0 = fără limită (se oprește manual). Ex: 7 zile = concurs de o săptămână.",
        "announce_channel": "ID canal pentru anunț final",
        "announce_channel_ph": "unde se anunță câștigătorii",
        "winners_count": "Câți câștigători se anunță",
        "live_hint_new": "Botul postează un clasament care se actualizează singur (editează același mesaj, nu spamează). Ex: 30 = din jumătate în jumătate de oră.",
        "start_btn": "🏁 Pornește concursul",
        "counts_hint": "Concursul numără doar invitațiile reale (fără conturi plecate/false) de la pornire încoace, fără să afecteze clasamentul „tot timpul”.",
        "ended_winner": "🏆 Încheiat. Câștigător: <b>{name}</b> cu {n} invitații.",
        "nobody_yet": "Încă nu a invitat nimeni.",
        # ---------------- editare concurs in desfasurare ----------------
        "edit_title": "✏️ Editează concursul",
        "lbl_start": "Început",
        "lbl_start_sched": "Începe",
        "lbl_end": "Se termină",
        "lbl_announce": "Canal anunț final",
        "lbl_winners": "Câștigători",
        "end_none": "fără limită (manual)",
        "not_set": "nesetat",
        "adjust_end": "Modifică sfârșitul (ore, + / −)",
        "adjust_start": "Mută startul (ore, + / −)",
        "no_limit": "Fără limită de timp (se oprește manual)",
        "edit_hint": "Modificările se aplică imediat, iar numărătoarea invitațiilor NU se resetează. Ex: +24 prelungește cu o zi, −2 scurtează cu 2 ore.",
        "edit_save": "💾 Salvează modificările",
        "flash_saved": "✅ Modificările au fost salvate.",
        "flash_end_past": "⚠️ Sfârșitul ar ajunge în trecut — ora de final nu a fost schimbată. Folosește „Oprește acum” dacă vrei să închei concursul.",
        # ---------------- Discord ----------------
        "dc_started_announce": "🏁 Concursul **{name}** a început! Invitați cât mai mulți membri!",
        "dc_final_title": "🏆 {name} · REZULTAT FINAL",
        "dc_winners": "🎉 Câștigător(i): {winners}",
        "dc_nobody": "nimeni",
        "dc_no_invites": "Nicio invitație.",
        "dc_live_title": "🏁 {name} · Clasament live",
        "dc_live_empty": "Încă nicio invitație. Fii primul!",
        "dc_live_footer": "Se actualizează automat",
        "dc_board_title": "🏁 {name} · Clasament",
        "dc_already_running": "Există deja un concurs activ/programat. Oprește-l cu `/concurs stop`.",
        "dc_started_cmd": "🏁 **{name}** a început! Vezi clasamentul cu `/concurs clasament`.\n💡 Pentru concurs cu durată fixă și anunț automat, folosește dashboardul.",
        "dc_no_active_board": "Nu e niciun concurs activ. Pornește cu `/concurs start` sau din dashboard.",
        "dc_nobody_invited": "🏁 **{name}** — încă nu a invitat nimeni.",
        "dc_no_active": "Nu e niciun concurs activ.",
        "dc_ended_nobody": "🏁 **{name}** s-a încheiat, dar nu a invitat nimeni. 📭",
        "dc_status_scheduled": "🕒 **{name}** e programat să înceapă peste **{mins} minute**.",
        "dc_status_running_left": "🏁 **{name}** e activ. Mai sunt **{hours} ore**.",
        "dc_status_running": "🏁 **{name}** e activ (fără durată fixă).",
        "dc_status_none": "Niciun concurs activ.",
    },
    "en": {
        # ---------------- dashboard ----------------
        "page_title": "Invite contest",
        "lang_label": "Contest language (dashboard + Discord)",
        "lang_save": "Save language",
        "scheduled_badge": "🕒 Scheduled: {name}",
        "starts_in": "Starts in {h} hours",
        "active_badge": "🏁 Active: {name}",
        "remaining": "{h} hours left",
        "no_fixed_duration": "No fixed duration — stops manually.",
        "stop_btn": "⏹️ Stop now",
        "stop_hint": "When it ends, the bot automatically announces the winners and the leaderboard in the configured channel. The leaderboard updates automatically.",
        "live_enable": "📊 Post the live leaderboard in a channel",
        "live_channel": "Live leaderboard channel ID",
        "live_channel_ph": "where the leaderboard appears",
        "live_interval": "Update every (minutes)",
        "board_count": "How many show on the leaderboard (max 25)",
        "live_hint_running": "It updates itself (edits the same message). You can change this even while the contest is running.",
        "live_save": "💾 Save live leaderboard",
        "excl_title": "🚫 Exclude people from the leaderboard",
        "excl_hint": "Excluded people don't appear on the leaderboard (e.g. you / the owner). Their real invites stay untouched in the rest of the bot.",
        "excl_remove": "✕ remove from list",
        "excl_none": "Nobody excluded yet.",
        "excl_id_label": "ID of the person to exclude",
        "excl_btn": "➕ Exclude",
        "excl_howto": "Right-click the person in Discord → Copy User ID. (You need “Developer Mode” enabled in Discord.)",
        "no_contest": "No active contest",
        "name_label": "Contest name",
        "name_ph": "e.g. August Contest",
        "default_name": "Invite contest",
        "start_delay": "Starts in (hours) — 0 = now",
        "dur_days": "Duration — days",
        "dur_hours": "+ hours",
        "dur_hint": "Duration 0 = no limit (stops manually). E.g. 7 days = a one-week contest.",
        "announce_channel": "Final announcement channel ID",
        "announce_channel_ph": "where the winners are announced",
        "winners_count": "How many winners to announce",
        "live_hint_new": "The bot posts a leaderboard that updates itself (edits the same message, no spam). E.g. 30 = every half hour.",
        "start_btn": "🏁 Start contest",
        "counts_hint": "The contest only counts real invites (no left/fake accounts) from the start onward, without affecting the “all time” leaderboard.",
        "ended_winner": "🏆 Ended. Winner: <b>{name}</b> with {n} invites.",
        "nobody_yet": "Nobody has invited anyone yet.",
        # ---------------- editare concurs in desfasurare ----------------
        "edit_title": "✏️ Edit the contest",
        "lbl_start": "Started",
        "lbl_start_sched": "Starts",
        "lbl_end": "Ends",
        "lbl_announce": "Final announcement channel",
        "lbl_winners": "Winners",
        "end_none": "no limit (manual)",
        "not_set": "not set",
        "adjust_end": "Change the end (hours, + / −)",
        "adjust_start": "Move the start (hours, + / −)",
        "no_limit": "No time limit (stop manually)",
        "edit_hint": "Changes apply immediately and the invite count is NOT reset. E.g. +24 extends by a day, −2 shortens by 2 hours.",
        "edit_save": "💾 Save changes",
        "flash_saved": "✅ Changes saved.",
        "flash_end_past": "⚠️ The end would land in the past — the end time was not changed. Use “Stop now” if you want to end the contest.",
        # ---------------- Discord ----------------
        "dc_started_announce": "🏁 The contest **{name}** has started! Invite as many members as you can!",
        "dc_final_title": "🏆 {name} · FINAL RESULT",
        "dc_winners": "🎉 Winner(s): {winners}",
        "dc_nobody": "nobody",
        "dc_no_invites": "No invites.",
        "dc_live_title": "🏁 {name} · Live leaderboard",
        "dc_live_empty": "No invites yet. Be the first!",
        "dc_live_footer": "Updates automatically",
        "dc_board_title": "🏁 {name} · Leaderboard",
        "dc_already_running": "There is already an active/scheduled contest. Stop it with `/concurs stop`.",
        "dc_started_cmd": "🏁 **{name}** has started! See the leaderboard with `/concurs clasament`.\n💡 For a fixed-duration contest with an automatic announcement, use the dashboard.",
        "dc_no_active_board": "There is no active contest. Start one with `/concurs start` or from the dashboard.",
        "dc_nobody_invited": "🏁 **{name}** — nobody has invited anyone yet.",
        "dc_no_active": "There is no active contest.",
        "dc_ended_nobody": "🏁 **{name}** has ended, but nobody invited anyone. 📭",
        "dc_status_scheduled": "🕒 **{name}** is scheduled to start in **{mins} minutes**.",
        "dc_status_running_left": "🏁 **{name}** is active. **{hours} hours** left.",
        "dc_status_running": "🏁 **{name}** is active (no fixed duration).",
        "dc_status_none": "No active contest.",
    },
}
