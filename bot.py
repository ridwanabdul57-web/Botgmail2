import io
import os
import re
import math
import unicodedata
from datetime import datetime, timezone, timedelta
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

BOT_TOKEN = os.getenv('BOT_TOKEN', '8966364905:AAEJKwW7MFa7rV0oI53gtxKUZEiuTHp0_5M')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', 8359903974))
CS_USERNAME = 'bossgmailbotcs'
MAX_BULK_LIMIT = 50
ITEMS_PER_PAGE_USERS = 5   # Jumlah user per slide halaman admin
ITEMS_PER_PAGE_DEPS = 10   # Jumlah akun per slide halaman verifikasi user

DEFAULT_WELCOME_TEXT = (
    "✨ *SELAMAT DATANG DI BOT SETORAN GMAIL V30* ✨\n"
    "Halo *{first_name}*! Silakan baca informasi & aturan setoran di bawah ini:\n\n"
    "💵 *INFORMASI RATE & PROSES*\n"
    "• *Estimasi Pengecekan:* 24 - 48 Jam Kerja\n"
    "• *Batas Bulking:* Maksimal 50 akun / setor\n\n"
    "🔑 *ATURAN KATA SANDI & HARGA AKTIF*\n"
    "{pwd_str}\n\n"
    "⚠️ *SYARAT & KETENTUAN WAJIB*\n"
    "1. *Dilarang Double-Sell:* Dilarang keras menyetor Gmail duplikat yang sudah pernah terdaftar di bot.\n"
    "2. *Nomor HP Pemulihan:* Wajib dikosongkan / jangan diverifikasi.\n"
    "3. *Kondisi Akun:* Akun langsung menampilkan opsi sandi (Good), bukan captcha.\n\n"
    "👇 *Pilih menu di bawah ini untuk memulai:*"
)

OVERLOAD_WELCOME_TEXT = (
    "⛔ *INFORMASI SETORAN DITUTUP SEMENTARA* ⛔\n"
    "Halo *{first_name}*!\n\n"
    "📢 *Mohon maaf, saat ini bot sedang tidak menerima setoran dikarenakan stock sedang OVERLOAD.*\n\n"
    "ℹ️ *Catatan Penting:*\n"
    "• Proses *Withdrawal (Penarikan Dana)* tetap berjalan normal.\n"
    "• Akun yang *sudah disetorkan sebelumnya* akan tetap diproses dan di-approve oleh admin.\n\n"
    "Silakan cek berkala menu ini untuk melihat update dibukanya kembali setoran."
)

def get_wib_time():
    wib_timezone = timezone(timedelta(hours=7))
    return datetime.now(wib_timezone).strftime("%d-%m-%Y %H:%M:%S WIB")

DEFAULT_MASTER_PASSWORDS = {
    'fineirga': ('ACTIVE', 4000),
    'sgsg1122': ('ACTIVE', 4000),
    'prabujaya': ('ACTIVE', 4000),
    'selaras9': ('ACTIVE', 4000)
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
            created_at TEXT,
            price BIGINT DEFAULT 4000
        )
    ''')
    
    try:
        cursor.execute("ALTER TABLE deposits ADD COLUMN price BIGINT DEFAULT 4000")
    except Exception:
        conn.rollback()
        cursor = conn.cursor()

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

    for pwd, (default_status, default_price) in DEFAULT_MASTER_PASSWORDS.items():
        setting_key_st = f"pwd_status_{pwd}"
        setting_key_pr = f"pwd_price_{pwd}"
        cursor.execute('INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING', (setting_key_st, default_status))
        cursor.execute('INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING', (setting_key_pr, str(default_price)))

    cursor.execute('INSERT INTO bot_settings (key, value) VALUES (\'welcome_text\', %s) ON CONFLICT (key) DO NOTHING', (DEFAULT_WELCOME_TEXT,))

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dep_user ON deposits(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wd_user ON withdrawals(user_id)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_dep_lower_gmail ON deposits(TRIM(LOWER(gmail)))")
        
    conn.commit()
    cursor.close()
    conn.close()

def delete_rejected_deposits():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM deposits WHERE status = 'REJECTED'")
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error deleting rejected deposits: {e}")

def get_all_passwords_info():
    pass_info = {}
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM bot_settings WHERE key LIKE 'pwd_%'")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        db_data = {r[0]: r[1] for r in rows}
        for pwd, (def_st, def_pr) in DEFAULT_MASTER_PASSWORDS.items():
            st = db_data.get(f"pwd_status_{pwd}", def_st)
            pr = int(db_data.get(f"pwd_price_{pwd}", str(def_pr)))
            pass_info[pwd] = {'status': st, 'price': pr}
    except Exception:
        for pwd, (def_st, def_pr) in DEFAULT_MASTER_PASSWORDS.items():
            pass_info[pwd] = {'status': def_st, 'price': def_pr}
    return pass_info

def set_password_status(password: str, status: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (f"pwd_status_{password}", status))
    conn.commit()
    cursor.close()
    conn.close()

def set_password_price(password: str, price: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (f"pwd_price_{password}", str(price)))
    conn.commit()
    cursor.close()
    conn.close()

def get_active_passwords():
    info = get_all_passwords_info()
    return {pwd: data['price'] for pwd, data in info.items() if data['status'] == 'ACTIVE'}

def get_welcome_text_db():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_settings WHERE key = 'welcome_text'")
        res = cursor.fetchone()
        cursor.close()
        conn.close()
        if res and res[0]:
            return res[0]
    except Exception:
        pass
    return DEFAULT_WELCOME_TEXT

def set_welcome_text_db(text: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO bot_settings (key, value) VALUES ('welcome_text', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (text,))
    conn.commit()
    cursor.close()
    conn.close()

def generate_user_txt_rekap(target_uid, username_txt):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT gmail, password, status, created_at, price FROM deposits WHERE user_id = %s ORDER BY id ASC", (target_uid,))
    user_all_deposits = cursor.fetchall()
    cursor.close()
    conn.close()

    pending_list = []
    proc_list = []
    approved_list = []

    for g, p, st, dt, pr in user_all_deposits:
        jam_str = dt.split()[1] if dt and len(dt.split()) > 1 else "00:00:00"
        pr_val = pr if pr else 4000
        formatted_item = f"{g}:{p} (Rp {pr_val:,}) | {jam_str} WIB"
        if st == 'PENDING':
            pending_list.append(formatted_item)
        elif st == 'PROCESSING':
            proc_list.append(formatted_item)
        elif st == 'APPROVED':
            approved_list.append(formatted_item)

    tgl_hari_ini = datetime.now(timezone(timedelta(hours=7))).strftime('%d-%m-%Y')

    txt_lines = [
        f"📅 TANGGAL SETOR: {tgl_hari_ini}",
        f"👤 USER: {username_txt} (ID: {target_uid})",
        f"📦 TOTAL AKUN SETORAN USER: {len(user_all_deposits)} Akun",
        "--------------------------------------------------",
        f"Gmail Pending (Belum Rekap) ({len(pending_list)}) :"
    ]
    txt_lines.extend(pending_list if pending_list else ["-"])

    txt_lines.append("")
    txt_lines.append(f"Gmail Processing (Sudah Rekap) ({len(proc_list)}) :")
    txt_lines.extend(proc_list if proc_list else ["-"])

    txt_lines.append("")
    txt_lines.append(f"Gmail Approved (Saldo Masuk) ({len(approved_list)}) :")
    txt_lines.extend(approved_list if approved_list else ["-"])

    txt_lines.append("--------------------------------------------------")
    txt_lines.append(f"📊 TOTAL KESELURUHAN SETORAN USER DI DATABASE: {len(user_all_deposits)} Akun")

    txt_content = "\n".join(txt_lines)
    txt_file = io.BytesIO(txt_content.encode('utf-8'))
    timestamp_file = datetime.now(timezone(timedelta(hours=7))).strftime("%Y%m%d_%H%M%S")
    filename = f"setoran_{target_uid}_{timestamp_file}.txt"
    return txt_file, filename

# ----------------- KEYBOARD MENUS -----------------
def persistent_reply_keyboard():
    keyboard = [
        [KeyboardButton("🔄 Refresh / Start"), KeyboardButton("📜 Daftar Setoran Saya")],
        [KeyboardButton("🚫 Batal Setoran Pending"), KeyboardButton("💰 Cek Saldo")],
        [KeyboardButton("💬 Hubungi CS")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def main_menu_keyboard(user_id):
    active_pwds = get_active_passwords()
    
    if not active_pwds:
        keyboard = [
            [
                InlineKeyboardButton("💰 Cek Saldo", callback_data="menu_saldo"),
                InlineKeyboardButton("💸 Penarikan Dana", callback_data="menu_tarik")
            ],
            [
                InlineKeyboardButton("📜 Riwayat Setoran", callback_data="menu_riwayat"),
                InlineKeyboardButton("🧾 Riwayat Withdraw", callback_data="menu_riwayat_wd")
            ],
            [
                InlineKeyboardButton("🚫 Batal Setoran Pending", callback_data="menu_batal_pending")
            ],
            [
                InlineKeyboardButton("💬 Hubungi CS / Admin", url=f"https://t.me/{CS_USERNAME}")
            ]
        ]
    else:
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
                InlineKeyboardButton("🚫 Batal Setoran Pending", callback_data="menu_batal_pending")
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
    active_pwds = get_active_passwords()
    if not active_pwds:
        return OVERLOAD_WELCOME_TEXT.replace("{first_name}", str(first_name))

    pwd_lines = [f"• `{pwd}` ➔ Rp {price:,}" for pwd, price in active_pwds.items()]
    pwd_str = "\n".join(pwd_lines)

    template = get_welcome_text_db()
    formatted_text = template.replace("{first_name}", str(first_name)).replace("{pwd_str}", pwd_str)
    return formatted_text

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
        active_pwds = get_active_passwords()
        if not active_pwds:
            await query.answer("❌ Mohon maaf, setoran sedang ditutup (Overload).", show_alert=True)
            return

        context.user_data['mode'] = 'SATUAN'
        pwd_info_text = "\n".join([f"• `{p}` (Harga: Rp {pr:,})" for p, pr in active_pwds.items()])
        sample_pwd = list(active_pwds.keys())[0]

        pesan = (
            "⏳ *MODE SETORAN SATUAN AKTIF*\n"
            "═══════════════════════\n"
            "Silakan ketik, kirim data Gmail kamu, atau kirim file `.txt` sekarang.\n\n"
            "📌 *Format:* `email@gmail.com:password`\n\n"
            f"💡 *Password & Harga Aktif Hari Ini:*\n{pwd_info_text}\n\n"
            f"💡 *Contoh:* `ridwan123@gmail.com:{sample_pwd}`\n\n"
            "_Sistem sedang menunggu inputan kamu..._"
        )
        await query.edit_message_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')

    elif data == "menu_bulking":
        active_pwds = get_active_passwords()
        if not active_pwds:
            await query.answer("❌ Mohon maaf, setoran sedang ditutup (Overload).", show_alert=True)
            return

        keyboard = []
        for p, pr in active_pwds.items():
            keyboard.append([InlineKeyboardButton(f"🔑 {p} (Rp {pr:,})", callback_data=f"bulkpwd_{p}")])
        keyboard.append([InlineKeyboardButton("« Batal / Kembali", callback_data="menu_utama")])
        
        pesan = (
            "📦 *SETORAN BULKING - PILIH PASSWORD*\n"
            "═══════════════════════\n"
            "Silakan pilih kata sandi aktif yang digunakan untuk kelompok akun yang ingin kamu setor:"
        )
        await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("bulkpwd_"):
        chosen_password = data.split('_')[1]
        active_pwds = get_active_passwords()
        
        if chosen_password not in active_pwds:
            await query.answer(f"❌ Sandi `{chosen_password}` sedang dinonaktifkan oleh Admin!", show_alert=True)
            return

        pwd_price = active_pwds[chosen_password]
        context.user_data['mode'] = 'BULKING_INPUT_EMAILS'
        context.user_data['bulk_password'] = chosen_password
        context.user_data['bulk_price'] = pwd_price

        pesan = (
            f"📦 *SETORAN BULKING AKTIF*\n"
            f"═══════════════════════\n"
            f"🔑 *Password Dipilih:* `{chosen_password}`\n"
            f"💵 *Harga Per Akun:* Rp {pwd_price:,}\n"
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

        cursor.execute("SELECT COALESCE(SUM(price), 0) FROM deposits WHERE user_id = %s AND status IN ('PENDING', 'PROCESSING')", (user.id,))
        estimasi_pending = cursor.fetchone()[0]

        cursor.execute('SELECT balance FROM users WHERE user_id = %s', (user.id,))
        res = cursor.fetchone()
        balance_ready = res[0] if res else 0

        cursor.close()
        conn.close()

        pesan = (
            f"📊 *INFORMASI AKUN & SALDO*\n"
            f"═══════════════════════\n"
            f"💵 *Saldo Siap Dicairkan:* Rp {balance_ready:,}\n"
            f"⏳ *Estimasi Saldo Tertahan:* Rp {estimasi_pending:,}\n"
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
        cursor.execute('SELECT gmail, status, created_at, price FROM deposits WHERE user_id = %s ORDER BY id DESC LIMIT 15', (user.id,))
        items = cursor.fetchall()
        cursor.close()
        conn.close()

        if not items:
            pesan = "📜 *DAFTAR SETORAN GMAIL*\n═══════════════════════\nBelum ada riwayat setoran."
        else:
            pesan = "📜 *DAFTAR SETORAN GMAIL (15 Terakhir)*\n═══════════════════════\n"
            for g_mail, st, dt, pr in items:
                status_icon = "⏳ PENDING"
                if st == "PROCESSING":
                    status_icon = "🔄 PROCESSING (Sudah Rekap)"
                elif st == "APPROVED":
                    status_icon = "✅ APPROVED (Saldo Masuk)"
                elif st == "REJECTED":
                    status_icon = "❌ REJECTED (Ditolak)"
                
                waktu = dt if dt else get_wib_time()
                harga_str = f"Rp {pr:,}" if pr else "Rp 4.000"
                pesan += f"📧 `{g_mail}`\n└ Rate: *{harga_str}*\n└ Status: *{status_icon}*\n└ Waktu: `{waktu}`\n\n"

        await query.edit_message_text(pesan, reply_markup=back_keyboard(), parse_mode='Markdown')

    elif data == "menu_batal_pending":
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, gmail, password, price FROM deposits WHERE user_id = %s AND status = 'PENDING' ORDER BY id DESC", (user.id,))
        pending_items = cursor.fetchall()
        cursor.close()
        conn.close()

        if not pending_items:
            await query.edit_message_text("🚫 *Tidak ada setoran berstatus PENDING yang dapat dibatalkan.*", reply_markup=back_keyboard(), parse_mode='Markdown')
            return

        pesan = (
            f"🚫 *BATALKAN SETORAN PENDING*\n"
            f"═══════════════════════\n"
            f"Pilih akun di bawah ini yang ingin Anda batalkan setorannya.\n"
            f"⚠️ *Perhatian:* Akun yang dibatalkan akan **langsung dihapus secara permanen dari database**.\n\n"
        )
        keyboard = []
        for dep_id, g_mail, p_ass, pr in pending_items[:15]:
            pr_val = pr if pr else 4000
            keyboard.append([InlineKeyboardButton(f"❌ Batal `{g_mail}` (Rp {pr_val:,})", callback_data=f"user_cancel_dep_{dep_id}")])
        
        keyboard.append([InlineKeyboardButton("🔥 Batalkan SEMUA Akun Pending", callback_data="user_cancel_all_pending")])
        keyboard.append([InlineKeyboardButton("« Kembali ke Menu Utama", callback_data="menu_utama")])
        await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("user_cancel_dep_"):
        dep_id = int(data.split('_')[3])
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM deposits WHERE id = %s AND user_id = %s AND status = 'PENDING' RETURNING gmail", (dep_id, user.id))
        deleted = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()

        if deleted:
            await query.answer(f"✅ Setoran `{deleted[0]}` telah dibatalkan & dihapus!", show_alert=True)
        else:
            await query.answer("⚠️ Gagal membatalkan setoran. Akun sudah diproses admin.", show_alert=True)

        await query.edit_message_text("Pembaruan daftar setoran pending...", reply_markup=back_keyboard())

    elif data == "user_cancel_all_pending":
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM deposits WHERE user_id = %s AND status = 'PENDING' RETURNING id", (user.id,))
        deleted_rows = cursor.fetchall()
        conn.commit()
        cursor.close()
        conn.close()

        count_del = len(deleted_rows)
        await query.answer(f"✅ Berhasil membatalkan {count_del} akun setoran pending!", show_alert=True)
        await query.edit_message_text(f"✅ *Sebanyak {count_del} akun setoran PENDING berhasil dibatalkan dan dihapus dari database.*", reply_markup=back_keyboard(), parse_mode='Markdown')

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

        if balance < 4000:
            pesan = (
                f"💸 *PENARIKAN DANA*\n"
                f"═══════════════════════\n"
                f"💰 *Saldo Dapat Dicairkan:* Rp {balance:,}\n\n"
                f"⚠️ *Minimal penarikan dana adalah Rp 4,000.* Saldo kamu belum mencukupi."
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

    elif data.startswith("admin_toggle_pwd_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        target_pwd = data.replace('admin_toggle_pwd_', '')
        all_info = get_all_passwords_info()
        
        if target_pwd in all_info:
            current_st = all_info[target_pwd]['status']
            new_st = 'INACTIVE' if current_st == 'ACTIVE' else 'ACTIVE'
            set_password_status(target_pwd, new_st)
            await query.answer(f"✅ Status `{target_pwd}` diubah menjadi {new_st}!", show_alert=True)

        await render_admin_panel(query)

    elif data.startswith("admin_set_price_ask_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        target_pwd = data.replace('admin_set_price_ask_', '')
        all_info = get_all_passwords_info()
        current_pr = all_info.get(target_pwd, {}).get('price', 4000)

        context.user_data['mode'] = 'WAITING_NEW_PWD_PRICE'
        context.user_data['target_pwd_for_price'] = target_pwd

        pesan = (
            f"✏️ *UBAH HARGA PASSWORD: `{target_pwd}`*\n"
            f"═══════════════════════\n"
            f"💵 *Harga Saat Ini:* Rp {current_pr:,}\n\n"
            f"Silakan ketik dan kirimkan nominal harga baru (contoh angka: `4500` / `5000`):"
        )
        await query.edit_message_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')

    elif data == "admin_edit_welcome_ask":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        context.user_data['mode'] = 'WAITING_WELCOME_TEXT'
        current_text = get_welcome_text_db()
        pesan = (
            "📝 *EDIT DESKRIPSI START / REFRESH*\n"
            "═══════════════════════\n"
            "Silakan kirimkan teks deskripsi baru untuk pesan /start atau refresh.\n\n"
            "💡 *Tips Tag Variabel Otomatis:*\n"
            "• Gunakan `{first_name}` untuk menampilkan nama user.\n"
            "• Gunakan `{pwd_str}` untuk menampilkan daftar password aktif & harganya.\n\n"
            "📄 *Deskripsi Saat Ini:*\n"
            f"```\n{current_text}\n```\n\n"
            "_Kirim pesan berisi deskripsi baru sekarang..._"
        )
        await query.edit_message_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')

    elif data == "admin_export_menu":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        keyboard = [
            [InlineKeyboardButton("📂 Rekap SEMUA Status Database", callback_data="admin_export_all_deposits")],
            [InlineKeyboardButton("🚫 Rekap Khusus REJECTED Realtime", callback_data="admin_export_rejected_txt")],
            [InlineKeyboardButton("✅ Rekap Khusus APPROVED Realtime", callback_data="admin_export_pwd_select_APPROVED")],
            [InlineKeyboardButton("⏳ Rekap Khusus PENDING (+ Nama User)", callback_data="admin_export_simple_PENDING")],
            [InlineKeyboardButton("🔄 Rekap Khusus PROCESSING (+ Nama User)", callback_data="admin_export_simple_PROCESSING")],
            [InlineKeyboardButton("« Kembali ke Panel Admin", callback_data="admin_panel")]
        ]
        pesan = (
            "📊 *MENU FITUR EKSPOR REKAP REALTIME (.TXT)*\n"
            "═══════════════════════\n"
            "Pilih jenis rekap data yang ingin Anda unduh dalam format .txt:"
        )
        await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == "admin_export_rejected_txt":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT d.user_id, u.username, d.gmail, d.password, d.created_at, d.price 
            FROM deposits d 
            LEFT JOIN users u ON d.user_id = u.user_id 
            WHERE d.status = 'REJECTED' 
            ORDER BY d.user_id DESC, d.id ASC
        ''')
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            await query.answer("⚠️ Tidak ada data Gmail REJECTED di database saat ini.", show_alert=True)
            return

        user_grouped = {}
        for uid, uname, gmail, pwd, dt, pr in rows:
            jam_str = dt.split()[1] if dt and len(dt.split()) > 1 else "00:00:00"
            u_tag = f"@{uname}" if uname else f"User_{uid}"

            if u_tag not in user_grouped:
                user_grouped[u_tag] = []
            
            user_grouped[u_tag].append(f"{gmail}:{pwd} | {jam_str} WIB")

        tgl_sekarang = datetime.now(timezone(timedelta(hours=7))).strftime('%d-%m-%Y')
        txt_lines = [
            f"🚫 DAFTAR REKAP TOTAL GMAIL REJECT REALTIME - {tgl_sekarang}",
            "==================================================",
            ""
        ]

        total_reject_all = len(rows)

        for u_tag, items in user_grouped.items():
            txt_lines.append(f"👤 USER: {u_tag}")
            txt_lines.append(f"📦 TOTAL GMAIL REJECT USER INI: {len(items)} Akun")
            txt_lines.append("--------------------------------------------------")
            txt_lines.extend(items)
            txt_lines.append("--------------------------------------------------\n")

        txt_lines.append("==================================================")
        txt_lines.append(f"📊 KESELURUHAN TOTAL GMAIL REJECT: {total_reject_all} Akun")
        txt_lines.append("==================================================")

        txt_content = "\n".join(txt_lines)
        txt_file = io.BytesIO(txt_content.encode('utf-8'))

        wib_now = datetime.now(timezone(timedelta(hours=7))).strftime('%Y%m%d_%H%M%S')
        filename = f"daftar_gmail_reject_realtime_{wib_now}.txt"

        await context.bot.send_document(
            chat_id=user.id,
            document=txt_file,
            filename=filename,
            caption=f"🚫 *REKAP REJECTED REALTIME (.TXT)*\n• Total Gmail Reject: `{total_reject_all}` Akun\n• Total User: `{len(user_grouped)}` User",
            parse_mode='Markdown'
        )
        await query.answer("✅ File rekap reject berhasil dikirim!", show_alert=False)

    elif data.startswith("admin_export_pwd_select_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        target_st = data.replace('admin_export_pwd_select_', '')
        keyboard = [
            [InlineKeyboardButton("🌐 Semua Kata Sandi", callback_data=f"do_exp_st_{target_st}_all")],
            [InlineKeyboardButton("🔑 fineirga", callback_data=f"do_exp_st_{target_st}_fineirga")],
            [InlineKeyboardButton("🔑 prabujaya", callback_data=f"do_exp_st_{target_st}_prabujaya")],
            [InlineKeyboardButton("🔑 sgsg", callback_data=f"do_exp_st_{target_st}_sgsg")],
            [InlineKeyboardButton("🔑 selaras 9", callback_data=f"do_exp_st_{target_st}_selaras9")],
            [InlineKeyboardButton("« Kembali", callback_data="admin_export_menu")]
        ]
        pesan = f"🔑 *PILIH FILTER PASSWORD UNTUK REKAP {target_st}*"
        await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("do_exp_st_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        parts = data.split('_')
        target_st = parts[3]
        target_pwd_filter = parts[4]

        conn = get_db()
        cursor = conn.cursor()

        query_sql = "SELECT d.gmail, d.password, u.username FROM deposits d LEFT JOIN users u ON d.user_id = u.user_id WHERE d.status = %s"
        params = [target_st]

        if target_pwd_filter != 'all':
            if target_pwd_filter == 'selaras9':
                query_sql += " AND (LOWER(d.password) LIKE '%selaras%' OR LOWER(d.password) LIKE '%selaras9%')"
            elif target_pwd_filter == 'sgsg':
                query_sql += " AND LOWER(d.password) LIKE '%sgsg%'"
            else:
                query_sql += " AND LOWER(d.password) = %s"
                params.append(target_pwd_filter.lower())

        cursor.execute(query_sql, tuple(params))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            await query.answer(f"⚠️ Tidak ada data {target_st} dengan filter kata sandi tersebut.", show_alert=True)
            return

        txt_lines = [f"{g}:{p}" for g, p, _ in rows]
        txt_content = "\n".join(txt_lines)
        txt_file = io.BytesIO(txt_content.encode('utf-8'))

        wib_now = datetime.now(timezone(timedelta(hours=7))).strftime('%Y%m%d_%H%M%S')
        filename = f"{target_st.lower()}_{target_pwd_filter}_{wib_now}.txt"

        await context.bot.send_document(
            chat_id=user.id,
            document=txt_file,
            filename=filename,
            caption=f"📄 *REKAP {target_st} REALTIME*\n• Filter Password: `{target_pwd_filter}`\n• Total Akun: `{len(rows)}` Akun",
            parse_mode='Markdown'
        )
        await query.answer("✅ File rekap berhasil dikirim!", show_alert=False)

    elif data.startswith("admin_export_simple_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        target_st = data.replace('admin_export_simple_', '')
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT d.gmail, d.password, u.username, d.user_id 
            FROM deposits d 
            LEFT JOIN users u ON d.user_id = u.user_id 
            WHERE d.status = %s
            ORDER BY d.id ASC
        ''', (target_st,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            await query.answer(f"⚠️ Tidak ada data setoran berstatus {target_st}.", show_alert=True)
            return

        txt_lines = []
        for gmail, pwd, uname, uid in rows:
            u_tag = f"@{uname}" if uname else f"User_{uid}"
            txt_lines.append(f"{gmail}:{pwd} | {u_tag}")

        txt_content = "\n".join(txt_lines)
        txt_file = io.BytesIO(txt_content.encode('utf-8'))

        wib_now = datetime.now(timezone(timedelta(hours=7))).strftime('%Y%m%d_%H%M%S')
        filename = f"daftar_{target_st.lower()}_user_{wib_now}.txt"

        await context.bot.send_document(
            chat_id=user.id,
            document=txt_file,
            filename=filename,
            caption=f"📋 *DAFTAR {target_st} REALTIME (+ NAMA USER)*\n• Total Akun: `{len(rows)}` Akun",
            parse_mode='Markdown'
        )
        await query.answer("✅ File berhasil dikirim!", show_alert=False)

    elif data == "admin_export_all_deposits":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT d.user_id, u.username, d.gmail, d.password, d.status, d.created_at, d.price
            FROM deposits d 
            LEFT JOIN users u ON d.user_id = u.user_id 
            ORDER BY d.user_id DESC, d.id ASC
        ''')
        all_deposits = cursor.fetchall()
        cursor.close()
        conn.close()

        if not all_deposits:
            await query.answer("⚠️ Belum ada riwayat setoran di database.", show_alert=True)
            return

        wib_now_date = datetime.now(timezone(timedelta(hours=7))).strftime('%Y%m%d_%H%M%S')
        user_grouped_data = {}
        total_semua_akun = len(all_deposits)

        for uid, uname, gmail, pwd, status, dt, pr in all_deposits:
            jam_str = dt.split()[1] if dt and len(dt.split()) > 1 else "00:00:00"
            pr_val = pr if pr else 4000
            
            user_key = (uid, uname)
            if user_key not in user_grouped_data:
                user_grouped_data[user_key] = {'PENDING': [], 'PROCESSING': [], 'APPROVED': [], 'REJECTED': []}
            
            if status in user_grouped_data[user_key]:
                user_grouped_data[user_key][status].append(f"{gmail}:{pwd} (Rp {pr_val:,}) | {jam_str} WIB")

        tgl_sekarang = datetime.now(timezone(timedelta(hours=7))).strftime('%d-%m-%Y')
        txt_lines = [f"📅 REKAP REALTIME DATABASE SETORAN - {tgl_sekarang}\n"]

        for (uid, uname), statuses_dict in user_grouped_data.items():
            u_tag = f"@{uname}" if uname else f"User_{uid}"
            tot_p = len(statuses_dict['PENDING'])
            tot_pr = len(statuses_dict['PROCESSING'])
            tot_a = len(statuses_dict['APPROVED'])
            tot_r = len(statuses_dict['REJECTED'])
            tot_user = tot_p + tot_pr + tot_a + tot_r

            txt_lines.append(f"👤 USER: {u_tag} (ID: {uid}) - Total Setor: {tot_user} Akun")
            
            txt_lines.append(f"  • Gmail Pending (Belum Rekap) ({tot_p}) :")
            txt_lines.extend([f"    {item}" for item in statuses_dict['PENDING']] if statuses_dict['PENDING'] else ["    -"])
            
            txt_lines.append(f"  • Gmail Processing (Sudah Rekap) ({tot_pr}) :")
            txt_lines.extend([f"    {item}" for item in statuses_dict['PROCESSING']] if statuses_dict['PROCESSING'] else ["    -"])

            txt_lines.append(f"  • Gmail Approved (Saldo Masuk) ({tot_a}) :")
            txt_lines.extend([f"    {item}" for item in statuses_dict['APPROVED']] if statuses_dict['APPROVED'] else ["    -"])

            txt_lines.append(f"  • Gmail Rejected (Ditolak) ({tot_r}) :")
            txt_lines.extend([f"    {item}" for item in statuses_dict['REJECTED']] if statuses_dict['REJECTED'] else ["    -"])
            
            txt_lines.append("--------------------------------------------------")

        txt_lines.append("\n==================================================")
        txt_lines.append(f"📊 KETERANGAN TOTAL AKUN KESELURUHAN DATABASE: {total_semua_akun} Akun")
        txt_lines.append("==================================================")

        txt_content = "\n".join(txt_lines)
        txt_file = io.BytesIO(txt_content.encode('utf-8'))
        filename = f"rekap_setoran_{wib_now_date}.txt"

        try:
            await context.bot.send_document(
                chat_id=user.id,
                document=txt_file,
                filename=filename,
                caption=f"📁 *REKAP SETORAN LENGKAP REAL-TIME*\n• Total Akun Terdaftar: `{total_semua_akun}` Akun",
                parse_mode='Markdown'
            )
            await query.answer("✅ File rekap berhasil dikirim!", show_alert=False)
        except Exception as e:
            await query.answer(f"❌ Gagal mengirim file: {e}", show_alert=True)

    elif data == "admin_setoran" or data.startswith("admin_setoran_page_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        page = 1
        if data.startswith("admin_setoran_page_"):
            page = int(data.split('_')[3])

        await render_admin_users_list(query, page)

    elif data == "admin_withdraw":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT w.id, w.user_id, u.username, w.nominal, w.metode, w.rekening, w.atas_nama, w.created_at 
            FROM withdrawals w 
            LEFT JOIN users u ON w.user_id = u.user_id 
            WHERE w.status = 'PENDING' 
            ORDER BY w.id DESC
        ''')
        wd_list = cursor.fetchall()
        cursor.close()
        conn.close()

        if not wd_list:
            await query.edit_message_text("✅ *Tidak ada permintaan withdraw pending.*", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali ke Panel Admin", callback_data="admin_panel")]]), parse_mode='Markdown')
            return

        pesan = f"💸 *DAFTAR PERMINTAAN WITHDRAW PENDING*\n═══════════════════════\n\n"
        keyboard = []
        for wd_id, uid, uname, nom, met, rek, an, dt in wd_list:
            u_txt = f"@{uname}" if uname else f"ID: `{uid}`"
            pesan += (
                f"🆔 *ID WD:* `#WD{wd_id}`\n"
                f"👤 *User:* {u_txt}\n"
                f"💵 *Nominal:* Rp {nom:,}\n"
                f"🏦 *Metode:* {met}\n"
                f"📌 *No Rek/HP:* `{rek}`\n"
                f"👤 *A/N:* `{an}`\n"
                f"🕒 *Waktu:* `{dt}`\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(f"✅ Approve #WD{wd_id}", callback_data=f"accwd_{wd_id}"),
                InlineKeyboardButton(f"❌ Reject #WD{wd_id}", callback_data=f"rejwd_{wd_id}")
            ])

        keyboard.append([InlineKeyboardButton("« Kembali ke Panel Admin", callback_data="admin_panel")])
        await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == "global_paste_process_ask":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        context.user_data['mode'] = 'WAITING_GLOBAL_PASTE_PROCESS'
        pesan = (
            f"🔄 *AUTO REKAP (PROCESSING) MASSAL VIA PASTE LIST*\n"
            f"═══════════════════════\n"
            f"Ubah status dari *PENDING* ke *PROCESSING* (Sistem/Admin sudah rekap).\n\n"
            f"Silakan *ketik, paste daftar email, atau kirim file .txt* yang sudah direkap (satu email per baris).\n"
            f"Format fleksibel: `email@gmail.com` atau `email@gmail.com:password`."
        )
        await query.edit_message_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')

    elif data == "global_paste_approve_ask":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        context.user_data['mode'] = 'WAITING_GLOBAL_PASTE_APPROVE'
        pesan = (
            f"✅ *AUTO APPROVE MASSAL VIA PASTE LIST (GLOBAL)*\n"
            f"═══════════════════════\n"
            f"Fitur ini berlaku untuk *SETORAN PENDING/PROCESSING ALL USER*.\n\n"
            f"Silakan *ketik, paste daftar email, atau kirim file .txt* yang ingin di-approve (satu email per baris).\n"
            f"Format fleksibel: `email@gmail.com` atau `email@gmail.com:password`."
        )
        await query.edit_message_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')

    elif data == "global_paste_reject_ask":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        keyboard = []
        for idx, reason in enumerate(REJECT_REASONS):
            keyboard.append([InlineKeyboardButton(f"❌ {reason}", callback_data=f"global_pasterej_do_{idx}")])
        keyboard.append([InlineKeyboardButton("« Kembali ke Panel Admin", callback_data="admin_panel")])

        pesan = (
            f"❌ *AUTO REJECT MASSAL VIA PASTE LIST (GLOBAL)*\n"
            f"═══════════════════════\n"
            f"Fitur ini berlaku untuk *SETORAN PENDING/PROCESSING ALL USER*.\n\n"
            f"Pilih alasan penolakan terlebih dahulu sebelum memasukkan daftar email:"
        )
        await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("global_pasterej_do_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        reason_idx = int(data.split('_')[3])
        context.user_data['global_reject_reason_idx'] = reason_idx
        context.user_data['mode'] = 'WAITING_GLOBAL_PASTE_REJECT'
        chosen_reason = REJECT_REASONS[reason_idx] if reason_idx < len(REJECT_REASONS) else "Ditolak Admin"

        pesan = (
            f"❌ *AUTO REJECT MASSAL - KIRIM LIST EMAIL*\n"
            f"═══════════════════════\n"
            f"📌 *Alasan Dipilih:* {chosen_reason}\n\n"
            f"Sekarang, silakan *ketik, paste daftar email, atau kirim file .txt* yang ingin di-reject (satu email per baris)."
        )
        await query.edit_message_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')

    elif data == "admin_broadcast":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        context.user_data['mode'] = 'WAITING_BROADCAST_TEXT'
        pesan = (
            f"📢 *FITUR PENGUMUMAN MASSAL (BROADCAST)*\n"
            f"═══════════════════════\n"
            f"Pesan yang Anda kirim setelah ini akan disebarkan ke *SEMUA USER* yang terdaftar di database bot.\n\n"
            f"Silakan ketik dan kirimkan teks pengumuman Anda sekarang:"
        )
        await query.edit_message_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')

    elif data.startswith("admuser_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        parts = data.split('_')
        target_uid = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 1

        context.user_data['sel_target_uid'] = target_uid
        if 'selected_deps' not in context.user_data:
            context.user_data['selected_deps'] = []

        await render_admin_user_deposits(query, target_uid, context, page)

    elif data.startswith("togdep_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        parts = data.split('_')
        dep_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 1

        target_uid = context.user_data.get('sel_target_uid')
        if not target_uid:
            return

        selected_deps = context.user_data.setdefault('selected_deps', [])
        if dep_id in selected_deps:
            selected_deps.remove(dep_id)
        else:
            selected_deps.append(dep_id)

        await render_admin_user_deposits(query, target_uid, context, page)

    elif data.startswith("togall_deps_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        page = int(data.split('_')[2])
        target_uid = context.user_data.get('sel_target_uid')
        if not target_uid:
            return

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM deposits WHERE user_id = %s AND status IN ('PENDING', 'PROCESSING')", (target_uid,))
        all_items = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()

        selected_deps = context.user_data.setdefault('selected_deps', [])
        if len(selected_deps) == len(all_items):
            context.user_data['selected_deps'] = []
        else:
            context.user_data['selected_deps'] = all_items

        await render_admin_user_deposits(query, target_uid, context, page)

    elif data == "do_sel_process":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        target_uid = context.user_data.get('sel_target_uid')
        selected_deps = context.user_data.get('selected_deps', [])

        if not selected_deps:
            await query.answer("⚠️ Belum ada akun yang dicentang!", show_alert=True)
            return

        conn = get_db()
        cursor = conn.cursor()
        
        placeholders = ','.join(['%s'] * len(selected_deps))
        cursor.execute(f"UPDATE deposits SET status = 'PROCESSING' WHERE id IN ({placeholders})", tuple(selected_deps))
        conn.commit()
        
        cursor.execute("SELECT username FROM users WHERE user_id = %s", (target_uid,))
        u_res = cursor.fetchone()
        uname = u_res[0] if u_res else None
        cursor.close()
        conn.close()

        count = len(selected_deps)
        context.user_data['selected_deps'] = []

        u_text = f"@{uname}" if uname else f"User_{target_uid}"
        txt_file, filename = generate_user_txt_rekap(target_uid, u_text)
        
        await context.bot.send_document(
            chat_id=user.id,
            document=txt_file,
            filename=filename,
            caption=f"📄 *REKAP UPDATE REALTIME (PROCESSING)*\n• User: {u_text}\n• Total akun diupdate: `{count}` akun."
        )

        await query.edit_message_text(
            f"🔄 *REKAP (PROCESSING) TERPILIH BERHASIL!*\n\nTotal `{count}` akun dari User `{target_uid}` diubah statusnya menjadi PROCESSING (Sudah Rekap).\nFile rekap .txt realtime telah dikirimkan ke chat.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali ke Daftar User", callback_data="admin_setoran")]]),
            parse_mode='Markdown'
        )

        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=f"🔄 *SETORAN TELAH DIREKAP!*\n\nSebanyak *{count} akun Gmail* kamu telah direkap oleh admin dan sekarang berada dalam tahap pengecekan (Processing).",
                parse_mode='Markdown'
            )
        except Exception:
            pass

    elif data == "do_sel_approve":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        target_uid = context.user_data.get('sel_target_uid')
        selected_deps = context.user_data.get('selected_deps', [])

        if not selected_deps:
            await query.answer("⚠️ Belum ada akun yang dicentang!", show_alert=True)
            return

        conn = get_db()
        cursor = conn.cursor()
        
        placeholders = ','.join(['%s'] * len(selected_deps))
        cursor.execute(f"SELECT COALESCE(SUM(price), 0) FROM deposits WHERE id IN ({placeholders})", tuple(selected_deps))
        total_added = cursor.fetchone()[0]

        cursor.execute(f"UPDATE deposits SET status = 'APPROVED' WHERE id IN ({placeholders})", tuple(selected_deps))
        
        count = len(selected_deps)
        cursor.execute('UPDATE users SET balance = balance + %s WHERE user_id = %s', (total_added, target_uid))
        
        cursor.execute("SELECT username FROM users WHERE user_id = %s", (target_uid,))
        u_res = cursor.fetchone()
        uname = u_res[0] if u_res else None
        
        conn.commit()
        cursor.close()
        conn.close()

        context.user_data['selected_deps'] = []

        u_text = f"@{uname}" if uname else f"User_{target_uid}"
        txt_file, filename = generate_user_txt_rekap(target_uid, u_text)
        
        await context.bot.send_document(
            chat_id=user.id,
            document=txt_file,
            filename=filename,
            caption=f"📄 *REKAP UPDATE REALTIME (APPROVED)*\n• User: {u_text}\n• Total akun disetujui: `{count}` akun."
        )

        await query.edit_message_text(
            f"✅ *APPROVE TERPILIH BERHASIL!*\n\nTotal `{count}` akun dari User `{target_uid}` telah disetujui.\nSaldo +Rp {total_added:,} telah dikreditkan.\nFile rekap .txt realtime telah dikirimkan ke chat.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali ke Daftar User", callback_data="admin_setoran")]]),
            parse_mode='Markdown'
        )

        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=f"🎉 *SETORAN DI-APPROVE!*\n\nSebanyak *{count} akun Gmail* kamu telah diverifikasi dan disetujui oleh admin.\n💰 *+Rp {total_added:,}* telah masuk ke saldo cair kamu!",
                parse_mode='Markdown'
            )
        except Exception:
            pass

    elif data == "do_sel_reject_ask":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        selected_deps = context.user_data.get('selected_deps', [])
        if not selected_deps:
            await query.answer("⚠️ Belum ada akun yang dicentang!", show_alert=True)
            return

        keyboard = []
        for idx, reason in enumerate(REJECT_REASONS):
            keyboard.append([InlineKeyboardButton(f"❌ {reason}", callback_data=f"do_sel_rej_{idx}")])
        keyboard.append([InlineKeyboardButton("« Batal", callback_data=f"admuser_{context.user_data.get('sel_target_uid')}_1")])

        pesan = f"⚠️ *PILIH ALASAN PENOLAKAN UNTUK {len(selected_deps)} AKUN TERPILIH*"
        await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("do_sel_rej_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        reason_idx = int(data.split('_')[3])
        chosen_reason = REJECT_REASONS[reason_idx] if reason_idx < len(REJECT_REASONS) else "Ditolak Admin"

        target_uid = context.user_data.get('sel_target_uid')
        selected_deps = context.user_data.get('selected_deps', [])

        if not selected_deps:
            await query.edit_message_text("⚠️ Tidak ada akun terpilih.", reply_markup=back_keyboard())
            return

        conn = get_db()
        cursor = conn.cursor()
        
        placeholders = ','.join(['%s'] * len(selected_deps))
        cursor.execute(f'SELECT gmail FROM deposits WHERE id IN ({placeholders})', tuple(selected_deps))
        rejected_emails = [row[0] for row in cursor.fetchall()]

        cursor.execute(f"UPDATE deposits SET status = 'REJECTED' WHERE id IN ({placeholders})", tuple(selected_deps))
        
        cursor.execute("SELECT username FROM users WHERE user_id = %s", (target_uid,))
        u_res = cursor.fetchone()
        uname = u_res[0] if u_res else None

        conn.commit()
        cursor.close()
        conn.close()

        count = len(selected_deps)
        context.user_data['selected_deps'] = []

        u_text = f"@{uname}" if uname else f"User_{target_uid}"
        txt_file, filename = generate_user_txt_rekap(target_uid, u_text)

        await context.bot.send_document(
            chat_id=user.id,
            document=txt_file,
            filename=filename,
            caption=f"📄 *REKAP UPDATE REALTIME (REJECTED)*\n• User: {u_text}\n• Total akun ditolak: `{count}` akun."
        )

        await query.edit_message_text(
            f"❌ *REJECT TERPILIH BERHASIL!*\n\nTotal `{count}` akun dari User `{target_uid}` telah ditolak (REJECTED).\n📌 *Alasan:* {chosen_reason}\nFile rekap .txt realtime telah dikirimkan ke chat.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali ke Daftar User", callback_data="admin_setoran")]]),
            parse_mode='Markdown'
        )

        try:
            email_list_str = "\n".join([f"• `{e}`" for e in rejected_emails])
            await context.bot.send_message(
                chat_id=target_uid,
                text=(
                    f"❌ *PEMBERITAHUAN PENOLAKAN GMAIL*\n"
                    f"═══════════════════════\n"
                    f"⚠️ Sejumlah {count} akun setoran Anda ditolak:\n"
                    f"{email_list_str}\n\n"
                    f"📌 *Alasan Ditolak:* {chosen_reason}\n"
                    f"═══════════════════════\n"
                    f"Silakan periksa kembali akun Anda."
                ),
                parse_mode='Markdown'
            )
        except Exception:
            pass

    elif data.startswith("accwd_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        wd_id = int(data.split('_')[1])
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, nominal, metode, rekening, atas_nama, status FROM withdrawals WHERE id = %s', (wd_id,))
        res = cursor.fetchone()

        if not res or res[5] != 'PENDING':
            await query.edit_message_text("⚠️ Penarikan ini sudah diproses.", reply_markup=back_keyboard())
            cursor.close()
            conn.close()
            return

        target_uid, nominal, metode, rekening, atas_nama, _ = res
        cursor.execute("UPDATE withdrawals SET status = 'APPROVED' WHERE id = %s", (wd_id,))
        conn.commit()
        cursor.close()
        conn.close()

        await query.edit_message_text(
            f"✅ *PENARIKAN #WD{wd_id} APPROVED!*\n\nNominal: Rp {nominal:,}\nMetode: {metode}\nRek/HP: `{rekening}`\nA/N: `{atas_nama}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali ke Panel WD", callback_data="admin_withdraw")]]),
            parse_mode='Markdown'
        )

        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=(
                    f"🎉 *PENARIKAN DANA BERHASIL!*\n"
                    f"═══════════════════════\n"
                    f"🆔 *ID WD:* `#WD{wd_id}`\n"
                    f"💵 *Nominal:* Rp {nominal:,}\n"
                    f"🏦 *Metode:* {metode}\n"
                    f"📌 *No Rek/HP:* `{rekening}`\n"
                    f"👤 *A/N:* `{atas_nama}`\n"
                    f"═══════════════════════\n"
                    f"Dana telah berhasil ditransfer ke rekening/e-wallet kamu. Terima kasih!"
                ),
                parse_mode='Markdown'
            )
        except Exception:
            pass

    elif data.startswith("rejwd_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        wd_id = int(data.split('_')[1])
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, nominal, metode, rekening, atas_nama, status FROM withdrawals WHERE id = %s', (wd_id,))
        res = cursor.fetchone()

        if not res or res[5] != 'PENDING':
            await query.edit_message_text("⚠️ Penarikan ini sudah diproses.", reply_markup=back_keyboard())
            cursor.close()
            conn.close()
            return

        target_uid, nominal, metode, rekening, atas_nama, _ = res
        cursor.execute("UPDATE withdrawals SET status = 'REJECTED' WHERE id = %s", (wd_id,))
        cursor.execute('UPDATE users SET balance = balance + %s WHERE user_id = %s', (nominal, target_uid))
        conn.commit()
        cursor.close()
        conn.close()

        await query.edit_message_text(
            f"❌ *PENARIKAN #WD{wd_id} DITOLAK & SALDO DIREFUND!*\n\nNominal Rp {nominal:,} dikembalikan ke User `{target_uid}`.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali ke Panel WD", callback_data="admin_withdraw")]]),
            parse_mode='Markdown'
        )

        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=(
                    f"❌ *PENARIKAN DANA DITOLAK*\n"
                    f"═══════════════════════\n"
                    f"🆔 *ID WD:* `#WD{wd_id}`\n"
                    f"💵 *Nominal:* Rp {nominal:,}\n"
                    f"🏦 *Metode:* {metode}\n"
                    f"📌 *No Rek/HP:* `{rekening}`\n"
                    f"👤 *A/N:* `{atas_nama}`\n"
                    f"═══════════════════════\n"
                    f"Permintaan penarikan kamu ditolak oleh admin.\n"
                    f"💰 *Saldo sebesar Rp {nominal:,} telah dikembalikan (refund) secara otomatis ke akun kamu.*"
                ),
                parse_mode='Markdown'
            )
        except Exception:
            pass

async def render_admin_panel(query):
    pass_info = get_all_passwords_info()
    keyboard = []

    for pwd, data in pass_info.items():
        st = data['status']
        pr = data['price']
        st_icon = "🟢 AKTIF" if st == 'ACTIVE' else "🔴 NONAKTIF"
        keyboard.append([
            InlineKeyboardButton(f"🔑 [{pwd}]: {st_icon}", callback_data=f"admin_toggle_pwd_{pwd}"),
            InlineKeyboardButton(f"💵 Rp {pr:,} (Ubah)", callback_data=f"admin_set_price_ask_{pwd}")
        ])

    keyboard.extend([
        [InlineKeyboardButton("📝 Edit Deskripsi Start/Refresh", callback_data="admin_edit_welcome_ask")],
        [InlineKeyboardButton("⚙️ Kelola Setoran Gmail Active (Pending & Processing)", callback_data="admin_setoran")],
        [InlineKeyboardButton("🔄 Auto Rekap Massal -> Processing", callback_data="global_paste_process_ask")],
        [InlineKeyboardButton("✅ Auto Approve Massal -> Approved", callback_data="global_paste_approve_ask")],
        [InlineKeyboardButton("❌ Auto Reject Massal -> Rejected", callback_data="global_paste_reject_ask")],
        [InlineKeyboardButton("📢 Pengumuman / Broadcast All User", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💸 Kelola Withdraw Pending", callback_data="admin_withdraw")],
        [InlineKeyboardButton("📂 Fitur Ekspor Rekap Realtime (.txt)", callback_data="admin_export_menu")],
        [InlineKeyboardButton("« Kembali ke Menu Utama", callback_data="menu_utama")]
    ])

    pesan = (
        "⚙️ *PANEL ADMIN - PENGATURAN SANDI, HARGA & LAYANAN*\n"
        "═══════════════════════\n"
        "Atur status aktif/nonaktif dan harga masing-masing password di bawah ini:"
    )
    await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def render_admin_users_list(query, page=1):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT d.user_id, u.username, COUNT(d.id) AS active_count
        FROM deposits d
        LEFT JOIN users u ON d.user_id = u.user_id
        WHERE d.status IN ('PENDING', 'PROCESSING')
        GROUP BY d.user_id, u.username
        ORDER BY active_count DESC
    ''')
    user_list = cursor.fetchall()
    cursor.close()
    conn.close()

    if not user_list:
        await query.edit_message_text(
            "✅ *Tidak ada data setoran aktif (Pending/Processing) saat ini.*",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali ke Panel Admin", callback_data="admin_panel")]]),
            parse_mode='Markdown'
        )
        return

    total_users = len(user_list)
    total_pages = math.ceil(total_users / ITEMS_PER_PAGE_USERS)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * ITEMS_PER_PAGE_USERS
    end_idx = start_idx + ITEMS_PER_PAGE_USERS
    current_page_users = user_list[start_idx:end_idx]

    keyboard = []
    for target_uid, uname, cnt in current_page_users:
        u_text = f"@{uname}" if uname else f"ID: {target_uid}"
        keyboard.append([InlineKeyboardButton(f"👤 {u_text} ({cnt} Akun Aktif)", callback_data=f"admuser_{target_uid}_1")])

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("« Prev", callback_data=f"admin_setoran_page_{page - 1}"))
    nav_buttons.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next »", callback_data=f"admin_setoran_page_{page + 1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("« Kembali ke Panel Admin", callback_data="admin_panel")])

    total_active_all_accounts = sum(item[2] for item in user_list)
    pesan = (
        f"⚙️ *PANEL ADMIN - KELOLA SETORAN AKTIF (PENDING & PROCESSING)*\n"
        f"═══════════════════════\n"
        f"👥 *Total User Aktif:* `{total_users}` User\n"
        f"📦 *Total Akun Aktif:* `{total_active_all_accounts}` Akun\n"
        f"📖 *Halaman:* {page} dari {total_pages}\n"
        f"═══════════════════════\n"
        f"Pilih user di bawah ini untuk melihat dan mengelola status akun:"
    )
    await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def render_admin_user_deposits(query, target_uid, context, page=1):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, gmail, password, status, created_at, price FROM deposits WHERE user_id = %s AND status IN ('PENDING', 'PROCESSING') ORDER BY id DESC", (target_uid,))
    items = cursor.fetchall()
    cursor.close()
    conn.close()

    if not items:
        await query.edit_message_text("✅ *Tidak ada setoran aktif untuk user ini.*", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali ke Daftar User", callback_data="admin_setoran")]]), parse_mode='Markdown')
        return

    total_items = len(items)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE_DEPS)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * ITEMS_PER_PAGE_DEPS
    end_idx = start_idx + ITEMS_PER_PAGE_DEPS
    current_page_items = items[start_idx:end_idx]

    selected_deps = context.user_data.get('selected_deps', [])

    pesan = (
        f"📧 *PILIH AKUN GMAIL (User ID: `{target_uid}`)*\n"
        f"═══════════════════════\n"
        f"📦 *Total Akun Aktif User Ini:* {total_items} Akun\n"
        f"📖 *Halaman:* {page} dari {total_pages}\n"
        f"═══════════════════════\n"
        f"Silakan centang akun yang ingin diproses:\n\n"
    )
    keyboard = []

    for dep_id, g_mail, p_ass, st, dt, pr in current_page_items:
        is_checked = dep_id in selected_deps
        check_icon = "✅ [PILIH]" if is_checked else "⬜ [   ]"
        
        st_tag = "⏳ PENDING"
        if st == 'PROCESSING':
            st_tag = "🔄 PROC"

        pr_val = pr if pr else 4000
        
        pesan += f"{check_icon} `{g_mail}` | `{p_ass}` (Rp {pr_val:,}) ({st_tag})\n"
        keyboard.append([InlineKeyboardButton(f"{check_icon} {g_mail} ({st_tag})", callback_data=f"togdep_{dep_id}_{page}")])

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("« Prev", callback_data=f"admuser_{target_uid}_{page - 1}"))
    nav_buttons.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next »", callback_data=f"admuser_{target_uid}_{page + 1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("☑️ Pilih / Batalkan Semua Akun", callback_data=f"togall_deps_{page}")])
    keyboard.append([
        InlineKeyboardButton(f"🔄 Rekap ({len(selected_deps)})", callback_data="do_sel_process"),
        InlineKeyboardButton(f"✅ Approve ({len(selected_deps)})", callback_data="do_sel_approve"),
        InlineKeyboardButton(f"❌ Reject ({len(selected_deps)})", callback_data="do_sel_reject_ask")
    ])
    keyboard.append([InlineKeyboardButton("« Kembali ke Daftar User", callback_data="admin_setoran")])

    try:
        await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception:
        pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    current_mode = context.user_data.get('mode')

    raw_input_text = ""
    is_document_upload = False

    if update.message.document:
        doc = update.message.document
        if doc.file_name and doc.file_name.lower().endswith('.txt'):
            is_document_upload = True
            file_obj = await context.bot.get_file(doc.file_id)
            file_bytes = await file_obj.download_as_bytearray()
            try:
                raw_input_text = file_bytes.decode('utf-8')
            except UnicodeDecodeError:
                raw_input_text = file_bytes.decode('latin-1', errors='ignore')
        else:
            await update.message.reply_text("❌ Mohon kirimkan file dengan format `.txt`.", reply_markup=cancel_keyboard())
            return
    elif update.message.text:
        raw_input_text = update.message.text.strip()

    text = raw_input_text.strip()

    if not is_document_upload and text in ["🔄 Refresh / Start", "/start"]:
        await start(update, context)
        return
    elif not is_document_upload and text == "📜 Daftar Setoran Saya":
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT gmail, status, created_at, price FROM deposits WHERE user_id = %s ORDER BY id DESC LIMIT 15', (user.id,))
        items = cursor.fetchall()
        cursor.close()
        conn.close()

        if not items:
            pesan = "📜 *DAFTAR SETORAN GMAIL*\n═══════════════════════\nBelum ada riwayat setoran."
        else:
            pesan = "📜 *DAFTAR SETORAN GMAIL (15 Terakhir)*\n═══════════════════════\n"
            for g_mail, st, dt, pr in items:
                status_icon = "⏳ PENDING"
                if st == "PROCESSING":
                    status_icon = "🔄 PROCESSING (Sudah Rekap)"
                elif st == "APPROVED":
                    status_icon = "✅ APPROVED (Saldo Masuk)"
                elif st == "REJECTED":
                    status_icon = "❌ REJECTED (Ditolak)"
                
                waktu = dt if dt else get_wib_time()
                harga_str = f"Rp {pr:,}" if pr else "Rp 4.000"
                pesan += f"📧 `{g_mail}`\n└ Rate: *{harga_str}*\n└ Status: *{status_icon}*\n└ Waktu: `{waktu}`\n\n"

        await update.message.reply_text(pesan, reply_markup=back_keyboard(), parse_mode='Markdown')
        return
    elif not is_document_upload and text == "🚫 Batal Setoran Pending":
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, gmail, password, price FROM deposits WHERE user_id = %s AND status = 'PENDING' ORDER BY id DESC", (user.id,))
        pending_items = cursor.fetchall()
        cursor.close()
        conn.close()

        if not pending_items:
            await update.message.reply_text("🚫 *Tidak ada setoran berstatus PENDING yang dapat dibatalkan.*", reply_markup=back_keyboard(), parse_mode='Markdown')
            return

        pesan = (
            f"🚫 *BATALKAN SETORAN PENDING*\n"
            f"═══════════════════════\n"
            f"Pilih akun di bawah ini yang ingin Anda batalkan setorannya.\n"
            f"⚠️ *Perhatian:* Akun yang dibatalkan akan **langsung dihapus secara permanen dari database**.\n\n"
        )
        keyboard = []
        for dep_id, g_mail, p_ass, pr in pending_items[:15]:
            pr_val = pr if pr else 4000
            keyboard.append([InlineKeyboardButton(f"❌ Batal `{g_mail}` (Rp {pr_val:,})", callback_data=f"user_cancel_dep_{dep_id}")])
        
        keyboard.append([InlineKeyboardButton("🔥 Batalkan SEMUA Akun Pending", callback_data="user_cancel_all_pending")])
        keyboard.append([InlineKeyboardButton("« Kembali ke Menu Utama", callback_data="menu_utama")])
        await update.message.reply_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return
    elif not is_document_upload and text == "💰 Cek Saldo":
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM deposits WHERE user_id = %s AND status = 'APPROVED'", (user.id,))
        app_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM deposits WHERE user_id = %s AND status = 'PROCESSING'", (user.id,))
        proc_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM deposits WHERE user_id = %s AND status = 'PENDING'", (user.id,))
        pen_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COALESCE(SUM(price), 0) FROM deposits WHERE user_id = %s AND status IN ('PENDING', 'PROCESSING')", (user.id,))
        estimasi_pending = cursor.fetchone()[0]

        cursor.execute('SELECT balance FROM users WHERE user_id = %s', (user.id,))
        res = cursor.fetchone()
        balance_ready = res[0] if res else 0
        cursor.close()
        conn.close()

        pesan = (
            f"📊 *INFORMASI AKUN & SALDO*\n"
            f"═══════════════════════\n"
            f"💵 *Saldo Siap Dicairkan:* Rp {balance_ready:,}\n"
            f"⏳ *Estimasi Saldo Tertahan:* Rp {estimasi_pending:,}\n"
            f"═══════════════════════\n"
            f"✅ *Gmail Disetujui (Approved):* {app_count} Akun\n"
            f"🔄 *Gmail Diproses (Processing):* {proc_count} Akun\n"
            f"⏳ *Gmail Menunggu Rekap (Pending):* {pen_count} Akun\n"
            f"═══════════════════════"
        )
        await update.message.reply_text(pesan, reply_markup=back_keyboard(), parse_mode='Markdown')
        return
    elif not is_document_upload and text == "💬 Hubungi CS":
        await update.message.reply_text(
            f"💬 Silakan hubungi Customer Service kami di: @{CS_USERNAME}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Chat CS Sekarang", url=f"https://t.me/{CS_USERNAME}")]])
        )
        return

    text_normalized = unicodedata.normalize("NFKD", text)
    cleaned_text = "".join([c for c in text_normalized if not unicodedata.combining(c)])
    cleaned_text = cleaned_text.replace('\r', '').replace('\ufeff', '').replace('\u200b', '')
    
    raw_lines = cleaned_text.split('\n')
    lines = [line.strip() for line in raw_lines if line.strip()]

    # --- PROSES SIMPAN DESKRIPSI WELCOME/START BARU ---
    if current_mode == 'WAITING_WELCOME_TEXT':
        if user.id != ADMIN_CHAT_ID:
            return

        new_text = raw_input_text.strip()
        set_welcome_text_db(new_text)

        context.user_data.clear()
        await update.message.reply_text(
            "✅ *DESKRIPSI START/REFRESH BERHASIL DIPERBARUI!*\n\n"
            "Pesan sambutan baru akan langsung tampil saat user klik `/start` atau **🔄 Refresh / Start**.",
            reply_markup=main_menu_keyboard(user.id),
            parse_mode='Markdown'
        )
        return

    # --- PROSES EDIT HARGA PASSWORD ---
    if current_mode == 'WAITING_NEW_PWD_PRICE':
        if user.id != ADMIN_CHAT_ID:
            return

        target_pwd = context.user_data.get('target_pwd_for_price')
        if not text.isdigit():
            await update.message.reply_text("❌ Mohon masukkan angka nominal harga yang valid!", reply_markup=cancel_keyboard())
            return

        new_price = int(text)
        set_password_price(target_pwd, new_price)

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ *HARGA PASSWORD BERHASIL DIUBAH!*\n\n"
            f"🔑 *Password:* `{target_pwd}`\n"
            f"💵 *Harga Baru:* Rp {new_price:,}",
            reply_markup=main_menu_keyboard(user.id),
            parse_mode='Markdown'
        )
        return

    # --- PROSES BROADCAST PENGUMUMAN MASSAL ---
    if current_mode == 'WAITING_BROADCAST_TEXT':
        if user.id != ADMIN_CHAT_ID:
            return

        broadcast_msg = raw_input_text
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        user_rows = cursor.fetchall()
        cursor.close()
        conn.close()

        all_user_ids = [r[0] for r in user_rows]
        success_bcast = 0
        failed_bcast = 0

        for uid in all_user_ids:
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"📢 *PENGUMUMAN DARI ADMIN*\n═══════════════════════\n\n{broadcast_msg}",
                    parse_mode='Markdown'
                )
                success_bcast += 1
            except Exception:
                failed_bcast += 1

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ *PENGUMUMAN BERHASIL DIKIRIM!*\n\n"
            f"• Berhasil dikirim ke: `{success_bcast}` user\n"
            f"• Gagal / Dibatalkan: `{failed_bcast}` user",
            reply_markup=main_menu_keyboard(user.id),
            parse_mode='Markdown'
        )
        return

    # --- PROSES AUTO REKAP (PROCESSING) MASSAL VIA PASTE LIST (GLOBAL) ---
    if current_mode == 'WAITING_GLOBAL_PASTE_PROCESS':
        if user.id != ADMIN_CHAT_ID:
            return

        emails_to_process = []
        for line in lines:
            line_lower = line.lower()
            if ':' in line_lower:
                part = line_lower.split(':')[0].strip()
                if '@gmail.com' in part:
                    emails_to_process.append(part)
            elif '@gmail.com' in line_lower:
                emails_to_process.append(line_lower)

        if not emails_to_process:
            await update.message.reply_text("❌ Tidak ada email Gmail valid yang terdeteksi dari teks/file yang dikirim.", reply_markup=cancel_keyboard())
            return

        conn = get_db()
        cursor = conn.cursor()

        # FIXED: Menjangkau akun berstatus PENDING & PROCESSING
        placeholders = ','.join(['%s'] * len(emails_to_process))
        query_sql = f"SELECT id, user_id, gmail FROM deposits WHERE status IN ('PENDING', 'PROCESSING') AND LOWER(TRIM(gmail)) IN ({placeholders})"
        cursor.execute(query_sql, tuple(emails_to_process))
        found_rows = cursor.fetchall()

        if not found_rows:
            cursor.close()
            conn.close()
            await update.message.reply_text("⚠️ Tidak ada akun setoran yang terdeteksi cocok dalam status PENDING/PROCESSING di database.", reply_markup=cancel_keyboard())
            return

        found_ids = [r[0] for r in found_rows]
        affected_user_ids = list(set([r[1] for r in found_rows]))

        update_placeholders = ','.join(['%s'] * len(found_ids))
        cursor.execute(f"UPDATE deposits SET status = 'PROCESSING' WHERE id IN ({update_placeholders})", tuple(found_ids))
        conn.commit()

        user_notif_count = {}
        for _, uid, _ in found_rows:
            user_notif_count[uid] = user_notif_count.get(uid, 0) + 1

        for uid, count_acc in user_notif_count.items():
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"🔄 *SETORAN TELAH DIREKAP!*\n\nSebanyak *{count_acc} akun Gmail* kamu telah direkap oleh admin dan sedang dalam tahap verifikasi (Processing).",
                    parse_mode='Markdown'
                )
            except Exception:
                pass

        for uid in affected_user_ids:
            cursor.execute("SELECT username FROM users WHERE user_id = %s", (uid,))
            u_res = cursor.fetchone()
            uname = u_res[0] if u_res else None
            u_text = f"@{uname}" if uname else f"User_{uid}"
            
            txt_file, filename = generate_user_txt_rekap(uid, u_text)
            try:
                await context.bot.send_document(
                    chat_id=user.id,
                    document=txt_file,
                    filename=filename,
                    caption=f"📄 *REKAP UPDATE REALTIME (PROCESSING)*\n• User: {u_text}\n• Total akun diupdate: `{user_notif_count.get(uid, 0)}` akun."
                )
            except Exception:
                pass

        cursor.close()
        conn.close()

        context.user_data.clear()
        await update.message.reply_text(
            f"🔄 *AUTO REKAP (PROCESSING) MASSAL BERHASIL!*\n\n"
            f"• Total Email Di-paste: `{len(emails_to_process)}` akun\n"
            f"• Berhasil Di-Processing: `{len(found_ids)}` akun\n"
            f"• Total User Terdampak: `{len(affected_user_ids)}` user\n\n"
            f"File rekap .txt realtime untuk masing-masing user telah dikirimkan di atas.",
            reply_markup=main_menu_keyboard(user.id),
            parse_mode='Markdown'
        )
        return

    # --- PROSES AUTO APPROVE MASSAL VIA PASTE LIST (GLOBAL) ---
    if current_mode == 'WAITING_GLOBAL_PASTE_APPROVE':
        if user.id != ADMIN_CHAT_ID:
            return

        emails_to_approve = []
        for line in lines:
            line_lower = line.lower()
            if ':' in line_lower:
                part = line_lower.split(':')[0].strip()
                if '@gmail.com' in part:
                    emails_to_approve.append(part)
            elif '@gmail.com' in line_lower:
                emails_to_approve.append(line_lower)

        if not emails_to_approve:
            await update.message.reply_text("❌ Tidak ada email Gmail valid yang terdeteksi dari teks/file yang dikirim.", reply_markup=cancel_keyboard())
            return

        conn = get_db()
        cursor = conn.cursor()

        # FIXED: Menjangkau akun berstatus PENDING & PROCESSING
        placeholders = ','.join(['%s'] * len(emails_to_approve))
        query_sql = f"SELECT id, user_id, price, gmail FROM deposits WHERE status IN ('PENDING', 'PROCESSING') AND LOWER(TRIM(gmail)) IN ({placeholders})"
        cursor.execute(query_sql, tuple(emails_to_approve))
        found_rows = cursor.fetchall()

        if not found_rows:
            cursor.close()
            conn.close()
            await update.message.reply_text("⚠️ Tidak ada akun setoran yang terdeteksi cocok dalam status PENDING/PROCESSING di database.", reply_markup=cancel_keyboard())
            return

        user_credits = {}
        user_counts = {}
        found_ids = []

        for dep_id, uid, pr, gm in found_rows:
            found_ids.append(dep_id)
            pr_val = pr if pr else 4000
            user_credits[uid] = user_credits.get(uid, 0) + pr_val
            user_counts[uid] = user_counts.get(uid, 0) + 1

        update_placeholders = ','.join(['%s'] * len(found_ids))
        cursor.execute(f"UPDATE deposits SET status = 'APPROVED' WHERE id IN ({update_placeholders})", tuple(found_ids))

        for uid, total_added in user_credits.items():
            cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (total_added, uid))

        conn.commit()

        for uid, total_added in user_credits.items():
            cnt = user_counts[uid]
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"🎉 *SETORAN DI-APPROVE!*\n\nSebanyak *{cnt} akun Gmail* kamu telah diverifikasi dan disetujui oleh admin.\n💰 *+Rp {total_added:,}* telah masuk ke saldo cair kamu!",
                    parse_mode='Markdown'
                )
            except Exception:
                pass

        for uid in user_credits.keys():
            cursor.execute("SELECT username FROM users WHERE user_id = %s", (uid,))
            u_res = cursor.fetchone()
            uname = u_res[0] if u_res else None
            u_text = f"@{uname}" if uname else f"User_{uid}"
            
            txt_file, filename = generate_user_txt_rekap(uid, u_text)
            try:
                await context.bot.send_document(
                    chat_id=user.id,
                    document=txt_file,
                    filename=filename,
                    caption=f"📄 *REKAP UPDATE REALTIME (APPROVED)*\n• User: {u_text}\n• Total akun disetujui: `{user_counts[uid]}` akun."
                )
            except Exception:
                pass

        cursor.close()
        conn.close()

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ *AUTO APPROVE MASSAL BERHASIL!*\n\n"
            f"• Total Email Di-paste: `{len(emails_to_approve)}` akun\n"
            f"• Berhasil Di-Approve: `{len(found_ids)}` akun\n"
            f"• Total User Terdampak: `{len(user_credits)}` user\n\n"
            f"Saldo masing-masing user telah bertambah dan file rekap .txt realtime telah dikirimkan di atas.",
            reply_markup=main_menu_keyboard(user.id),
            parse_mode='Markdown'
        )
        return

    # --- PROSES AUTO REJECT MASSAL VIA PASTE LIST (GLOBAL) ---
    if current_mode == 'WAITING_GLOBAL_PASTE_REJECT':
        if user.id != ADMIN_CHAT_ID:
            return

        reason_idx = context.user_data.get('global_reject_reason_idx', 0)
        chosen_reason = REJECT_REASONS[reason_idx] if reason_idx < len(REJECT_REASONS) else "Ditolak Admin"

        emails_to_reject = []
        for line in lines:
            line_lower = line.lower()
            if ':' in line_lower:
                part = line_lower.split(':')[0].strip()
                if '@gmail.com' in part:
                    emails_to_reject.append(part)
            elif '@gmail.com' in line_lower:
                emails_to_reject.append(line_lower)

        if not emails_to_reject:
            await update.message.reply_text("❌ Tidak ada email Gmail valid yang terdeteksi dari teks/file yang dikirim.", reply_markup=cancel_keyboard())
            return

        conn = get_db()
        cursor = conn.cursor()

        # FIXED: Menjangkau akun berstatus PENDING & PROCESSING
        placeholders = ','.join(['%s'] * len(emails_to_reject))
        query_sql = f"SELECT id, user_id, gmail FROM deposits WHERE status IN ('PENDING', 'PROCESSING') AND LOWER(TRIM(gmail)) IN ({placeholders})"
        cursor.execute(query_sql, tuple(emails_to_reject))
        found_rows = cursor.fetchall()

        if not found_rows:
            cursor.close()
            conn.close()
            await update.message.reply_text("⚠️ Tidak ada akun setoran yang terdeteksi cocok dalam status PENDING/PROCESSING di database.", reply_markup=cancel_keyboard())
            return

        found_ids = [r[0] for r in found_rows]
        user_rejected_emails = {}

        for dep_id, uid, gm in found_rows:
            if uid not in user_rejected_emails:
                user_rejected_emails[uid] = []
            user_rejected_emails[uid].append(gm)

        update_placeholders = ','.join(['%s'] * len(found_ids))
        cursor.execute(f"UPDATE deposits SET status = 'REJECTED' WHERE id IN ({update_placeholders})", tuple(found_ids))
        conn.commit()

        for uid, rej_list in user_rejected_emails.items():
            try:
                email_list_str = "\n".join([f"• `{e}`" for e in rej_list])
                await context.bot.send_message(
                    chat_id=uid,
                    text=(
                        f"❌ *PEMBERITAHUAN PENOLAKAN GMAIL*\n"
                        f"═══════════════════════\n"
                        f"⚠️ Sejumlah {len(rej_list)} akun setoran Anda ditolak:\n"
                        f"{email_list_str}\n\n"
                        f"📌 *Alasan Ditolak:* {chosen_reason}\n"
                        f"═══════════════════════\n"
                        f"Silakan periksa kembali akun Anda."
                    ),
                    parse_mode='Markdown'
                )
            except Exception:
                pass

        for uid in user_rejected_emails.keys():
            cursor.execute("SELECT username FROM users WHERE user_id = %s", (uid,))
            u_res = cursor.fetchone()
            uname = u_res[0] if u_res else None
            u_text = f"@{uname}" if uname else f"User_{uid}"
            
            txt_file, filename = generate_user_txt_rekap(uid, u_text)
            try:
                await context.bot.send_document(
                    chat_id=user.id,
                    document=txt_file,
                    filename=filename,
                    caption=f"📄 *REKAP UPDATE REALTIME (REJECTED)*\n• User: {u_text}\n• Total akun ditolak: `{len(user_rejected_emails[uid])}` akun."
                )
            except Exception:
                pass

        cursor.close()
        conn.close()

        context.user_data.clear()
        await update.message.reply_text(
            f"❌ *AUTO REJECT MASSAL BERHASIL!*\n\n"
            f"• Total Email Di-paste: `{len(emails_to_reject)}` akun\n"
            f"• Berhasil Di-Reject: `{len(found_ids)}` akun\n"
            f"• Total User Terdampak: `{len(user_rejected_emails)}` user\n"
            f"• Alasan: *{chosen_reason}*\n\n"
            f"Pemberitahuan telah dikirimkan ke user terkait dan file rekap .txt realtime telah dikirimkan di atas.",
            reply_markup=main_menu_keyboard(user.id),
            parse_mode='Markdown'
        )
        return

    # --- PROSES WAITING REK (PENARIKAN DANA) ---
    if current_mode == 'WAITING_REK':
        nominal = context.user_data.get('wd_nominal', 0)
        metode = context.user_data.get('wd_metode', 'DANA')
        input_rek = text.strip()

        context.user_data['wd_rekening'] = input_rek
        context.user_data['mode'] = 'WAITING_AN'

        pesan = (
            f"👤 *INPUT NAMA PEMILIK (A/N)*\n"
            f"═══════════════════════\n"
            f"💵 *Nominal:* Rp {nominal:,}\n"
            f"🏦 *Metode:* {metode}\n"
            f"📌 *No Rek/HP:* `{input_rek}`\n\n"
            f"Silakan *ketik dan kirimkan* Nama Lengkap Pemilik Rekening / E-Wallet:"
        )
        await update.message.reply_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')
        return

    if current_mode == 'WAITING_AN':
        nominal = context.user_data.get('wd_nominal', 0)
        metode = context.user_data.get('wd_metode', 'DANA')
        rekening = context.user_data.get('wd_rekening', '-')
        atas_nama = text.strip()

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT balance FROM users WHERE user_id = %s', (user.id,))
        res = cursor.fetchone()
        current_balance = res[0] if res else 0

        if current_balance < nominal:
            await update.message.reply_text("❌ Saldo kamu tidak mencukupi untuk melakukan penarikan ini.", reply_markup=back_keyboard())
            cursor.close()
            conn.close()
            context.user_data.clear()
            return

        cursor.execute('UPDATE users SET balance = balance - %s WHERE user_id = %s', (nominal, user.id))
        
        wib_time = get_wib_time()
        cursor.execute('''
            INSERT INTO withdrawals (user_id, nominal, metode, rekening, atas_nama, status, created_at)
            VALUES (%s, %s, %s, %s, %s, 'PENDING', %s)
            RETURNING id
        ''', (user.id, nominal, metode, rekening, atas_nama, wib_time))
        wd_id = cursor.fetchone()[0]

        conn.commit()
        cursor.close()
        conn.close()

        context.user_data.clear()

        pesan_user = (
            f"✅ *PERMINTAAN PENARIKAN DIBUAT!*\n"
            f"═══════════════════════\n"
            f"🆔 *ID WD:* `#WD{wd_id}`\n"
            f"💵 *Nominal:* Rp {nominal:,}\n"
            f"🏦 *Metode:* {metode}\n"
            f"📌 *No Rek/HP:* `{rekening}`\n"
            f"👤 *A/N:* `{atas_nama}`\n"
            f"🕒 *Waktu:* `{wib_time}`\n"
            f"═══════════════════════\n"
            f"Status penarikan kamu saat ini *PENDING*. Admin akan segera mentransfer dana kamu."
        )
        await update.message.reply_text(pesan_user, reply_markup=back_keyboard(), parse_mode='Markdown')

        try:
            u_text = f"@{user.username}" if user.username else f"ID: `{user.id}`"
            pesan_admin = (
                f"🔔 *PERMINTAAN WITHDRAW BARU! (#WD{wd_id})*\n"
                f"═══════════════════════\n"
                f"👤 *User:* {u_text}\n"
                f"💵 *Nominal:* Rp {nominal:,}\n"
                f"🏦 *Metode:* {metode}\n"
                f"📌 *No Rek/HP:* `{rekening}`\n"
                f"👤 *A/N:* `{atas_nama}`\n"
                f"🕒 *Waktu:* `{wib_time}`"
            )
            keyboard_admin = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(f"✅ Approve #WD{wd_id}", callback_data=f"accwd_{wd_id}"),
                    InlineKeyboardButton(f"❌ Reject #WD{wd_id}", callback_data=f"rejwd_{wd_id}")
                ]
            ])
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=pesan_admin, reply_markup=keyboard_admin, parse_mode='Markdown')
        except Exception:
            pass
        return

    # --- PROSES INPUT SETORAN SATUAN ---
    if current_mode == 'SATUAN':
        active_pwds = get_active_passwords()
        if not active_pwds:
            await update.message.reply_text("❌ Mohon maaf, setoran sedang ditutup (Overload).", reply_markup=back_keyboard())
            return

        valid_entries = []
        invalid_entries = []

        for line in lines:
            if ':' in line:
                parts = line.split(':', 1)
                g_mail = parts[0].strip().lower()
                p_ass = parts[1].strip()

                if '@gmail.com' in g_mail:
                    valid_entries.append((g_mail, p_ass))
                else:
                    invalid_entries.append(line)
            else:
                invalid_entries.append(line)

        if not valid_entries:
            await update.message.reply_text(
                "❌ Format tidak valid!\n\nGunakan format: `email@gmail.com:password`",
                reply_markup=cancel_keyboard(),
                parse_mode='Markdown'
            )
            return

        conn = get_db()
        cursor = conn.cursor()

        saved_count = 0
        duplicate_count = 0
        wrong_pwd_count = 0

        wib_time = get_wib_time()

        for g_mail, p_ass in valid_entries:
            if p_ass not in active_pwds:
                wrong_pwd_count += 1
                continue

            item_price = active_pwds[p_ass]
            try:
                cursor.execute(
                    'INSERT INTO deposits (user_id, gmail, password, status, created_at, price) VALUES (%s, %s, %s, %s, %s, %s)',
                    (user.id, g_mail, p_ass, 'PENDING', wib_time, item_price)
                )
                saved_count += 1
            except psycopg2.IntegrityError:
                conn.rollback()
                duplicate_count += 1
            except Exception:
                conn.rollback()

        conn.commit()
        cursor.close()
        conn.close()

        context.user_data.clear()

        pesan = (
            f"📥 *HASIL PROSES SETORAN SATUAN*\n"
            f"═══════════════════════\n"
            f"✅ *Berhasil Tersimpan:* {saved_count} Akun\n"
            f"⚠️ *Duplikat (Pernah Ada):* {duplicate_count} Akun\n"
            f"❌ *Password Nonaktif/Salah:* {wrong_pwd_count} Akun\n"
            f"═══════════════════════\n"
            f"Status akun kamu sekarang *PENDING* (menunggu rekap/pengecekan admin)."
        )
        await update.message.reply_text(pesan, reply_markup=main_menu_keyboard(user.id), parse_mode='Markdown')
        return

    # --- PROSES INPUT SETORAN BULKING ---
    if current_mode == 'BULKING_INPUT_EMAILS':
        chosen_password = context.user_data.get('bulk_password')
        pwd_price = context.user_data.get('bulk_price', 4000)

        active_pwds = get_active_passwords()
        if not chosen_password or chosen_password not in active_pwds:
            await update.message.reply_text("❌ Sandi yang dipilih tidak lagi aktif. Silakan ulangi proses bulking.", reply_markup=back_keyboard())
            return

        emails_to_add = []
        for line in lines:
            if ':' in line:
                part_email = line.split(':')[0].strip().lower()
                if '@gmail.com' in part_email:
                    emails_to_add.append(part_email)
            elif '@gmail.com' in line.lower():
                emails_to_add.append(line.strip().lower())

        if not emails_to_add:
            await update.message.reply_text(
                "❌ Tidak ditemukan alamat `@gmail.com` yang valid pada baris input kamu.",
                reply_markup=cancel_keyboard(),
                parse_mode='Markdown'
            )
            return

        if len(emails_to_add) > MAX_BULK_LIMIT:
            await update.message.reply_text(
                f"⚠️ *JUMLAH AKUN MELEBIHI BATAS!*\n\n"
                f"Kamu mengirim *{len(emails_to_add)} akun*. Batas maksimal bulking adalah *{MAX_BULK_LIMIT} akun* per sekali setor.\n"
                f"Silakan kurangi daftar akun kamu dan coba lagi.",
                reply_markup=cancel_keyboard(),
                parse_mode='Markdown'
            )
            return

        conn = get_db()
        cursor = conn.cursor()

        saved_count = 0
        duplicate_count = 0
        wib_time = get_wib_time()

        for g_mail in emails_to_add:
            try:
                cursor.execute(
                    'INSERT INTO deposits (user_id, gmail, password, status, created_at, price) VALUES (%s, %s, %s, %s, %s, %s)',
                    (user.id, g_mail, chosen_password, 'PENDING', wib_time, pwd_price)
                )
                saved_count += 1
            except psycopg2.IntegrityError:
                conn.rollback()
                duplicate_count += 1
            except Exception:
                conn.rollback()

        conn.commit()
        cursor.close()
        conn.close()

        context.user_data.clear()

        pesan = (
            f"📦 *HASIL PROSES SETORAN BULKING*\n"
            f"═══════════════════════\n"
            f"🔑 *Password Dipakai:* `{chosen_password}`\n"
            f"💵 *Harga Per Akun:* Rp {pwd_price:,}\n"
            f"═══════════════════════\n"
            f"✅ *Berhasil Tersimpan:* {saved_count} Akun\n"
            f"⚠️ *Duplikat (Pernah Ada):* {duplicate_count} Akun\n"
            f"═══════════════════════\n"
            f"Status akun kamu sekarang *PENDING* (menunggu rekap/pengecekan admin)."
        )
        await update.message.reply_text(pesan, reply_markup=main_menu_keyboard(user.id), parse_mode='Markdown')
        return

    # Fallback jika tidak ada perintah/mode aktif
    await update.message.reply_text(get_welcome_text(user.first_name), reply_markup=main_menu_keyboard(user.id), parse_mode='Markdown')

async def post_init(application: ApplicationBuilder):
    init_db()
    commands = [
        BotCommand("start", "Tampilkan Menu Utama / Refresh")
    ]
    await application.bot.set_my_commands(commands)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.MimeType("text/plain"), handle_message))

    print("Bot Setoran Gmail V30 Aktif & Berjalan...")
    app.run_polling()

if __name__ == '__main__':
    main()
