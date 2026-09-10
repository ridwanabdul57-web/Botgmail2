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

# Default Password: Password -> (Status, Harga Default)
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
    
    # Tambah kolom price jika belum ada di tabel deposits
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

def get_all_passwords_info():
    """Mengembalikan dict: {pwd: {'status': status, 'price': harga}}"""
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
    """Mengembalikan dict password aktif beserta harganya: {pwd: price}"""
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

# ----------------- KEYBOARD MENUS -----------------
def persistent_reply_keyboard():
    keyboard = [
        [KeyboardButton("🔄 Refresh / Start"), KeyboardButton("📜 Daftar Setoran Saya")],
        [KeyboardButton("💰 Cek Saldo"), KeyboardButton("💬 Hubungi CS")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def main_menu_keyboard(user_id):
    active_pwds = get_active_passwords()
    
    # Apabila stock overload / password tidak ada yang aktif
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

        # Hitung estimasi saldo tertahan berbasis harga masing-masing deposit
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
                    status_icon = "❌ REJECTED"
                
                waktu = dt if dt else get_wib_time()
                harga_str = f"Rp {pr:,}" if pr else "Rp 4.000"
                pesan += f"📧 `{g_mail}`\n└ Rate: *{harga_str}*\n└ Status: *{status_icon}*\n└ Waktu: `{waktu}`\n\n"

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
            ORDER BY d.created_at DESC, d.user_id DESC
        ''')
        all_deposits = cursor.fetchall()
        cursor.close()
        conn.close()

        if not all_deposits:
            await query.answer("⚠️ Belum ada riwayat setoran di database.", show_alert=True)
            return

        wib_now_date = datetime.now(timezone(timedelta(hours=7))).strftime('%Y%m%d_%H%M%S')
        grouped_data = {}
        total_semua_akun = len(all_deposits)

        for uid, uname, gmail, pwd, status, dt, pr in all_deposits:
            tanggal_str = dt.split()[0] if dt and len(dt.split()) > 0 else datetime.now(timezone(timedelta(hours=7))).strftime('%d-%m-%Y')
            jam_str = dt.split()[1] if dt and len(dt.split()) > 1 else "00:00:00"
            pr_val = pr if pr else 4000
            
            if tanggal_str not in grouped_data:
                grouped_data[tanggal_str] = {}
            
            user_key = (uid, uname)
            if user_key not in grouped_data[tanggal_str]:
                grouped_data[tanggal_str][user_key] = {'PENDING': [], 'PROCESSING': [], 'APPROVED': [], 'REJECTED': []}
            
            if status in grouped_data[tanggal_str][user_key]:
                grouped_data[tanggal_str][user_key][status].append(f"{gmail}:{pwd} (Rp {pr_val:,}) | {jam_str} WIB")

        txt_lines = []
        for tanggal, users_dict in grouped_data.items():
            total_akun_tanggal = sum(
                len(s['PENDING']) + len(s['PROCESSING']) + len(s['APPROVED']) + len(s['REJECTED'])
                for s in users_dict.values()
            )
            txt_lines.append(f"📅 TANGGAL SETOR: {tanggal} (Total Akun: {total_akun_tanggal})")
            txt_lines.append("="*50)
            
            for (uid, uname), statuses_dict in users_dict.items():
                u_tag = f"@{uname}" if uname else f"User_{uid}"
                tot_p = len(statuses_dict['PENDING'])
                tot_pr = len(statuses_dict['PROCESSING'])
                tot_a = len(statuses_dict['APPROVED'])
                tot_r = len(statuses_dict['REJECTED'])
                tot_user = tot_p + tot_pr + tot_a + tot_r

                txt_lines.append(f"👤 USER: {u_tag} (ID: {uid}) - Total Setor: {tot_user} Akun")
                
                txt_lines.append(f"  • Gmail Pending (Belum Rekap) ({tot_p}) :")
                if statuses_dict['PENDING']:
                    for item in statuses_dict['PENDING']:
                        txt_lines.append(f"    {item}")
                else:
                    txt_lines.append("    -")
                
                txt_lines.append(f"  • Gmail Processing (Sudah Rekap) ({tot_pr}) :")
                if statuses_dict['PROCESSING']:
                    for item in statuses_dict['PROCESSING']:
                        txt_lines.append(f"    {item}")
                else:
                    txt_lines.append("    -")

                txt_lines.append(f"  • Gmail Approved (Saldo Masuk) ({tot_a}) :")
                if statuses_dict['APPROVED']:
                    for item in statuses_dict['APPROVED']:
                        txt_lines.append(f"    {item}")
                else:
                    txt_lines.append("    -")

                txt_lines.append(f"  • Gmail Rejected ({tot_r}) :")
                if statuses_dict['REJECTED']:
                    for item in statuses_dict['REJECTED']:
                        txt_lines.append(f"    {item}")
                else:
                    txt_lines.append("    -")
                
                txt_lines.append("--------------------------------------------------")
            txt_lines.append("\n")

        txt_lines.append("==================================================")
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
        cursor.close()
        conn.close()

        count = len(selected_deps)
        context.user_data['selected_deps'] = []

        await query.edit_message_text(
            f"🔄 *REKAP (PROCESSING) TERPILIH BERHASIL!*\n\nTotal `{count}` akun dari User `{target_uid}` diubah statusnya menjadi PROCESSING (Sudah Rekap).",
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
        # Ambil total harga spesifik dari akun-akun yang di-approve
        cursor.execute(f"SELECT COALESCE(SUM(price), 0) FROM deposits WHERE id IN ({placeholders})", tuple(selected_deps))
        total_added = cursor.fetchone()[0]

        cursor.execute(f"UPDATE deposits SET status = 'APPROVED' WHERE id IN ({placeholders})", tuple(selected_deps))
        
        count = len(selected_deps)
        cursor.execute('UPDATE users SET balance = balance + %s WHERE user_id = %s', (total_added, target_uid))
        conn.commit()
        cursor.close()
        conn.close()

        context.user_data['selected_deps'] = []
        await query.edit_message_text(
            f"✅ *APPROVE TERPILIH BERHASIL!*\n\nTotal `{count}` akun dari User `{target_uid}` telah disetujui.\nSaldo +Rp {total_added:,} telah dikreditkan.",
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
        conn.commit()
        cursor.close()
        conn.close()

        count = len(selected_deps)
        context.user_data['selected_deps'] = []

        await query.edit_message_text(
            f"❌ *REJECT TERPILIH BERHASIL!*\n\nTotal `{count}` akun dari User `{target_uid}` telah ditolak.\n📌 *Alasan:* {chosen_reason}",
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
        [InlineKeyboardButton("⚙️ Kelola Setoran Gmail Active", callback_data="admin_setoran")],
        [InlineKeyboardButton("🔄 Auto Rekap Massal -> Processing", callback_data="global_paste_process_ask")],
        [InlineKeyboardButton("✅ Auto Approve Massal -> Approved", callback_data="global_paste_approve_ask")],
        [InlineKeyboardButton("❌ Auto Reject Massal -> Rejected", callback_data="global_paste_reject_ask")],
        [InlineKeyboardButton("📢 Pengumuman / Broadcast All User", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💸 Kelola Withdraw Pending", callback_data="admin_withdraw")],
        [InlineKeyboardButton("📂 Rekap Setoran Terpisah Realtime (.txt)", callback_data="admin_export_all_deposits")],
        [InlineKeyboardButton("« Kembali ke Menu Utama", callback_data="menu_utama")]
    ])

    pesan = (
        "⚙️ *PANEL ADMIN - PENGATURAN SANDI, HARGA & LAYANAN*\n"
        "═══════════════════════\n"
        "Atur status aktif/nonaktif dan harga masing-masing password di bawah ini:"
    )
    await query.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ----------------- AKURASI USER & SLIDE / PAGINATION ADMIN -----------------
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
            "✅ *Tidak ada setoran pending atau processing saat ini.*",
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
        keyboard.append([InlineKeyboardButton(f"👤 {u_text} ({cnt} Akun Active)", callback_data=f"admuser_{target_uid}_1")])

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
        f"⚙️ *PANEL ADMIN - KELOLA SETORAN UNTUK VERIFIKASI*\n"
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
        await query.edit_message_text("✅ *Tidak ada setoran aktif (Pending/Processing) untuk user ini.*", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali ke Daftar User", callback_data="admin_setoran")]]), parse_mode='Markdown')
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
        f"📦 *Total Akun User Ini:* {total_items} Akun\n"
        f"📖 *Halaman:* {page} dari {total_pages}\n"
        f"═══════════════════════\n"
        f"Silakan centang akun yang ingin diproses:\n\n"
    )
    keyboard = []

    for dep_id, g_mail, p_ass, st, dt, pr in current_page_items:
        is_checked = dep_id in selected_deps
        check_icon = "✅ [PILIH]" if is_checked else "⬜ [   ]"
        st_tag = "⏳ PENDING" if st == 'PENDING' else "🔄 PROC"
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
                    status_icon = "❌ REJECTED"
                
                waktu = dt if dt else get_wib_time()
                harga_str = f"Rp {pr:,}" if pr else "Rp 4.000"
                pesan += f"📧 `{g_mail}`\n└ Rate: *{harga_str}*\n└ Status: *{status_icon}*\n└ Waktu: `{waktu}`\n\n"

        await update.message.reply_text(pesan, reply_markup=back_keyboard(), parse_mode='Markdown')
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
            await update.message.reply_text(
                "❌ *Format email tidak valid atau tidak ditemukan.*\nSilakan kirim ulang daftar email yang benar atau klik batal.",
                reply_markup=cancel_keyboard(),
                parse_mode='Markdown'
            )
            return

        conn = get_db()
        cursor = conn.cursor()

        success_count = 0
        not_found_count = 0
        user_proc_map = {}

        for email in emails_to_process:
            cursor.execute("SELECT id, user_id FROM deposits WHERE TRIM(LOWER(gmail)) = TRIM(LOWER(%s)) AND status = 'PENDING'", (email,))
            row = cursor.fetchone()
            
            if row:
                dep_id, target_uid = row
                cursor.execute("UPDATE deposits SET status = 'PROCESSING' WHERE id = %s", (dep_id,))
                success_count += 1
                user_proc_map[target_uid] = user_proc_map.get(target_uid, 0) + 1
            else:
                not_found_count += 1

        conn.commit()
        cursor.close()
        conn.close()

        for target_uid, proc_cnt in user_proc_map.items():
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        f"🔄 *SETORAN TELAH DIREKAP!*\n\n"
                        f"Sebanyak *{proc_cnt} akun Gmail* kamu telah direkap oleh admin dan sekarang berada dalam tahap pengecekan (Processing)."
                    ),
                    parse_mode='Markdown'
                )
            except Exception:
                pass

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ *AUTO REKAP (PROCESSING) MASSAL GLOBAL BERHASIL!*\n\n"
            f"• Berhasil diubah status ke PROCESSING: `{success_count}` akun untuk `{len(user_proc_map)}` user\n"
            f"• Tidak cocok / bukan status PENDING: `{not_found_count}` akun",
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
            await update.message.reply_text(
                "❌ *Format email tidak valid atau tidak ditemukan.*\nSilakan kirim ulang daftar email yang benar atau klik batal.",
                reply_markup=cancel_keyboard(),
                parse_mode='Markdown'
            )
            return

        conn = get_db()
        cursor = conn.cursor()

        success_count = 0
        not_found_count = 0
        user_notif_map = {}

        for email in emails_to_reject:
            cursor.execute("SELECT id, user_id FROM deposits WHERE TRIM(LOWER(gmail)) = TRIM(LOWER(%s)) AND status IN ('PENDING', 'PROCESSING')", (email,))
            row = cursor.fetchone()
            
            if row:
                dep_id, target_uid = row
                cursor.execute("UPDATE deposits SET status = 'REJECTED' WHERE id = %s", (dep_id,))
                success_count += 1
                
                if target_uid not in user_notif_map:
                    user_notif_map[target_uid] = []
                user_notif_map[target_uid].append(email)
            else:
                not_found_count += 1

        conn.commit()
        cursor.close()
        conn.close()

        for target_uid, emails in user_notif_map.items():
            try:
                email_list_str = "\n".join([f"• `{e}`" for e in emails])
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        f"❌ *PEMBERITAHUAN PENOLAKAN GMAIL*\n"
                        f"═══════════════════════\n"
                        f"⚠️ Sejumlah {len(emails)} akun setoran Anda ditolak:\n"
                        f"{email_list_str}\n\n"
                        f"📌 *Alasan Ditolak:* {chosen_reason}\n"
                        f"═══════════════════════\n"
                        f"Silakan periksa kembali akun Anda."
                    ),
                    parse_mode='Markdown'
                )
            except Exception:
                pass

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ *AUTO REJECT MASSAL GLOBAL BERHASIL!*\n\n"
            f"• Berhasil di-reject & dinotifikasi: `{success_count}` akun dari `{len(user_notif_map)}` user\n"
            f"• Tidak cocok / bukan status aktif: `{not_found_count}` akun\n"
            f"• Alasan: {chosen_reason}",
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
            await update.message.reply_text(
                "❌ *Format email tidak valid atau tidak ditemukan.*\nSilakan kirim ulang daftar email yang benar atau klik batal.",
                reply_markup=cancel_keyboard(),
                parse_mode='Markdown'
            )
            return

        conn = get_db()
        cursor = conn.cursor()

        success_count = 0
        not_found_count = 0
        user_approve_map = {}
        total_payout_sum = 0

        for email in emails_to_approve:
            cursor.execute("SELECT id, user_id, price FROM deposits WHERE TRIM(LOWER(gmail)) = TRIM(LOWER(%s)) AND status IN ('PENDING', 'PROCESSING')", (email,))
            row = cursor.fetchone()
            
            if row:
                dep_id, target_uid, item_price = row
                item_price_val = item_price if item_price else 4000
                
                cursor.execute("UPDATE deposits SET status = 'APPROVED' WHERE id = %s", (dep_id,))
                cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (item_price_val, target_uid))
                
                success_count += 1
                total_payout_sum += item_price_val
                
                if target_uid not in user_approve_map:
                    user_approve_map[target_uid] = {'count': 0, 'added_balance': 0}
                user_approve_map[target_uid]['count'] += 1
                user_approve_map[target_uid]['added_balance'] += item_price_val
            else:
                not_found_count += 1

        conn.commit()
        cursor.close()
        conn.close()

        for target_uid, data_acc in user_approve_map.items():
            app_cnt = data_acc['count']
            tot_added = data_acc['added_balance']
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        f"🎉 *SETORAN DI-APPROVE!*\n\n"
                        f"Sebanyak *{app_cnt} akun Gmail* kamu telah diverifikasi dan disetujui oleh admin.\n"
                        f"💰 *+Rp {tot_added:,}* telah masuk ke saldo cair kamu!"
                    ),
                    parse_mode='Markdown'
                )
            except Exception:
                pass

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ *AUTO APPROVE MASSAL GLOBAL BERHASIL!*\n\n"
            f"• Berhasil di-approve & ditambah saldo: `{success_count}` akun untuk `{len(user_approve_map)}` user\n"
            f"• Tidak cocok / bukan status aktif: `{not_found_count}` akun\n"
            f"• Total nominal dikreditkan: Rp {total_payout_sum:,}",
            reply_markup=main_menu_keyboard(user.id),
            parse_mode='Markdown'
        )
        return

    if current_mode == 'WAITING_REK':
        context.user_data['wd_rekening'] = text
        context.user_data['mode'] = 'WAITING_AN'

        pesan = (
            f"👤 *INPUT NAMA ATAS NAMA (A/N)*\n"
            f"═══════════════════════\n"
            f"📌 *No Rek/HP:* `{text}`\n\n"
            f"Silakan *ketik dan kirimkan Nama Pemilik (Atas Nama)* dari rekening/e-wallet kamu:"
        )
        await update.message.reply_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')
        return

    elif current_mode == 'WAITING_AN':
        atas_nama = text
        rekening = context.user_data.get('wd_rekening', '-')
        nominal = context.user_data.get('wd_nominal', 0)
        metode = context.user_data.get('wd_metode', '-')

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT balance FROM users WHERE user_id = %s', (user.id,))
        res = cursor.fetchone()
        balance = res[0] if res else 0

        if balance < nominal:
            await update.message.reply_text("❌ Saldo tidak mencukupi untuk melakukan penarikan ini.", reply_markup=main_menu_keyboard(user.id))
            context.user_data.clear()
            cursor.close()
            conn.close()
            return

        now_str = get_wib_time()
        cursor.execute('UPDATE users SET balance = balance - %s WHERE user_id = %s', (nominal, user.id))
        cursor.execute('INSERT INTO withdrawals (user_id, nominal, metode, rekening, atas_nama, created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id', 
                       (user.id, nominal, metode, rekening, atas_nama, now_str))
        wd_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()

        username_txt = f"@{user.username}" if user.username else "No Username"
        keyboard_admin = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"✅ Approve #WD{wd_id}", callback_data=f"accwd_{wd_id}"),
                InlineKeyboardButton(f"❌ Reject #WD{wd_id} (Refund)", callback_data=f"rejwd_{wd_id}")
            ]
        ])

        laporan_admin = (
            f"💸 *PERMINTAAN PENARIKAN DANA*\n"
            f"═══════════════════════\n"
            f"🆔 *ID WD:* `#WD{wd_id}`\n"
            f"👤 *Pengirim:* {user.first_name} ({username_txt})\n"
            f"🆔 *ID Telegram:* `{user.id}`\n"
            f"💵 *Nominal:* Rp {nominal:,}\n"
            f"🏦 *Metode:* {metode}\n"
            f"📌 *No Rek/HP:* `{rekening}`\n"
            f"👤 *A/N:* `{atas_nama}`\n"
            f"🕒 *Waktu:* `{now_str}`\n"
            f"═══════════════════════"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=laporan_admin, reply_markup=keyboard_admin, parse_mode='Markdown')
        except Exception:
            pass

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ *PENARIKAN BERHASIL DIAJUKAN!*\n\n"
            f"🆔 ID WD: `#WD{wd_id}`\n"
            f"💵 Nominal: *Rp {nominal:,}*\n"
            f"🏦 Metode: *{metode}*\n"
            f"📌 No Rek/HP: `{rekening}`\n"
            f"👤 Atas Nama: `{atas_nama}`\n\n"
            f"Permintaan penarikan kamu sedang diproses oleh admin. Cek statusnya secara berkala melalui menu *Riwayat Withdraw*.",
            reply_markup=main_menu_keyboard(user.id),
            parse_mode='Markdown'
        )
        return

    if not current_mode:
        await update.message.reply_text(
            "⚠️ Silakan pilih tombol **Setor Satuan** atau **Setor Bulking** terlebih dahulu pada menu utama.",
            reply_markup=main_menu_keyboard(user.id),
            parse_mode='Markdown'
        )
        return

    items_to_process = []
    is_bulking_mode = (current_mode == 'BULKING_INPUT_EMAILS')
    bulk_password_used = context.user_data.get('bulk_password') if is_bulking_mode else None
    bulk_price_used = context.user_data.get('bulk_price', 4000) if is_bulking_mode else 4000

    active_pwds = get_active_passwords()

    if is_bulking_mode and bulk_password_used not in active_pwds:
        await update.message.reply_text(f"❌ Sandi `{bulk_password_used}` sedang dinonaktifkan oleh Admin!", reply_markup=main_menu_keyboard(user.id))
        context.user_data.clear()
        return

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        extracted_email = ""
        extracted_pwd = ""

        if ':' in line_clean:
            parts = line_clean.split(':', 1)
            extracted_email = parts[0].strip().lower()
            extracted_pwd = parts[1].strip()
        else:
            extracted_email = line_clean.strip().lower()

        if extracted_email.endswith('@gmail.com') and len(extracted_email) > 10:
            if current_mode == 'SATUAN':
                if extracted_pwd in active_pwds:
                    items_to_process.append((extracted_email, extracted_pwd, active_pwds[extracted_pwd]))
            elif is_bulking_mode:
                items_to_process.append((extracted_email, bulk_password_used, bulk_price_used))

    total_input_count = len(items_to_process)
    if is_bulking_mode and total_input_count > MAX_BULK_LIMIT:
        items_to_process = items_to_process[:MAX_BULK_LIMIT]

    if items_to_process:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING', (user.id, user.username))
        
        inserted_count = 0
        duplicate_count = 0
        total_batch_price = 0
        successfully_inserted_accounts = []
        seen_batch_emails = set()

        for gmail, password, price_val in items_to_process:
            gmail_clean = gmail.strip().lower()

            if gmail_clean in seen_batch_emails:
                duplicate_count += 1
                continue
            seen_batch_emails.add(gmail_clean)

            cursor.execute('SELECT id FROM deposits WHERE TRIM(LOWER(gmail)) = TRIM(LOWER(%s))', (gmail_clean,))
            if cursor.fetchone():
                duplicate_count += 1
                continue

            now_str = get_wib_time()

            try:
                cursor.execute("INSERT INTO deposits (user_id, gmail, password, status, created_at, price) VALUES (%s, %s, %s, 'PENDING', %s, %s)", 
                               (user.id, gmail_clean, password, now_str, price_val))
                inserted_count += 1
                total_batch_price += price_val
                successfully_inserted_accounts.append((gmail_clean, password, now_str, price_val))
            except psycopg2.IntegrityError:
                conn.rollback()
                duplicate_count += 1
                cursor = conn.cursor()
                continue

        conn.commit()
        cursor.close()
        conn.close()
        context.user_data.clear()

        if inserted_count > 0:
            username_txt = f"@{user.username}" if user.username else f"User_{user.id}"
            mode_label = "BULKING" if is_bulking_mode else "SATUAN"
            
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT gmail, password, status, created_at, price FROM deposits WHERE user_id = %s ORDER BY id ASC", (user.id,))
            user_all_deposits = cursor.fetchall()
            cursor.close()
            conn.close()

            pending_list = []
            proc_list = []
            approved_list = []
            rejected_list = []

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
                elif st == 'REJECTED':
                    rejected_list.append(formatted_item)
            
            tgl_hari_ini = datetime.now(timezone(timedelta(hours=7))).strftime('%d-%m-%Y')

            txt_lines = [
                f"📅 TANGGAL SETOR: {tgl_hari_ini}",
                f"👤 USER: {username_txt} (ID: {user.id})",
                f"📦 TOTAL AKUN BARU SETOR: {inserted_count} Akun",
                "--------------------------------------------------",
                f"Gmail Pending (Belum Rekap) ({len(pending_list)}) :"
            ]
            if pending_list:
                txt_lines.extend(pending_list)
            else:
                txt_lines.append("-")

            txt_lines.append("")
            txt_lines.append(f"Gmail Processing (Sudah Rekap) ({len(proc_list)}) :")
            if proc_list:
                txt_lines.extend(proc_list)
            else:
                txt_lines.append("-")

            txt_lines.append("")
            txt_lines.append(f"Gmail Approved (Saldo Masuk) ({len(approved_list)}) :")
            if approved_list:
                txt_lines.extend(approved_list)
            else:
                txt_lines.append("-")

            txt_lines.append("")
            txt_lines.append(f"Gmail Rejected ({len(rejected_list)}) :")
            if rejected_list:
                txt_lines.extend(rejected_list)
            else:
                txt_lines.append("-")

            txt_lines.append("--------------------------------------------------")
            txt_lines.append(f"📊 TOTAL KESELURUHAN SETORAN USER DI DATABASE: {len(user_all_deposits)} Akun")

            txt_content = "\n".join(txt_lines)
            txt_file = io.BytesIO(txt_content.encode('utf-8'))
            
            timestamp_file = datetime.now(timezone(timedelta(hours=7))).strftime("%Y%m%d_%H%M%S")
            filename = f"setoran_{user.id}_{timestamp_file}.txt"

            laporan_admin_text = (
                f"📥 *SETORAN {mode_label} BARU MASUK*\n"
                f"═══════════════════════\n"
                f"👤 *User:* {user.first_name} ({username_txt})\n"
                f"📦 *Akun Baru Masuk:* `{inserted_count}` Akun\n"
                f"💰 *Estimasi Nilai:* Rp {total_batch_price:,}\n"
                f"═══════════════════════\n"
                f"📄 *File .txt di atas berisi seluruh daftar setoran aktif & riwayat lengkap user tanpa ada yang hilang.*"
            )

            try:
                await context.bot.send_document(
                    chat_id=ADMIN_CHAT_ID,
                    document=txt_file,
                    filename=filename,
                    caption=laporan_admin_text,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Buka Panel Admin", callback_data="admin_panel")]]),
                    parse_mode='Markdown'
                )
            except Exception as e:
                print(f"Gagal mengirim dokumen setoran ke admin: {e}")
        
        msg_response = ""
        if inserted_count > 0:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), COALESCE(SUM(price), 0) FROM deposits WHERE user_id = %s AND status IN ('PENDING', 'PROCESSING')", (user.id,))
            row_act = cursor.fetchone()
            total_active_count = row_act[0]
            total_active_est = row_act[1]
            cursor.close()
            conn.close()

            msg_response += (
                f"✅ *AKUN BERHASIL TERKIRIM & DIARKIBKAN!*\n\n"
                f"📩 Total `{inserted_count}` akun baru ditambahkan (Status: Pending).\n"
                f"📂 Total akumulasi akun aktif (Pending + Processing) Anda: `{total_active_count}` akun.\n"
                f"⏳ Estimasi saldo tertahan: *Rp {total_active_est:,}*\n"
            )
        if duplicate_count > 0:
            msg_response += f"\n⚠️ *AUTO REJECT:* `{duplicate_count}` akun ditolak otomatis oleh sistem karena akun/Gmail tersebut sudah pernah terdaftar di database (Anti-Kecurangan)."

        if is_bulking_mode and total_input_count > MAX_BULK_LIMIT:
            msg_response += (
                f"\n\n📌 *Catatan Batas Bulking:*\n"
                f"Teks yang Anda kirim berisi {total_input_count} akun valid. Bot secara otomatis memproses *{MAX_BULK_LIMIT} akun pertama* agar bot tidak macet.\n"
                f"Silakan klik **📦 Setor Bulking** kembali untuk menyetor sisa akun berikutnya."
            )

        await update.message.reply_text(
            msg_response,
            reply_markup=main_menu_keyboard(user.id),
            parse_mode='Markdown'
        )
    else:
        allowed_str = ", ".join([f"`{p}`" for p in active_pwds.keys()]) if active_pwds else "Tidak ada"
        error_msg = (
            f"❌ *FORMAT LIST / SANDI TIDAK VALID!*\n\n"
            f"⚠️ Pastikan sandi yang digunakan sesuai dengan daftar sandi yang **aktif** hari ini: {allowed_str}.\n\n"
            f"🌐 Silakan cek terlebih dahulu di web *netnit.net*:\n"
            f"1. Masuk dan lakukan **Quick Fix Issue** pada akun Anda.\n"
            f"2. Pastikan status akun di sana sudah **Wajib GOOD semua** sebelum disetor ulang ke bot ini!"
        )
        await update.message.reply_text(
            error_msg,
            reply_markup=cancel_keyboard(),
            parse_mode='Markdown'
        )

async def post_init(application):
    init_db()
    await application.bot.set_my_commands([
        BotCommand("start", "🔄 Tampilkan Menu Utama / Refresh Bot")
    ])

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler((filters.TEXT | filters.Document.ALL) & ~filters.COMMAND, handle_message))

    print("Bot Setoran V30 Aktif (System Auto Reject Fraud & Dynamic Price Activated)...")
    app.run_polling()
