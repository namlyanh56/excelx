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

MAIN_MENU_CREATE_DOC = "📝 Buat Dokumen Excel"
MAIN_MENU_HELP = "ℹ️ Pusat Bantuan"

SUBMENU_MANUAL = "⌨️ Input Manual"
SUBMENU_INSTANT = "⚡ Input Instan"
SUBMENU_BACK = "🔙 Kembali"

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
        return False, "❌ <b>Oops! UID kosong.</b>\nSilakan masukkan minimal 1 UID."
    for i, uid in enumerate(uids, start=1):
        if not UID_REGEX.fullmatch(uid):
            return (
                False,
                f"❌ <b>UID ke-{i} tidak valid:</b> <code>{uid}</code>\n"
                "📌 <i>Syarat: Hanya boleh berisi angka (digit) dengan panjang 8–20 karakter.</i>",
            )
    return True, ""


def validate_passwords(passwords: Sequence[str]) -> Tuple[bool, str]:
    if not passwords:
        return False, "❌ <b>Oops! Password kosong.</b>\nSilakan masukkan minimal 1 password."
    for i, pwd in enumerate(passwords, start=1):
        if not PASSWORD_REGEX.fullmatch(pwd):
            return (
                False,
                f"❌ <b>Password ke-{i} tidak valid.</b>\n"
                "📌 <i>Syarat: 6–64 karakter dan tidak boleh mengandung spasi.</i>",
            )
    return True, ""


def validate_cookie(cookie: str) -> Tuple[bool, str]:
    c = cookie.strip()
    if not c:
        return False, "Cookie tidak boleh kosong."
    if len(c) < 20:
        return False, "Cookie minimal 20 karakter."
    if "c_user=" not in c or "xs=" not in c:
        return False, "Cookie wajib mengandung <code>c_user=</code> dan <code>xs=</code>."
    if not COOKIE_FORMAT_REGEX.fullmatch(c):
        return False, "Format harus <code>key=value;key=value;</code> (dipisah dengan ';')."
    return True, ""


def validate_cookies(cookies: Sequence[str]) -> Tuple[bool, str]:
    if not cookies:
        return False, "❌ <b>Oops! Cookie kosong.</b>\nSilakan masukkan minimal 1 cookie."
    for i, ck in enumerate(cookies, start=1):
        ok, reason = validate_cookie(ck)
        if not ok:
            return False, f"❌ <b>Cookie ke-{i} tidak valid.</b>\n💡 <i>Alasan: {reason}</i>"
    return True, ""


def parse_instant_message(text: str) -> Tuple[bool, str, ParsedInput | None]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False, "❌ <b>Gagal! Format Input Instan memerlukan minimal 3 baris:</b>\n1️⃣ Baris UID\n2️⃣ Baris PASSWORD\n3️⃣ Baris COOKIE", None

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
            "❌ <b>Jumlah data tidak seimbang!</b>\n"
            f"📊 UID: {len(uids)}\n"
            f"🔑 PASSWORD: {len(passwords)}\n"
            f"🍪 COOKIE: {len(cookies)}\n\n"
            "💡 <i>Tips: Pastikan jumlah UID, PASSWORD, dan COOKIE sama banyak.</i>",
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
    "📚 <b>PUSAT BANTUAN FBDOCBOT</b> 📚\n\n"
    "🎯 <b>Menu Utama</b>\n"
    "• 📝 <b>Buat Dokumen:</b> Memulai proses pembuatan file Excel.\n"
    "• ℹ️ <b>Bantuan:</b> Menampilkan panduan penggunaan bot ini.\n\n"
    "⌨️ <b>Input Manual (Langkah demi Langkah)</b>\n"
    "1️⃣ Kirim daftar <b>UID</b> (pisahkan dgn spasi/koma/enter)\n"
    "2️⃣ Kirim daftar <b>PASSWORD</b>\n"
    "3️⃣ Kirim daftar <b>COOKIE</b>\n"
    "<i>*Catatan: Jumlah data di setiap langkah harus seimbang.</i>\n\n"
    "⚡ <b>Input Instan (Cepat)</b>\n"
    "Kirim langsung 1 pesan berisi minimal 3 baris:\n"
    "Baris 1: <code>[Daftar UID]</code>\n"
    "Baris 2: <code>[Daftar PASSWORD]</code>\n"
    "Baris 3: <code>[Daftar COOKIE]</code>\n\n"
    "💡 <b>Aturan Validasi Ketat:</b>\n"
    "• <b>UID:</b> 8-20 angka (<code>^[0-9]{8,20}$</code>)\n"
    "• <b>PASSWORD:</b> 6-64 karakter tanpa spasi\n"
    "• <b>COOKIE:</b> Wajib ada <code>c_user=</code> dan <code>xs=</code>\n\n"
    "⚠️ <b>Kesalahan Umum:</b>\n"
    "❌ Jumlah baris/data tidak sama\n"
    "❌ Terdapat huruf/simbol pada UID\n"
    "❌ Terdapat spasi pada PASSWORD"
)


async def send_xlsx_result(update: Update, context: ContextTypes.DEFAULT_TYPE, data: ParsedInput) -> None:
    try:
        xlsx_buffer = build_xlsx_file(data)
        await update.effective_chat.send_document(
            document=InputFile(xlsx_buffer, filename="facebook_accounts_.xlsx"),
            caption="🎉 <b>Yeay! Dokumen berhasil dibuat.</b>\nSilakan unduh file Excel Anda di atas. 📁✨",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        logger.exception("Failed to generate/send XLSX")
        await update.effective_message.reply_text(
            "❌ <b>Terjadi kesalahan sistem</b> saat membuat file XLSX. Silakan coba beberapa saat lagi.",
            parse_mode=ParseMode.HTML
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
        "🎉 <b>Selamat Datang di FBDocBot!</b> 🎉\n\n"
        "Saya siap membantu Anda menyusun data akun Facebook menjadi dokumen Excel yang rapi dan terorganisir dengan cepat. 📊✨\n\n"
        "Silakan pilih menu di bawah ini untuk memulai:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text(
        HELP_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def menu_create_doc_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_user_session(context)
    await update.effective_message.reply_text(
        "🛠️ <b>Menu Pembuatan Dokumen</b>\n\n"
        "Silakan pilih metode input data yang paling nyaman untuk Anda:\n"
        "• <b>Input Manual:</b> Dituntun langkah demi langkah.\n"
        "• <b>Input Instan:</b> Sekali kirim langsung jadi.\n",
        parse_mode=ParseMode.HTML,
        reply_markup=create_doc_submenu_keyboard(),
    )
    return ConversationHandler.END


# -----------------------------------------------------------------------------
# Handlers: Manual Flow
# -----------------------------------------------------------------------------

async def manual_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_user_session(context)
    await update.effective_message.reply_text(
        "📌 <b>Langkah 1: Input UID</b>\n\n"
        "Silakan masukkan daftar <b>UID</b> akun Anda.\n"
        "<i>(Pisahkan dengan spasi, koma, atau baris baru)</i> 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return States.ASK_UID


async def ask_uid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.effective_message.text or "").strip()

    if not has_delimiter(raw):
        await update.effective_message.reply_text(
            "❌ <b>Format UID ditolak.</b>\nMohon pisahkan antar UID dengan spasi, koma, atau enter (baris baru).",
            parse_mode=ParseMode.HTML
        )
        return States.ASK_UID

    uids = split_tokens(raw)
    ok, err = validate_uids(uids)
    if not ok:
        await update.effective_message.reply_text(err, parse_mode=ParseMode.HTML)
        return States.ASK_UID

    context.user_data["uids"] = uids
    await update.effective_message.reply_text(
        "✅ <b>UID Valid!</b>\n\n"
        "🔐 <b>Langkah 2: Input Password</b>\n"
        "Sekarang masukkan daftar <b>Password</b>.\n"
        f"<i>(Pastikan jumlahnya sama persis: <b>{len(uids)}</b> password)</i> 👇",
        parse_mode=ParseMode.HTML
    )
    return States.ASK_PASSWORD


async def ask_password_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.effective_message.text or "").strip()
    passwords = split_tokens(raw)

    ok, err = validate_passwords(passwords)
    if not ok:
        await update.effective_message.reply_text(err, parse_mode=ParseMode.HTML)
        return States.ASK_PASSWORD

    uids = context.user_data.get("uids", [])
    if len(passwords) != len(uids):
        await update.effective_message.reply_text(
            f"❌ <b>Jumlah password tidak sesuai dengan jumlah UID!</b>\n"
            f"📊 UID: <b>{len(uids)}</b> | 🔑 PASSWORD: <b>{len(passwords)}</b>\n\n"
            "<i>Mohon masukkan ulang dengan jumlah yang tepat.</i>",
            parse_mode=ParseMode.HTML
        )
        return States.ASK_PASSWORD

    context.user_data["passwords"] = passwords
    await update.effective_message.reply_text(
        "✅ <b>Password Valid!</b>\n\n"
        "🍪 <b>Langkah 3: Input Cookie</b>\n"
        "Terakhir, masukkan daftar <b>Cookie</b>.\n"
        f"<i>(Pastikan jumlahnya sama persis: <b>{len(uids)}</b> cookie)</i> 👇",
        parse_mode=ParseMode.HTML
    )
    return States.ASK_COOKIE


async def ask_cookie_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.effective_message.text or "").strip()
    cookies = split_tokens(raw)

    ok, err = validate_cookies(cookies)
    if not ok:
        await update.effective_message.reply_text(err, parse_mode=ParseMode.HTML)
        return States.ASK_COOKIE

    uids = context.user_data.get("uids", [])
    passwords = context.user_data.get("passwords", [])

    if not (len(uids) == len(passwords) == len(cookies)):
        await update.effective_message.reply_text(
            "❌ <b>Jumlah data tidak seimbang!</b>\n"
            f"📊 UID: <b>{len(uids)}</b>\n"
            f"🔑 PASSWORD: <b>{len(passwords)}</b>\n"
            f"🍪 COOKIE: <b>{len(cookies)}</b>\n\n"
            "<i>Mohon masukkan ulang dengan jumlah yang tepat.</i>",
            parse_mode=ParseMode.HTML
        )
        return States.ASK_COOKIE

    # Data Valid - Generate Excel
    parsed = ParsedInput(uids=uids, passwords=passwords, cookies=cookies)
    
    # Progress Message
    progress_msg = await update.effective_message.reply_text(
        "⏳ <i>Memproses data Anda. Mempersiapkan Excel...</i>",
        parse_mode=ParseMode.HTML
    )
    
    await send_xlsx_result(update, context, parsed)

    reset_user_session(context)
    
    # Clean up processing message & Show Main Menu again
    await progress_msg.delete()
    await update.effective_message.reply_text(
        "🔙 <b>Kembali ke Menu Utama</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


# -----------------------------------------------------------------------------
# Handlers: Instant Flow
# -----------------------------------------------------------------------------

async def instant_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text(
        "⚡ <b>Mode Input Instan</b>\n\n"
        "Kirimkan data Anda dalam <b>1 pesan sekaligus</b> (minimal 3 baris):\n\n"
        "1️⃣ <b>Baris 1:</b> Daftar UID\n"
        "2️⃣ <b>Baris 2:</b> Daftar PASSWORD\n"
        "3️⃣ <b>Baris 3:</b> Daftar COOKIE\n\n"
        "<b>Contoh Pesan:</b>\n"
        "<code>12345678901234 12345678901235</code>\n"
        "<code>Pass1234 Pass5678</code>\n"
        "<code>c_user=aaa;xs=bbb; c_user=ccc;xs=ddd;</code> 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def instant_payload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    ok, err, parsed = parse_instant_message(text)

    if not ok or parsed is None:
        await update.effective_message.reply_text(err, parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    progress_msg = await update.effective_message.reply_text(
        "⏳ <i>Memproses data instan Anda. Mempersiapkan Excel...</i>",
        parse_mode=ParseMode.HTML
    )

    await send_xlsx_result(update, context, parsed)
    
    await progress_msg.delete()
    await update.effective_message.reply_text(
        "🔙 <b>Kembali ke Menu Utama</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


# -----------------------------------------------------------------------------
# Fallback & Global Text Router
# -----------------------------------------------------------------------------

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reset_user_session(context)
    await update.effective_message.reply_text(
        "❎ <b>Proses dibatalkan.</b>\nAnda telah dikembalikan ke menu utama.",
        parse_mode=ParseMode.HTML,
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
            "🔙 <b>Kembali ke Menu Utama</b>",
            parse_mode=ParseMode.HTML,
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
        "🤔 <b>Mohon maaf, saya tidak mengenali pesan Anda.</b>\n"
        "Silakan gunakan tombol menu yang tersedia di bawah ini.",
        parse_mode=ParseMode.HTML,
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
