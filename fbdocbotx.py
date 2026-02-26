#!/usr/bin/env python3
"""
Telegram Bot: Facebook Account XLSX Generator
- Python 3.11+
- python-telegram-bot v20+
- openpyxl
- Modular handler architecture in a single file
- Strict validation
- ConversationHandler for interactive flows
"""

from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Sequence, Tuple

from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from telegram import (
    InputFile,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# -----------------------------------------------------------------------------
# Environment & Logging
# -----------------------------------------------------------------------------

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger("fbdocbotx")

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

MAIN_MENU_CREATE_DOC = "1️⃣ Buat Dokumen"
MAIN_MENU_HELP = "2️⃣ Bantuan"

SUBMENU_MANUAL = "Input Manual"
SUBMENU_INSTANT = "Input Instan"
SUBMENU_BACK = "⬅️ Kembali"

UID_REGEX = re.compile(r"^[0-9]{8,20}$")
PASSWORD_REGEX = re.compile(r"^[^\s]{6,64}$")

# Delimiter split: comma, whitespace (space/tab/newline), including multiple
SPLIT_REGEX = re.compile(r"[,\s]+")

# Strict cookie key=value; validator (semicolon optional at end)
COOKIE_FORMAT_REGEX = re.compile(
    r"^\s*[A-Za-z0-9_]+=[^;=\n\r]+(?:;\s*[A-Za-z0-9_]+=[^;=\n\r]+)*;?\s*$"
)


class States(IntEnum):
    ASK_UID = 1
    ASK_PASSWORD = 2
    ASK_COOKIE = 3


@dataclass
class ParsedInput:
    uids: List[str]
    passwords: List[str]
    cookies: List[str]


# -----------------------------------------------------------------------------
# Keyboard Builders
# -----------------------------------------------------------------------------

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(MAIN_MENU_CREATE_DOC)],
            [KeyboardButton(MAIN_MENU_HELP)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def create_doc_submenu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(SUBMENU_MANUAL), KeyboardButton(SUBMENU_INSTANT)],
            [KeyboardButton(SUBMENU_BACK)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


# -----------------------------------------------------------------------------
# Parsing & Validation Utilities
# -----------------------------------------------------------------------------

def split_tokens(text: str) -> List[str]:
    """
    Split by comma/space/newline and strip empties.
    """
    return [part.strip() for part in SPLIT_REGEX.split(text.strip()) if part.strip()]


def has_delimiter(raw: str) -> bool:
    """
    Ensure input uses at least one delimiter among comma/space/newline.
    Required by spec for manual UID step.
    """
    return bool(re.search(r"[,\s]", raw))


def validate_uids(uids: Sequence[str]) -> Tuple[bool, str]:
    if not uids:
        return False, "❌ UID kosong. Silakan masukkan minimal 1 UID."
    for i, uid in enumerate(uids, start=1):
        if not UID_REGEX.fullmatch(uid):
            return (
                False,
                f"❌ UID ke-{i} tidak valid: `{uid}`\n"
                "Syarat UID: hanya digit, panjang 8–20.",
            )
    return True, ""


def validate_passwords(passwords: Sequence[str]) -> Tuple[bool, str]:
    if not passwords:
        return False, "❌ Password kosong. Silakan masukkan minimal 1 password."
    for i, pwd in enumerate(passwords, start=1):
        if not PASSWORD_REGEX.fullmatch(pwd):
            return (
                False,
                f"❌ Password ke-{i} tidak valid.\n"
                "Syarat password: 6–64 karakter, tanpa spasi.",
            )
    return True, ""


def validate_cookie(cookie: str) -> Tuple[bool, str]:
    c = cookie.strip()
    if not c:
        return False, "Cookie tidak boleh kosong."
    if len(c) < 20:
        return False, "Cookie minimal 20 karakter."
    if "c_user=" not in c or "xs=" not in c:
        return False, "Cookie wajib mengandung 'c_user=' dan 'xs='."
    if not COOKIE_FORMAT_REGEX.fullmatch(c):
        return False, "Format cookie harus key=value;key=value; (dipisah ';')."
    return True, ""


def validate_cookies(cookies: Sequence[str]) -> Tuple[bool, str]:
    if not cookies:
        return False, "❌ Cookie kosong. Silakan masukkan minimal 1 cookie."
    for i, ck in enumerate(cookies, start=1):
        ok, reason = validate_cookie(ck)
        if not ok:
            return False, f"❌ Cookie ke-{i} tidak valid.\nAlasan: {reason}"
    return True, ""


def parse_instant_message(text: str) -> Tuple[bool, str, ParsedInput | None]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False, "❌ Format Input Instan minimal 3 baris: UID, PASSWORD, COOKIE.", None

    uid_line, pwd_line, cookie_line = lines[0], lines[1], lines[2]

    uids = split_tokens(uid_line)
    passwords = split_tokens(pwd_line)
    cookies = split_tokens(cookie_line)

    ok, err = validate_uids(uids)
    if not ok:
        return False, err, None

    ok, err = validate_passwords(passwords)
    if not ok:
        return False, err, None

    ok, err = validate_cookies(cookies)
    if not ok:
        return False, err, None

    if not (len(uids) == len(passwords) == len(cookies)):
        return (
            False,
            "❌ Jumlah UID, PASSWORD, dan COOKIE harus sama.\n"
            f"UID={len(uids)}, PASSWORD={len(passwords)}, COOKIE={len(cookies)}",
            None,
        )

    return True, "", ParsedInput(uids=uids, passwords=passwords, cookies=cookies)


# -----------------------------------------------------------------------------
# XLSX Generator
# -----------------------------------------------------------------------------

def build_xlsx_file(data: ParsedInput) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "DATA"

    headers = ["UID", "PASSWORD", "COOKIE"]
    ws.append(headers)

    for uid, pwd, cookie in zip(data.uids, data.passwords, data.cookies):
        ws.append([uid, pwd, cookie])

    # Styles
    header_font = Font(name="Calibri", size=13, bold=True, color="FFFFFF")
    data_font = Font(name="Calibri", size=11)
    header_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    medium_side = Side(style="medium", color="000000")
    all_border = Border(
        left=medium_side, right=medium_side, top=medium_side, bottom=medium_side
    )

    # Header style
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = all_border

    # Data style
    max_row = ws.max_row
    for r in range(2, max_row + 1):
        for c in range(1, 4):
            cell = ws.cell(row=r, column=c)
            cell.font = data_font
            cell.border = all_border
            if c == 3:
                # COOKIE wrap text enabled
                cell.alignment = Alignment(
                    horizontal="left", vertical="center", wrap_text=True
                )
            else:
                cell.alignment = Alignment(
                    horizontal="left", vertical="center", wrap_text=False
                )

    # Column widths
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 25
    ws.column_dimensions["C"].width = 80

    # Freeze top row and autofilter
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:C{max_row}"

    # Row heights (optional polish)
    ws.row_dimensions[1].height = 24

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


# -----------------------------------------------------------------------------
# Response Helpers
# -----------------------------------------------------------------------------

HELP_TEXT = (
    "📘 *Bantuan Penggunaan Bot*\n\n"
    "• *Menu Utama*\n"
    "  - 1️⃣ Buat Dokumen\n"
    "  - 2️⃣ Bantuan\n\n"
    "• *Input Manual*\n"
    "  - Langkah 1: kirim daftar UID (pisahkan dengan spasi/koma/baris baru)\n"
    "  - Langkah 2: kirim daftar PASSWORD (jumlah harus sama)\n"
    "  - Langkah 3: kirim daftar COOKIE (jumlah harus sama)\n\n"
    "• *Input Instan*\n"
    "  - Kirim 1 pesan dengan minimal 3 baris:\n"
    "    1) baris UID\n"
    "    2) baris PASSWORD\n"
    "    3) baris COOKIE\n\n"
    "• *Contoh Format Benar (Input Instan)*\n"
    "  - Baris 1: 12345678901234 12345678901235\n"
    "  - Baris 2: Pass1234 Pass5678\n"
    "  - Baris 3: c_user=aaa;xs=bbb; c_user=ccc;xs=ddd;\n\n"
    "• *Validasi Ketat*\n"
    "  - UID: hanya digit, 8–20 karakter (`^[0-9]{8,20}$`)\n"
    "  - PASSWORD: 6–64 karakter, tanpa spasi (`^[^\\s]{6,64}$`)\n"
    "  - COOKIE: wajib ada `c_user=` dan `xs=`, minimal 20 karakter, format key=value;key=value;\n\n"
    "• *Kesalahan Umum*\n"
    "  - Jumlah UID/PASSWORD/COOKIE tidak sama\n"
    "  - UID mengandung huruf/simbol\n"
    "  - PASSWORD mengandung spasi\n"
    "  - COOKIE tidak lengkap atau format salah\n"
)


async def send_xlsx_result(update: Update, context: ContextTypes.DEFAULT_TYPE, data: ParsedInput) -> None:
    try:
        xlsx_buffer = build_xlsx_file(data)
        await update.effective_chat.send_document(
            document=InputFile(xlsx_buffer, filename="facebook_accounts_.xlsx"),
            caption="✅ Dokumen berhasil dibuat.",
        )
    except Exception:
        logger.exception("Failed to generate/send XLSX")
        await update.effective_message.reply_text(
            "❌ Terjadi kesalahan saat membuat file XLSX. Silakan coba lagi."
        )


def reset_user_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("uids", None)
    context.user_data.pop("passwords", None)


# -----------------------------------------------------------------------------
# Handlers: Core Menu
# -----------------------------------------------------------------------------

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_user_session(context)
    await update.effective_message.reply_text(
        "👋 Selamat datang.\nPilih menu di bawah ini:",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text(
        HELP_TEXT,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def menu_create_doc_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_user_session(context)
    await update.effective_message.reply_text(
        "📄 *Buat Dokumen*\nPilih metode input:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=create_doc_submenu_keyboard(),
    )
    return ConversationHandler.END


# -----------------------------------------------------------------------------
# Handlers: Manual Flow
# -----------------------------------------------------------------------------

async def manual_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_user_session(context)
    await update.effective_message.reply_text(
        "Masukkan daftar UID (pisahkan dengan spasi, koma, atau baris baru):",
        reply_markup=ReplyKeyboardRemove(),
    )
    return States.ASK_UID


async def ask_uid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.effective_message.text or "").strip()

    if not has_delimiter(raw):
        await update.effective_message.reply_text(
            "❌ Format UID ditolak. Gunakan pemisah spasi, koma, atau baris baru."
        )
        return States.ASK_UID

    uids = split_tokens(raw)
    ok, err = validate_uids(uids)
    if not ok:
        await update.effective_message.reply_text(err, parse_mode=ParseMode.MARKDOWN)
        return States.ASK_UID

    context.user_data["uids"] = uids
    await update.effective_message.reply_text(
        "✅ UID valid.\nSekarang masukkan password (jumlah harus sama dengan UID):"
    )
    return States.ASK_PASSWORD


async def ask_password_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.effective_message.text or "").strip()
    passwords = split_tokens(raw)

    ok, err = validate_passwords(passwords)
    if not ok:
        await update.effective_message.reply_text(err)
        return States.ASK_PASSWORD

    uids = context.user_data.get("uids", [])
    if len(passwords) != len(uids):
        await update.effective_message.reply_text(
            f"❌ Jumlah password tidak sama dengan UID.\nUID={len(uids)}, PASSWORD={len(passwords)}"
        )
        return States.ASK_PASSWORD

    context.user_data["passwords"] = passwords
    await update.effective_message.reply_text(
        "✅ Password valid.\nSekarang masukkan cookie (jumlah harus sama dengan UID):"
    )
    return States.ASK_COOKIE


async def ask_cookie_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.effective_message.text or "").strip()
    cookies = split_tokens(raw)

    ok, err = validate_cookies(cookies)
    if not ok:
        await update.effective_message.reply_text(err)
        return States.ASK_COOKIE

    uids = context.user_data.get("uids", [])
    passwords = context.user_data.get("passwords", [])

    if not (len(uids) == len(passwords) == len(cookies)):
        await update.effective_message.reply_text(
            "❌ Jumlah UID, PASSWORD, dan COOKIE harus sama.\n"
            f"UID={len(uids)}, PASSWORD={len(passwords)}, COOKIE={len(cookies)}"
        )
        return States.ASK_COOKIE

    parsed = ParsedInput(uids=uids, passwords=passwords, cookies=cookies)
    await send_xlsx_result(update, context, parsed)

    reset_user_session(context)
    await update.effective_message.reply_text(
        "Kembali ke menu utama.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


# -----------------------------------------------------------------------------
# Handlers: Instant Flow
# -----------------------------------------------------------------------------

async def instant_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text(
        "Kirim data *Input Instan* dalam 1 pesan (minimal 3 baris):\n"
        "Baris 1 = UID\nBaris 2 = PASSWORD\nBaris 3 = COOKIE\n\n"
        "Contoh:\n"
        "12345678901234 12345678901235\n"
        "Pass1234 Pass5678\n"
        "c_user=aaa;xs=bbb; c_user=ccc;xs=ddd;",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def instant_payload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    ok, err, parsed = parse_instant_message(text)

    if not ok or parsed is None:
        await update.effective_message.reply_text(err)
        return ConversationHandler.END

    await send_xlsx_result(update, context, parsed)
    await update.effective_message.reply_text(
        "Kembali ke menu utama.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


# -----------------------------------------------------------------------------
# Fallback & Global Text Router
# -----------------------------------------------------------------------------

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_user_session(context)
    await update.effective_message.reply_text(
        "❎ Proses dibatalkan. Kembali ke menu utama.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def global_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Route top-level non-conversation messages:
    - Main menu selections
    - Submenu selections
    - Instant payload auto-detection (3+ lines)
    """
    text = (update.effective_message.text or "").strip()

    if text == MAIN_MENU_CREATE_DOC:
        await menu_create_doc_handler(update, context)
        return

    if text == MAIN_MENU_HELP:
        await help_handler(update, context)
        return

    if text == SUBMENU_BACK:
        await update.effective_message.reply_text(
            "Kembali ke menu utama.",
            reply_markup=main_menu_keyboard(),
        )
        return

    if text == SUBMENU_MANUAL:
        # Start manual conversation by prompting; conversation entry point remains command/text-based
        await manual_start_handler(update, context)
        return

    if text == SUBMENU_INSTANT:
        await instant_handler(update, context)
        return

    # Auto-detect possible instant payload (3+ non-empty lines)
    non_empty_lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(non_empty_lines) >= 3:
        await instant_payload_handler(update, context)
        return

    await update.effective_message.reply_text(
        "Saya tidak mengenali perintah tersebut.\n"
        "Gunakan menu yang tersedia.",
        reply_markup=main_menu_keyboard(),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception: %s", context.error)


# -----------------------------------------------------------------------------
# Application Setup
# -----------------------------------------------------------------------------

def build_application() -> Application:
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_TOKEN tidak ditemukan. "
            "Silakan isi TELEGRAM_TOKEN di environment/.env."
        )

    app = Application.builder().token(token).build()

    # Manual conversation flow
    manual_conv = ConversationHandler(
        entry_points=[
            CommandHandler("manual", manual_start_handler),
            MessageHandler(filters.Regex(f"^{re.escape(SUBMENU_MANUAL)}$"), manual_start_handler),
        ],
        states={
            States.ASK_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_uid_handler)],
            States.ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password_handler)],
            States.ASK_COOKIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_cookie_handler)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_handler),
            MessageHandler(filters.Regex(r"^/cancel$"), cancel_handler),
        ],
        allow_reentry=True,
        name="manual_conversation",
        persistent=False,
    )

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("buat", menu_create_doc_handler))
    app.add_handler(CommandHandler("instan", instant_handler))
    app.add_handler(manual_conv)

    # Global text router (outside active conversation)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, global_text_router))

    app.add_error_handler(error_handler)
    return app


def main() -> None:
    try:
        app = build_application()
        logger.info("Bot is running...")
        app.run_polling(drop_pending_updates=True)
    except Exception:
        logger.exception("Fatal error while starting bot")
        raise


if __name__ == "__main__":
    main()
