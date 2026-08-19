"""
Бот для отслеживания открытия ПВЗ (9:00 / 10:00 / 11:00).

Логика:
- В группе сотрудники присылают фото с подписью/сообщением, где есть номер ПВЗ (например "ТАШ-83").
  Бот НИЧЕГО не пишет в чат — просто тихо ставит реакцию ✅ на фото и запоминает, что ПВЗ открылся
  (с фактическим временем; если раньше/позже положенного — помечается в базе как "досрочно"/
  "с опозданием").
- Фото НЕ засчитываются с 19:00 до 05:00 (ночное время) — в этот промежуток бот их игнорирует.
- За 20 и за 10 минут до времени открытия каждого ПВЗ (если он ещё не открылся) бот шлёт в группу
  отдельное сообщение с тегом ответственного: "отправьте отчёт — через N мин открытие ТАШ-XX!".
- Каждое утро в MORNING_MESSAGE_TIME бот один раз шлёт в группу приветственное сообщение —
  случайно выбранное из базы в отдельном файле morning_messages.py (не повторяя вчерашнее).
- Как только время открытия группы (9:00, 10:00 или 11:00) проходит — бот один раз шлёт итог по
  этой группе: если все открылись — короткое поздравление, если кто-то не успел — список с тегами
  тех, кто не открылся. Больше никакого постоянно обновляющегося статуса и никаких повторных
  напоминаний до этого момента.
- После STOP_TIME (11:30) бот больше не засчитывает открытия по фото. В этот момент, если для
  какой-то группы итог ещё не был отправлен (например бот был выключен) — отправляется финальный
  итог по всем группам.
- Команда /status и фраза "дай свод всех пвз" (текстом, без слэша) — только для ADMIN_ID.
  Оба ответа приходят с кнопкой "📊 Открыть свод" — открывает мини-приложение (index.html на
  GitHub Pages) с красивой таблицей опозданий. Кнопка появляется только после того, как в
  WEBAPP_URL вписана реальная ссылка на опубликованную страницу.
- report.json для мини-приложения обновляется автоматически (при каждом открытии и раз в минуту).
  Не забывай пушить его в GitHub вместе с остальным репозиторием, иначе страница будет показывать
  устаревшие данные.
- Все события хранятся в SQLite (pvz_bot.db) — переживает перезапуск бота.

ТЕСТОВЫЙ РЕЖИМ (TEST_MODE = True), команды тоже только для ADMIN_ID:
- /settime HH:MM — мгновенно "переносит" бота на нужное время.
- /testopen НОМЕР_ПВЗ — отмечает ПВЗ открытым прямо сейчас (без фото).
- /resettest — стирает из базы все записи за сегодняшний день.
Не забудь выключить TEST_MODE = False перед боевым запуском!

Требования: pip install aiogram==3.* tzdata
Файл morning_messages.py должен лежать в той же папке, что и этот скрипт.
"""

import asyncio
import logging
import random
import re
import sqlite3
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReactionTypeEmoji, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.enums import ParseMode

from morning_messages import MORNING_MESSAGES

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = "8679055019:AAFUMZve7dAGyXtL67MmCrFFVANrnXnpasQ"
GROUP_CHAT_IDS = [-1002512904846, -1004366880638]  # обе группы, где бот принимает фото и шлёт сообщения

# Твой telegram user id — команды /status и /allreport сработают только для него.
# Узнать свой id можно у бота @userinfobot.
ADMIN_ID = 1419261914

TIMEZONE = ZoneInfo("Asia/Tashkent")

# После этого времени бот больше не засчитывает открытия по фото
STOP_TIME = dtime(11, 30)

# За сколько минут до открытия тегать ответственного с просьбой отправить отчёт
REMINDER_OFFSETS = [20, 10]

# Фото НЕ засчитываются в этот промежуток (ночь)
ACCEPT_START = dtime(5, 0)
ACCEPT_END = dtime(19, 0)

# Во сколько слать утреннее приветствие (один раз в день)
MORNING_MESSAGE_TIME = dtime(8, 0)
# Сами тексты приветствий лежат в отдельном файле morning_messages.py — редактируй список там,
# не трогая этот файл.

# Файл базы данных (создастся сам рядом со скриптом при первом запуске)
DB_PATH = "pvz_bot.db"

# Путь к report.json для мини-приложения (папка с index.html, который выгружен на GitHub Pages).
# Держи report.json в ТОЙ ЖЕ папке репозитория, что и index.html — страница читает его рядом с собой.
WEBAPP_REPORT_PATH = "report.json"

# --- АВТОПУШ report.json В GIT (чтобы сайт на GitHub Pages всегда показывал свежие данные) ---
# Папка локального git-репозитория (клонированного с GitHub), где лежит report.json и index.html.
# "." значит "та же папка, где лежит этот скрипт" — так и должно быть, если ты положил
# pvz_open_bot.py прямо внутрь склонированного репозитория.
GIT_REPO_DIR = "."
GIT_AUTO_PUSH = True          # False — если хочешь пушить report.json вручную самостоятельно
GIT_PUSH_MIN_INTERVAL_MIN = 3  # не чаще одного пуша в столько минут, даже если данные меняются чаще

# Ссылка на мини-приложение (index.html), опубликованное на GitHub Pages. Обязательно HTTPS!
# Впиши сюда свою реальную ссылку, например: https://твой-юзернейм.github.io/репозиторий/index.html
WEBAPP_URL = "https://skywhy-sourse.github.io/open/index.html"

# --- ТЕСТОВЫЙ РЕЖИМ ---
TEST_MODE = False  # поставь True, чтобы включить команды /settime, /testopen, /resettest

# Список ПВЗ: номер -> время открытия + telegram username точки
PVZ_LIST = {
    "ТАШ-28": {"open_time": dtime(9, 0), "username": "@tkaduzum"},
    "ТАШ-62": {"open_time": dtime(9, 0), "username": "@tash62vodnik"},
    "ТАШ-83": {"open_time": dtime(9, 0), "username": "@uzum_vodnik_tash83"},
    "ТАШ-115": {"open_time": dtime(9, 0), "username": "@Bektimir115"},

    "ТАШ-108": {"open_time": dtime(11, 0), "username": "@Tash108"},
    "ТАШ-145": {"open_time": dtime(11, 0), "username": "@uzumkuyluk6"},

    "ТАШ-33": {"open_time": dtime(10, 0), "username": "@KuylukkonechkaTash33"},
    "ТАШ-68": {"open_time": dtime(10, 0), "username": "@kuylukbarakamarket"},
    "ТАШ-75": {"open_time": dtime(10, 0), "username": "@uzumtash75"},
    "ТАШ-84": {"open_time": dtime(10, 0), "username": "@hosiyatuzum"},
    "ТАШ-131": {"open_time": dtime(10, 0), "username": "@uzummerhaba"},
    "ТАШ-130": {"open_time": dtime(10, 0), "username": "@Uzumtash130"},
    "FrТАШ-163": {"open_time": dtime(10, 0), "username": "@Frtash163"},
    "FrТАШ-178": {"open_time": dtime(10, 0), "username": "@FrTash178"},
    "FrТАШ-180": {"open_time": dtime(10, 0), "username": "@frtash180"},
    "FrТАШ-193": {"open_time": dtime(10, 0), "username": "@frtash193"},
    "FrТАШ-192": {"open_time": dtime(10, 0), "username": "@FrTash192"},
    "FrТАШ-207": {"open_time": dtime(10, 0), "username": "@FrTash207"},
    "FrТАШ-211": {"open_time": dtime(10, 0), "username": "@FrTash211"},
    "FrТАШ-224": {"open_time": dtime(10, 0), "username": "@FrTash224"},
    "FrТАШ-272": {"open_time": dtime(10, 0), "username": "@FrTash_272"},
    "FrТАШ-288": {"open_time": dtime(10, 0), "username": "@fruzumtash288"},
    "FrТАШ-276": {"open_time": dtime(10, 0), "username": "@FrTash276"},
    "FrТАШ-326": {"open_time": dtime(10, 0), "username": "@FRTASH326"},
    "FrТАШ-340": {"open_time": dtime(10, 0), "username": "@FrTash_340"},
    "FrТАШ-341": {"open_time": dtime(10, 0), "username": "@Frtash341"},
    "FrТАШ-347": {"open_time": dtime(10, 0), "username": "@Frtash347"},
    "FrТАШ-357": {"open_time": dtime(10, 0), "username": "@Frtash357"},
    "FrТАШ-363": {"open_time": dtime(10, 0), "username": "@FRTASH363"},
    "FrТАШ-366": {"open_time": dtime(10, 0), "username": "@frtash366"},
    "FrТАШ-348": {"open_time": dtime(10, 0), "username": "@frtash348"},
    "FrТАШ-394": {"open_time": dtime(10, 0), "username": "@Frtash394"},
}

# ================================================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pvz-open-bot")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

PVZ_PATTERN = re.compile(r"[А-Яа-яA-Za-z]+-\d+")

# ---------------- БАЗА ДАННЫХ ----------------

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS openings (
            date TEXT NOT NULL,
            pvz TEXT NOT NULL,
            opened_time TEXT,
            status TEXT NOT NULL,
            PRIMARY KEY (date, pvz)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_flags (
            date TEXT NOT NULL,
            flag_key TEXT NOT NULL,
            PRIMARY KEY (date, flag_key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS morning_used (
            date TEXT PRIMARY KEY,
            message_index INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders_sent (
            date TEXT NOT NULL,
            pvz TEXT NOT NULL,
            offset_min INTEGER NOT NULL,
            PRIMARY KEY (date, pvz, offset_min)
        )
    """)
    conn.commit()
    return conn


def db_get_morning_index(date_str: str) -> int | None:
    conn = db_connect()
    row = conn.execute("SELECT message_index FROM morning_used WHERE date = ?", (date_str,)).fetchone()
    conn.close()
    return row[0] if row else None


def db_save_morning_index(date_str: str, index: int) -> None:
    conn = db_connect()
    conn.execute("INSERT OR REPLACE INTO morning_used (date, message_index) VALUES (?, ?)", (date_str, index))
    conn.commit()
    conn.close()


def db_save_reminder_sent(date_str: str, pvz: str, offset: int) -> None:
    conn = db_connect()
    conn.execute(
        "INSERT OR IGNORE INTO reminders_sent (date, pvz, offset_min) VALUES (?, ?, ?)",
        (date_str, pvz, offset),
    )
    conn.commit()
    conn.close()


def db_load_reminders_sent(date_str: str) -> dict[str, set[int]]:
    conn = db_connect()
    rows = conn.execute(
        "SELECT pvz, offset_min FROM reminders_sent WHERE date = ?", (date_str,)
    ).fetchall()
    conn.close()
    result: dict[str, set[int]] = {}
    for pvz, offset in rows:
        result.setdefault(pvz, set()).add(offset)
    return result


def pick_morning_message() -> str:
    """Выбирает приветствие для сегодня: если уже выбрано (например, бот перезапускался) —
    возвращает то же самое; иначе выбирает случайное, не повторяя вчерашнее."""
    date_str = current_day.isoformat()
    idx = db_get_morning_index(date_str)
    if idx is not None and 0 <= idx < len(MORNING_MESSAGES):
        return MORNING_MESSAGES[idx]

    prev_date = (current_day - timedelta(days=1)).isoformat()
    prev_idx = db_get_morning_index(prev_date)

    choices = list(range(len(MORNING_MESSAGES)))
    if prev_idx is not None and len(choices) > 1 and prev_idx in choices:
        choices.remove(prev_idx)

    idx = random.choice(choices)
    db_save_morning_index(date_str, idx)
    return MORNING_MESSAGES[idx]


def format_morning_message(text: str) -> str:
    """Оборачивает всё приветствие в жирный текст — это максимум акцента, который поддерживает
    Telegram (реального увеличения размера шрифта в сообщениях ботов нет)."""
    return f"<b>{text}</b>"


def db_save_opening(date_str: str, pvz: str, opened_time: dtime | None, status: str) -> None:
    conn = db_connect()
    conn.execute(
        "INSERT OR REPLACE INTO openings (date, pvz, opened_time, status) VALUES (?, ?, ?, ?)",
        (date_str, pvz, opened_time.strftime("%H:%M") if opened_time else None, status),
    )
    conn.commit()
    conn.close()


def db_load_openings(date_str: str) -> dict[str, dtime]:
    conn = db_connect()
    rows = conn.execute(
        "SELECT pvz, opened_time FROM openings WHERE date = ? AND opened_time IS NOT NULL",
        (date_str,),
    ).fetchall()
    conn.close()
    return {pvz: datetime.strptime(t, "%H:%M").time() for pvz, t in rows}


def db_load_not_opened(date_str: str) -> set[str]:
    conn = db_connect()
    rows = conn.execute(
        "SELECT pvz FROM openings WHERE date = ? AND status = 'not_opened'", (date_str,)
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def db_full_report_rows(date_str: str) -> dict[str, str]:
    conn = db_connect()
    rows = conn.execute("SELECT pvz, status FROM openings WHERE date = ?", (date_str,)).fetchall()
    conn.close()
    return {pvz: status for pvz, status in rows}


def db_set_flag(date_str: str, flag_key: str) -> None:
    conn = db_connect()
    conn.execute("INSERT OR IGNORE INTO daily_flags (date, flag_key) VALUES (?, ?)", (date_str, flag_key))
    conn.commit()
    conn.close()


def db_load_flags(date_str: str) -> set[str]:
    conn = db_connect()
    rows = conn.execute("SELECT flag_key FROM daily_flags WHERE date = ?", (date_str,)).fetchall()
    conn.close()
    return {r[0] for r in rows}


def db_clear_today(date_str: str) -> None:
    conn = db_connect()
    conn.execute("DELETE FROM openings WHERE date = ?", (date_str,))
    conn.execute("DELETE FROM daily_flags WHERE date = ?", (date_str,))
    conn.commit()
    conn.close()


# ---------------- СОСТОЯНИЕ В ПАМЯТИ (зеркало базы для скорости) ----------------

opened_today: dict[str, dtime] = {}
late_logged_today: set[str] = set()           # pvz, по которым уже есть запись в БД сегодня
sent_flags_today: set[str] = set()             # "morning", "summary_09:00", "final_report" — что уже отправлено
reminded_today: dict[str, set[int]] = {}       # pvz -> какие offset-напоминания уже отправлены сегодня
final_report_logged = False
last_git_push: datetime | None = None          # когда последний раз пушили report.json в git
current_day = datetime.now(TIMEZONE).date()
time_offset = timedelta(0)                     # используется только в тестовом режиме (/settime)


def get_now() -> datetime:
    return datetime.now(TIMEZONE) + time_offset


def is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == ADMIN_ID


def load_day_state() -> None:
    global opened_today, late_logged_today, sent_flags_today, final_report_logged, reminded_today
    date_str = current_day.isoformat()
    opened_today = db_load_openings(date_str)
    not_opened_logged = db_load_not_opened(date_str)
    late_logged_today = set(opened_today.keys()) | not_opened_logged
    sent_flags_today = db_load_flags(date_str)
    final_report_logged = "final_report" in sent_flags_today
    reminded_today = db_load_reminders_sent(date_str)


def reset_state_if_new_day() -> None:
    global current_day
    today = get_now().date()
    if today != current_day:
        current_day = today
        load_day_state()
        log.info("Новый день — состояние подтянуто из базы (пустое)")


def is_before_stop_time() -> bool:
    return get_now().time() < STOP_TIME


def is_within_accept_hours() -> bool:
    now = get_now().time()
    return ACCEPT_START <= now <= ACCEPT_END


def find_pvz_number(text: str) -> str | None:
    if not text:
        return None
    match = PVZ_PATTERN.search(text)
    if not match:
        return None
    candidate = match.group(0)
    for pvz in PVZ_LIST:
        if pvz.lower() == candidate.lower():
            return pvz
    return None


def build_status_text() -> str:
    """Для команды /status — текущая картина на данный момент (по запросу, не авторассылка)."""
    opened = sorted(opened_today.items(), key=lambda x: x[1])
    lines = [f"📋 <b>Статус на {get_now().strftime('%H:%M')}</b>", ""]
    lines.append(f"✅ Открылись ({len(opened)}/{len(PVZ_LIST)}):")
    for pvz, t in opened:
        scheduled = PVZ_LIST[pvz]["open_time"]
        note = " (досрочно)" if t < scheduled else (" (опоздание)" if t > scheduled else "")
        lines.append(f"• {pvz} — {t.strftime('%H:%M')}{note}")
    lines.append("")
    lines.append("⏳ Ещё не открылись:")
    not_opened = [pvz for pvz in PVZ_LIST if pvz not in opened_today]
    if not_opened:
        for pvz in not_opened:
            info = PVZ_LIST[pvz]
            lines.append(f"• {pvz} ({info['open_time'].strftime('%H:%M')}) — {info['username']}")
    else:
        lines.append("— все открылись —")
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def export_webapp_report() -> None:
    """Выгружает сегодняшние данные в report.json (формат для мини-приложения на GitHub Pages).
    Подтягивает существующий файл и добавляет/обновляет только сегодняшний день, чтобы история
    за прошлые дни не терялась."""
    import json

    date_str = current_day.isoformat()
    db_status = db_full_report_rows(date_str)

    rows = []
    for pvz, info in PVZ_LIST.items():
        scheduled = info["open_time"].strftime("%H:%M")
        if pvz in opened_today:
            actual_t = opened_today[pvz]
            actual = actual_t.strftime("%H:%M")
            if actual_t > info["open_time"]:
                status, delay = "late", int((datetime.combine(current_day, actual_t) -
                                              datetime.combine(current_day, info["open_time"])).total_seconds() // 60)
            elif actual_t < info["open_time"]:
                status, delay = "early", 0
            else:
                status, delay = "ontime", 0
        else:
            actual, status, delay = None, ("none" if db_status.get(pvz) == "not_opened" else "none"), 0
        rows.append({
            "pvz": pvz, "scheduled": scheduled, "actual": actual,
            "status": status, "username": info["username"], "delay_minutes": delay,
        })

    try:
        with open(WEBAPP_REPORT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"days": {}}

    data["days"][date_str] = {
        "generated_at": get_now().strftime("%d.%m.%Y %H:%M"),
        "rows": rows,
    }

    try:
        with open(WEBAPP_REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"Не удалось записать {WEBAPP_REPORT_PATH}: {e}")


def _git_push_sync() -> None:
    """Синхронная часть (выполняется в отдельном потоке, чтобы не блокировать бота)."""
    import subprocess
    try:
        subprocess.run(["git", "add", "report.json"], cwd=GIT_REPO_DIR, check=True,
                        capture_output=True, text=True)
        commit = subprocess.run(
            ["git", "commit", "-m", f"report.json: авто-обновление {get_now().strftime('%d.%m %H:%M')}"],
            cwd=GIT_REPO_DIR, capture_output=True, text=True,
        )
        # если нечего коммитить — git commit вернёт ненулевой код, это нормально, просто выходим
        if commit.returncode != 0:
            if "nothing to commit" in (commit.stdout + commit.stderr).lower():
                return
            log.warning(f"git commit: {commit.stdout} {commit.stderr}")
            return
        push = subprocess.run(["git", "push"], cwd=GIT_REPO_DIR, capture_output=True, text=True)
        if push.returncode != 0:
            log.warning(f"git push не удался: {push.stdout} {push.stderr}")
        else:
            log.info("report.json запушен в GitHub")
    except FileNotFoundError:
        log.warning("git не найден в системе — автопуш отключи (GIT_AUTO_PUSH = False) или установи git")
    except subprocess.CalledProcessError as e:
        log.warning(f"git add не удался: {e.stderr}")


async def git_push_report_if_due() -> None:
    """Пушит report.json в GitHub не чаще раза в GIT_PUSH_MIN_INTERVAL_MIN минут."""
    global last_git_push
    if not GIT_AUTO_PUSH:
        return
    now = get_now()
    if last_git_push is not None and (now - last_git_push).total_seconds() < GIT_PUSH_MIN_INTERVAL_MIN * 60:
        return
    last_git_push = now
    await asyncio.to_thread(_git_push_sync)


def build_full_report_text() -> str:
    db_status = db_full_report_rows(current_day.isoformat())
    lines = [f"📊 <b>Полный отчёт за {current_day.strftime('%d.%m.%Y')}</b>", ""]
    for open_time in sorted(set(info["open_time"] for info in PVZ_LIST.values())):
        block = [f"<b>🕒 {open_time.strftime('%H:%M')}</b>"]
        for pvz, info in PVZ_LIST.items():
            if info["open_time"] != open_time:
                continue
            if pvz in opened_today:
                actual = opened_today[pvz]
                if actual > open_time:
                    block.append(f"⚠️ {pvz} — открылся в {actual.strftime('%H:%M')} (с опозданием)")
                elif actual < open_time:
                    block.append(f"🔹 {pvz} — открылся в {actual.strftime('%H:%M')} (досрочно)")
                else:
                    block.append(f"✅ {pvz} — открылся в {actual.strftime('%H:%M')}")
            elif db_status.get(pvz) == "not_opened":
                block.append(f"❌ {pvz} — не открылся ({info['username']})")
            else:
                block.append(f"⏳ {pvz} — пока не открылся ({info['username']})")
        lines.append("<blockquote>" + "\n".join(block) + "</blockquote>")
    return "\n".join(lines).strip()


def build_group_summary_text(open_time: dtime) -> str:
    """Итог по одной группе сразу после того, как её время прошло."""
    group_pvz = [pvz for pvz, info in PVZ_LIST.items() if info["open_time"] == open_time]
    not_opened = [pvz for pvz in group_pvz if pvz not in opened_today]

    if not not_opened:
        return f"<blockquote>🎉 <b>ПВЗ группы {open_time.strftime('%H:%M')} — все открылись!</b> ✅</blockquote>"

    opened_count = len(group_pvz) - len(not_opened)
    lines = [f"🕒 <b>Итог по группе {open_time.strftime('%H:%M')}:</b> открылись {opened_count}/{len(group_pvz)}", ""]
    lines.append("❌ Не открылись:")
    for pvz in not_opened:
        lines.append(f"• {pvz} — {PVZ_LIST[pvz]['username']}")
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


async def mark_opened(pvz: str) -> None:
    """Отмечает ПВЗ открытым в памяти и в базе. Ничего в чат не пишет (это делает вызывающий код)."""
    opened_time = get_now().time()
    opened_today[pvz] = opened_time
    late_logged_today.add(pvz)

    scheduled = PVZ_LIST[pvz]["open_time"]
    status = "late" if opened_time > scheduled else ("early" if opened_time < scheduled else "ontime")
    db_save_opening(current_day.isoformat(), pvz, opened_time, status)
    log.info(f"{pvz} отмечен как открытый в {opened_time.strftime('%H:%M')} ({status})")
    export_webapp_report()


async def broadcast(text: str, parse_mode: str | None = None) -> None:
    """Отправляет сообщение во все группы из GROUP_CHAT_IDS."""
    for chat_id in GROUP_CHAT_IDS:
        try:
            await bot.send_message(chat_id, text, parse_mode=parse_mode)
        except Exception as e:
            log.warning(f"Не удалось отправить сообщение в {chat_id}: {e}")


@dp.message(F.chat.id.in_(GROUP_CHAT_IDS), F.photo)
async def handle_photo(message: Message) -> None:
    reset_state_if_new_day()

    if not is_before_stop_time():
        return
    if not is_within_accept_hours():
        return  # ночное фото — игнорируем

    text = message.caption or message.text or ""
    pvz = find_pvz_number(text)

    if not pvz or pvz in opened_today:
        return

    try:
        await bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji="🔥")],
        )
    except Exception as e:
        log.warning(f"Не удалось поставить реакцию: {e}")

    await mark_opened(pvz)  # тихо, без сообщений в чат


@dp.message(Command("chatid"))
async def handle_chatid(message: Message) -> None:
    """Служебная команда — работает в любом чате, без ограничений. Показывает id чата,
    чтобы сверить с GROUP_CHAT_IDS в настройках."""
    await message.answer(f"id этого чата: <code>{message.chat.id}</code>", parse_mode=ParseMode.HTML)


def webapp_keyboard() -> InlineKeyboardMarkup | None:
    """Кнопка, открывающая мини-приложение. Возвращает None, пока ссылка не настроена."""
    if not WEBAPP_URL.startswith("https://") or "ВПИШИ-СЮДА" in WEBAPP_URL:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📊 Открыть свод", url=WEBAPP_URL)
    ]])


@dp.message(F.chat.id.in_(GROUP_CHAT_IDS), Command("status"))
async def handle_status_command(message: Message) -> None:
    if not is_admin(message):
        return
    reset_state_if_new_day()
    await message.answer(build_status_text(), parse_mode=ParseMode.HTML, reply_markup=webapp_keyboard())


@dp.message(F.chat.id.in_(GROUP_CHAT_IDS), F.text.lower() == "дай свод всех пвз")
async def handle_allreport_command(message: Message) -> None:
    if not is_admin(message):
        return
    reset_state_if_new_day()
    await message.answer(build_full_report_text(), parse_mode=ParseMode.HTML, reply_markup=webapp_keyboard())


# ---------------- ТЕСТОВЫЕ КОМАНДЫ (только если TEST_MODE = True, только ADMIN_ID) ----------------

@dp.message(F.chat.id.in_(GROUP_CHAT_IDS), Command("settime"))
async def handle_settime(message: Message) -> None:
    if not TEST_MODE or not is_admin(message):
        return
    global time_offset
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /settime 08:55")
        return
    try:
        target = datetime.strptime(parts[1], "%H:%M").time()
    except ValueError:
        await message.answer("Не понял время. Формат: /settime 08:55")
        return
    real_now = datetime.now(TIMEZONE)
    target_dt = datetime.combine(real_now.date(), target, tzinfo=TIMEZONE)
    time_offset = target_dt - real_now
    await message.answer(f"🧪 Тестовое время установлено: {target.strftime('%H:%M')}")


@dp.message(F.chat.id.in_(GROUP_CHAT_IDS), Command("testopen"))
async def handle_testopen(message: Message) -> None:
    if not TEST_MODE or not is_admin(message):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /testopen ТАШ-62")
        return
    pvz = find_pvz_number(parts[1])
    if not pvz:
        await message.answer("Такой ПВЗ не найден в списке")
        return
    await mark_opened(pvz)
    await message.answer(f"🧪 {pvz} отмечен открытым")


@dp.message(F.chat.id.in_(GROUP_CHAT_IDS), Command("resettest"))
async def handle_resettest(message: Message) -> None:
    if not TEST_MODE or not is_admin(message):
        return
    global time_offset
    db_clear_today(current_day.isoformat())
    load_day_state()
    time_offset = timedelta(0)
    await message.answer("🧪 Тестовое состояние сброшено (записи за сегодня удалены из базы)")


# -----------------------------------------------------------------------------


async def send_reminders() -> None:
    """Тегает каждый ещё не открывшийся ПВЗ отдельным сообщением за 20 и за 10 минут до открытия."""
    now_dt = get_now()

    for pvz, info in PVZ_LIST.items():
        if pvz in opened_today:
            continue

        open_dt = datetime.combine(current_day, info["open_time"], tzinfo=TIMEZONE)
        minutes_left = (open_dt - now_dt).total_seconds() / 60
        if minutes_left <= 0:
            continue

        sent = reminded_today.setdefault(pvz, set())
        for offset in REMINDER_OFFSETS:
            if offset in sent:
                continue
            if minutes_left <= offset:
                sent.add(offset)
                db_save_reminder_sent(current_day.isoformat(), pvz, offset)
                text = f"<blockquote>⏰ <b>{pvz}</b> через {offset} мин — {info['username']}, отчёт!</blockquote>"
                try:
                    await broadcast(text, parse_mode=ParseMode.HTML)
                except Exception as e:
                    log.warning(f"Не удалось отправить напоминание по {pvz}: {e}")


async def background_loop() -> None:
    """Раз в минуту: утреннее приветствие, итог по группе сразу после её времени,
    финальная запись за день в STOP_TIME."""
    global final_report_logged
    while True:
        reset_state_if_new_day()
        now_time = get_now().time()
        date_str = current_day.isoformat()

        # утреннее приветствие
        if now_time >= MORNING_MESSAGE_TIME and "morning" not in sent_flags_today:
            try:
                await broadcast(format_morning_message(pick_morning_message()), parse_mode=ParseMode.HTML)
            except Exception as e:
                log.warning(f"Не удалось отправить утреннее приветствие: {e}")
            sent_flags_today.add("morning")
            db_set_flag(date_str, "morning")

        # тег-напоминания за 20/10 минут до открытия
        await send_reminders()

        # итог по каждой группе сразу после того, как её время прошло
        for open_time in sorted(set(info["open_time"] for info in PVZ_LIST.values())):
            flag = f"summary_{open_time.strftime('%H:%M')}"
            if now_time >= open_time and flag not in sent_flags_today:
                try:
                    await broadcast(build_group_summary_text(open_time), parse_mode=ParseMode.HTML)
                except Exception as e:
                    log.warning(f"Не удалось отправить итог по группе {open_time}: {e}")
                sent_flags_today.add(flag)
                db_set_flag(date_str, flag)

        # финальная запись за день (после STOP_TIME, один раз)
        if now_time >= STOP_TIME and not final_report_logged:
            for pvz in PVZ_LIST:
                if pvz not in opened_today and pvz not in late_logged_today:
                    late_logged_today.add(pvz)
                    db_save_opening(date_str, pvz, None, "not_opened")
            final_report_logged = True
            sent_flags_today.add("final_report")
            db_set_flag(date_str, "final_report")
            log.info("Финальная запись за день сделана (STOP_TIME наступил)")

        export_webapp_report()
        await git_push_report_if_due()
        await asyncio.sleep(60)


async def main() -> None:
    db_connect().close()  # создаёт файл базы и таблицы при первом запуске
    load_day_state()
    if TEST_MODE:
        log.warning("⚠️ ТЕСТОВЫЙ РЕЖИМ ВКЛЮЧЁН — не забудь выключить (TEST_MODE = False) перед боевым запуском")
    asyncio.create_task(background_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
