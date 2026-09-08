import io
import os
import re
import unicodedata
from datetime import datetime, timezone, timedelta
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

BOT_TOKEN = os.getenv('BOT_TOKEN', '8966364905:AAEJKwW7MFa7rV0oI53gtxKUZEiuTHp0_5M')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', 8359903974))
CS_USERNAME = 'bossgmailbotcs'
HARGA_PER_GMAIL = 4000
MAX_BULK_LIMIT = 50

# Deskripsi bawaan jika belum ada di database
DEFAULT_WELCOME_TEXT = (
    "✨ *SELAMAT DATANG DI BOT SETORAN GMAIL V30* ✨\n"
    "Halo *{first_name}*! Silakan baca informasi & aturan setoran di bawah ini:\n\n"
    "💵 *INFORMASI RATE & PROSES*\n"
    "• *Rate Per Akun:* Rp 4.000\n"
    "• *Estimasi Pengecekan:* 24 - 48 Jam Kerja\n"
    "• *Batas Bulking:* Maksimal 50 akun / setor\n\n"
    "🔑 *ATURAN KATA SANDI (PASSWORD AKTIF)*\n"
    "• Password yang valid hari ini: {pwd_str}\n\n"
    "⚠️ *SYARAT & KETENTUAN WAJIB*\n"
    "1. *Dilarang Double-Sell:* Dilarang keras menyetor Gmail duplikat yang sudah pernah terdaftar di bot.\n"
    "2. *Nomor HP Pemulihan:* Wajib dikosongkan / jangan diverifikasi.\n"
    "3. *Kondisi Akun:* Akun langsung menampilkan opsi sandi (Good), bukan captcha.\n\n"
    "👇 *Pilih menu di bawah ini untuk memulai:*"
)

def get_wib_time():
    wib_timezone = timezone(timedelta(hours=7))
    return datetime.now(wib_timezone).strftime("%d-%m-%Y %H:%M:%S WIB")

DEFAULT_MASTER_PASSWORDS = {
    'fineirga': 'ACTIVE',
    'sgsg1122': 'ACTIVE',
    'prabujaya': 'ACTIVE',
    'selaras9': 'ACTIVE'
}

REJECT_REASONS = [
    "Password Salah / Tidak Sesuai Rules",
    "Akun Terkena Bug / Captcha",
    "Nomor HP Pemulihan Terverifikasi",
    "Akun Terkena Sesi / Terkunci",
    "Format / Data Akun Tidak Valid"
]

DATABASE_URL = os.getenv('DATABASE_URL')

# ----------------- DATABASE SETUP -----------------
def get_db():
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        raise ValueError("DATABASE_URL belum diisi di Variable Environment Railway!")
    conn = psycopg2.connect(db_url)
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            balance BIGINT DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deposits (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            gmail TEXT UNIQUE,
            password TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            nominal BIGINT,
            metode TEXT,
            rekening TEXT,
            atas_nama TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    for pwd, default_status in DEFAULT_MASTER_PASSWORDS.items():
        setting_key = f"pwd_status_{pwd}"
        cursor.execute('''
            INSERT INTO bot_settings (key, value) 
            VALUES (%s, %s) 
            ON CONFLICT (key) DO NOTHING
        ''', (setting_key, default_status))

    # Inisialisasi template welcome text jika belum ada
    cursor.execute('''
        INSERT INTO bot_settings (key, value) 
        VALUES ('welcome_text', %s) 
        ON CONFLICT (key) DO NOTHING
    ''', (DEFAULT_WELCOME_TEXT,))

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dep_user ON deposits(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wd_user ON withdrawals(user_id)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_dep_lower_gmail ON deposits(TRIM(LOWER(gmail)))")
        
    conn.commit()
    cursor.close()
    conn.close()

def get_all_password_statuses():
    statuses = {}
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM bot_settings WHERE key LIKE 'pwd_status_%'")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        db_data = {r[0].replace('pwd_status_', ''): r[1] for r in rows}
        for pwd in DEFAULT_MASTER_PASSWORDS.keys():
            statuses[pwd] = db_data.get(pwd, DEFAULT_MASTER_PASSWORDS[pwd])
    except Exception:
        for pwd, st in DEFAULT_MASTER_PASSWORDS.items():
            statuses[pwd] = st
    return statuses

def set_password_status(password: str, status: str):
    conn = get_db()
    cursor = conn.cursor()
    setting_key = f"pwd_status_{password}"
    cursor.execute(
        "INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", 
        (setting_key, status)
    )
    conn.commit()
    cursor.close()
    conn.close()

def get_current_allowed_passwords():
    statuses = get_all_password_statuses()
    return [pwd for pwd, st in statuses.items() if st == 'ACTIVE']

def set_setting(key: str, value: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, value)
    )
    conn.commit()
    cursor.close()
    conn.close()

def get_setting(key: str, default_value: str = ""):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_settings WHERE key = %s", (key,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row[0] if row else default_value
    except Exception:
        return default_value

# ----------------- KEYBOARD MENUS -----------------
def persistent_reply_keyboard():
    keyboard = [
        [KeyboardButton("🔄 Refresh / Start"), KeyboardButton("📜 Daftar Setoran Saya")],
        [KeyboardButton("💰 Cek Saldo"), KeyboardButton("💬 Hubungi CS")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def main_menu_keyboard(user_id):
    keyboard = [
        [
            InlineKeyboardButton("📥 Setor Satuan", callback_data="menu_satuan"),
            InlineKeyboardButton("📦 Setor Bulking", callback_data="menu_bulking")
        ],
        [
            InlineKeyboardButton("💰 Cek Saldo", callback_data="menu_saldo"),
            InlineKeyboardButton("💸 Penarikan Dana", callback_data="menu_tarik")
        ],
        [
            InlineKeyboardButton("📜 Riwayat Setoran", callback_data="menu_riwayat"),
            InlineKeyboardButton("🧾 Riwayat Withdraw", callback_data="menu_riwayat_wd")
        ],
        [
            InlineKeyboardButton("💬 Hubungi CS / Admin", url=f"https://t.me/{CS_USERNAME}")
        ]
    ]
    if user_id == ADMIN_CHAT_ID:
        keyboard.append([InlineKeyboardButton("⚙️ Panel Admin", callback_data="admin_panel")])
        
    return InlineKeyboardMarkup(keyboard)

def cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal / Kembali ke Menu Utama", callback_data="menu_utama")]])

def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali ke Menu Utama", callback_data="menu_utama")]])

def get_welcome_text(first_name):
    allowed_pwds = get_current_allowed_passwords()
    if allowed_pwds:
        pwd_str = ", ".join([f"`{p}`" for p in allowed_pwds])
    else:
        pwd_str = "_Tidak ada sandi yang aktif saat ini_"

    template = get_setting('welcome_text', DEFAULT_WELCOME_TEXT)
    
    # Format variabel {first_name} dan {pwd_str} jika tersedia di teks template
    try:
        text_formatted = template.format(first_name=first_name, pwd_str=pwd_str)
    except KeyError:
        text_formatted = template

    return text_formatted

# ----------------- HANDLERS -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    context.user_data.clear()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING', (user.id, user.username))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("Papan menu cepat diaktifkan di bawah 👇", reply_markup=persistent_reply_keyboard())
    await update.message.reply_text(get_welcome_text(user.first_name), reply_markup=main_menu_keyboard(user.id), parse_mode='Markdown')

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data
    user = query.from_user

    if data == "menu_utama":
        context.user_data.clear()
        await query.edit_message_text(get_welcome_text(user.first_name), reply_markup=main_menu_keyboard(user.id), parse_mode='Markdown')

    elif data == "menu_satuan":
        allowed_pwds = get_current_allowed_passwords()
        if not allowed_pwds:
            await query.answer("❌ Mohon maaf, saat ini tidak ada sandi yang diaktifkan oleh admin.", show_alert=True)
            return

        context.user_data['mode'] = 'SATUAN'
        pwd_example = " / ".join(allowed_pwds)
        pesan = (
            "⏳ *MODE SETORAN SATUAN AKTIF*\n"
            "═══════════════════════\n"
            "Silakan ketik, kirim data Gmail kamu, atau kirim file `.txt` sekarang.\n\n"
            "📌 *Format:* `email@gmail.com:password`\n"
            f"💡 *Password Aktif:* {pwd_example}\n"
            f"💡 *Contoh:* `ridwan123@gmail.com:{allowed_pwds[0]}`\n\n"
            "_Sistem sedang menunggu inputan kamu..._"
        )
        await query.edit_message_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')

    elif data == "menu_bulking":
        allowed_pwds = get_current_allowed_passwords()
        if not allowed_pwds:
            await query.answer("❌ Mohon maaf, saat ini tidak ada sandi yang diaktifkan oleh admin.", show_alert=True)
            return

        keyboard = []
        for p in allowed_pwds:
            keyboard.append([InlineKeyboardButton(f"🔑 {p}", callback_data=f"bulkpwd_{p}")])
        keyboard.append([InlineKeyboardButton("« Batal / Kembali", callback_data="menu_utama")])
        
        pesan = (
            "📦 *SETORAN BULKING - PILIH PASSWORD*\n"
            "═══════════════════════\n"
            "Silakan pilih kata sandi aktif yang digunakan untuk kelompok akun yang ingin kamu setor:"
        )
        await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("bulkpwd_"):
        chosen_password = data.split('_')[1]
        allowed_pwds = get_current_allowed_passwords()
        
        if chosen_password not in allowed_pwds:
            await query.answer(f"❌ Sandi `{chosen_password}` sedang dinonaktifkan oleh Admin!", show_alert=True)
            return

        context.user_data['mode'] = 'BULKING_INPUT_EMAILS'
        context.user_data['bulk_password'] = chosen_password

        pesan = (
            f"📦 *SETORAN BULKING AKTIF*\n"
            f"═══════════════════════\n"
            f"🔑 *Password Dipilih:* `{chosen_password}`\n"
            f"⚠️ *Batas Maksimal:* {MAX_BULK_LIMIT} Akun sekali kirim\n\n"
            f"Sekarang, silakan *ketik, paste daftar list gmail*, atau *kirim file .txt* dengan format awal (`email@gmail.com` atau `email@gmail.com:password`) di bawah ini (satu per baris):\n\n"
            f"📌 *Contoh Format:*\n"
            f"`email1@gmail.com`\n"
            f"`email2@gmail.com`\n\n"
            f"_Sistem sedang menunggu list kamu..._"
        )
        await query.edit_message_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')

    elif data == "menu_saldo":
        context.user_data.clear()
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM deposits WHERE user_id = %s AND status = 'APPROVED'", (user.id,))
        app_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM deposits WHERE user_id = %s AND status = 'PROCESSING'", (user.id,))
        proc_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM deposits WHERE user_id = %s AND status = 'PENDING'", (user.id,))
        pen_count = cursor.fetchone()[0]

        cursor.execute('SELECT balance FROM users WHERE user_id = %s', (user.id,))
        res = cursor.fetchone()
        balance_ready = res[0] if res else 0

        cursor.close()
        conn.close()

        total_pending_all = proc_count + pen_count

        pesan = (
            f"📊 *INFORMASI AKUN & SALDO*\n"
            f"═══════════════════════\n"
            f"💵 *Saldo Siap Dicairkan:* Rp {balance_ready:,}\n"
            f"⏳ *Estimasi Saldo Tertahan:* Rp {total_pending_all * HARGA_PER_GMAIL:,}\n"
            f"═══════════════════════\n"
            f"✅ *Gmail Disetujui (Approved):* {app_count} Akun\n"
            f"🔄 *Gmail Diproses (Processing):* {proc_count} Akun\n"
            f"⏳ *Gmail Menunggu Rekap (Pending):* {pen_count} Akun\n"
            f"═══════════════════════"
        )
        await query.edit_message_text(pesan, reply_markup=back_keyboard(), parse_mode='Markdown')

    elif data == "menu_riwayat":
        context.user_data.clear()
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT gmail, status, created_at FROM deposits WHERE user_id = %s ORDER BY id DESC LIMIT 15', (user.id,))
        items = cursor.fetchall()
        cursor.close()
        conn.close()

        if not items:
            pesan = "📜 *DAFTAR SETORAN GMAIL*\n═══════════════════════\nBelum ada riwayat setoran."
        else:
            pesan = "📜 *DAFTAR SETORAN GMAIL (15 Terakhir)*\n═══════════════════════\n"
            for g_mail, st, dt in items:
                status_icon = "⏳ PENDING"
                if st == "PROCESSING":
                    status_icon = "🔄 PROCESSING (Sudah Rekap)"
                elif st == "APPROVED":
                    status_icon = "✅ APPROVED (Saldo Masuk)"
                elif st == "REJECTED":
                    status_icon = "❌ REJECTED"
                
                waktu = dt if dt else get_wib_time()
                pesan += f"📧 `{g_mail}`\n└ Status: *{status_icon}*\n└ Waktu: `{waktu}`\n\n"

        await query.edit_message_text(pesan, reply_markup=back_keyboard(), parse_mode='Markdown')

    elif data == "menu_riwayat_wd":
        context.user_data.clear()
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT id, nominal, metode, rekening, atas_nama, status, created_at FROM withdrawals WHERE user_id = %s ORDER BY id DESC LIMIT 10', (user.id,))
        wd_items = cursor.fetchall()
        cursor.close()
        conn.close()

        if not wd_items:
            pesan = "🧾 *RIWAYAT PENARIKAN DANA*\n═══════════════════════\nBelum ada riwayat penarikan dana."
        else:
            pesan = "🧾 *RIWAYAT PENARIKAN DANA (10 Terakhir)*\n═══════════════════════\n"
            for w_id, nom, met, rek, an, st, dt in wd_items:
                status_icon = "⌛ PENDING"
                if st == "APPROVED":
                    status_icon = "✅ BERHASIL"
                elif st == "REJECTED":
                    status_icon = "❌ DITOLAK (REFUND)"

                waktu = dt if dt else "-"
                pesan += (
                    f"🆔 *ID WD:* `#WD{w_id}`\n"
                    f"💵 *Nominal:* Rp {nom:,}\n"
                    f"🏦 *Metode:* {met} (`{rek}`)\n"
                    f"👤 *A/N:* `{an}`\n"
                    f"📌 *Status:* *{status_icon}*\n"
                    f"🕒 *Waktu:* `{waktu}`\n"
                    f"───────────────────────\n"
                )

        await query.edit_message_text(pesan, reply_markup=back_keyboard(), parse_mode='Markdown')

    elif data == "menu_tarik":
        context.user_data.clear()
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT balance FROM users WHERE user_id = %s', (user.id,))
        res = cursor.fetchone()
        balance = res[0] if res else 0
        cursor.close()
        conn.close()

        if balance < HARGA_PER_GMAIL:
            pesan = (
                f"💸 *PENARIKAN DANA*\n"
                f"═══════════════════════\n"
                f"💰 *Saldo Dapat Dicairkan:* Rp {balance:,}\n\n"
                f"⚠️ *Minimal penarikan dana adalah Rp {HARGA_PER_GMAIL:,} (Harga 1 Akun).* Saldo kamu belum mencukupi."
            )
            await query.edit_message_text(pesan, reply_markup=back_keyboard(), parse_mode='Markdown')
            return

        nominals = [4000, 8000, 12000, 20000, 50000, 100000]
        keyboard = []
        for n in nominals:
            if balance >= n:
                keyboard.append([InlineKeyboardButton(f"💵 Rp {n:,}", callback_data=f"wdnom_{n}")])
        keyboard.append([InlineKeyboardButton(f"🔥 Semua Saldo (Rp {balance:,})", callback_data=f"wdnom_{balance}")])
        keyboard.append([InlineKeyboardButton("« Kembali ke Menu Utama", callback_data="menu_utama")])

        pesan = (
            f"💸 *PENARIKAN DANA INTERAKTIF*\n"
            f"═══════════════════════\n"
            f"💰 *Saldo Dapat Dicairkan:* Rp {balance:,}\n\n"
            f"Pilih nominal pencairan yang kamu inginkan di bawah ini:"
        )
        await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("wdnom_"):
        nominal = int(data.split('_')[1])
        context.user_data['wd_nominal'] = nominal

        methods = ["DANA", "OVO", "GoPay", "ShopeePay", "BCA", "BRI", "Mandiri", "BNI"]
        keyboard = []
        for i in range(0, len(methods), 2):
            row = [InlineKeyboardButton(methods[i], callback_data=f"wdmet_{methods[i]}")]
            if i + 1 < len(methods):
                row.append(InlineKeyboardButton(methods[i+1], callback_data=f"wdmet_{methods[i+1]}"))
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("« Batal", callback_data="menu_utama")])

        pesan = (
            f"💳 *PILIH METODE PEMBAYARAN*\n"
            f"═══════════════════════\n"
            f"💵 *Nominal Penarikan:* Rp {nominal:,}\n\n"
            f"Silakan pilih metode pencairan dana:"
        )
        await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("wdmet_"):
        metode = data.split('_')[1]
        nominal = context.user_data.get('wd_nominal', 0)
        context.user_data['wd_metode'] = metode
        context.user_data['mode'] = 'WAITING_REK'

        pesan = (
            f"📝 *INPUT NOMOR REKENING / HP*\n"
            f"═══════════════════════\n"
            f"💵 *Nominal:* Rp {nominal:,}\n"
            f"🏦 *Metode:* {metode}\n\n"
            f"Silakan *ketik dan kirimkan* Nomor Rekening / E-Wallet kamu sekarang:"
        )
        await query.edit_message_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')

    elif data == "admin_panel":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        await render_admin_panel(query)

    elif data == "admin_edit_welcome_ask":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        context.user_data['mode'] = 'WAITING_EDIT_WELCOME'
        current_text = get_setting('welcome_text', DEFAULT_WELCOME_TEXT)

        pesan = (
            f"📝 *EDIT DESKRIPSI WELCOME / START*\n"
            f"═══════════════════════\n"
            f"Kirimkan teks deskripsi baru yang akan tampil setiap kali user menekan *Start / Refresh*.\n\n"
            f"📌 *Variabel Otomatis yang Bisa Digunakan:*\n"
            f"• `{{first_name}}` : Nama pertama user Telegram\n"
            f"• `{{pwd_str}}` : Daftar password aktif otomatis\n\n"
            f"📄 *Teks Saat Ini:*\n```\n{current_text}\n
