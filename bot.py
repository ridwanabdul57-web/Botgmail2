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

# Helper untuk mendapatkan waktu WIB yang akurat
def get_wib_time():
    wib_timezone = timezone(timedelta(hours=7))
    return datetime.now(wib_timezone).strftime("%d-%m-%Y %H:%M:%S WIB")

# Master daftar semua sandi yang dikelola bot beserta status default-nya
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

    return (
        f"✨ *SELAMAT DATANG DI BOT SETORAN GMAIL V30* ✨\n"
        f"Halo *{first_name}*! Silakan baca informasi & aturan setoran di bawah ini:\n\n"
        f"💵 *INFORMASI RATE & PROSES*\n"
        f"• *Rate Per Akun:* Rp 4.000\n"
        f"• *Estimasi Pengecekan:* 24 - 48 Jam Kerja\n"
        f"• *Batas Bulking:* Maksimal {MAX_BULK_LIMIT} akun / setor\n\n"
        f"🔑 *ATURAN KATA SANDI (PASSWORD AKTIF)*\n"
        f"• Password yang valid hari ini: {pwd_str}\n\n"
        f"⚠️ *SYARAT & KETENTUAN WAJIB*\n"
        f"1. *Dilarang Double-Sell:* Dilarang keras menyetor Gmail duplikat yang sudah pernah terdaftar di bot.\n"
        f"2. *Nomor HP Pemulihan:* Wajib dikosongkan / jangan diverifikasi.\n"
        f"3. *Kondisi Akun:* Akun langsung menampilkan opsi sandi (Good), bukan captcha.\n\n"
        f"👇 *Pilih menu di bawah ini untuk memulai:* "
    )

# ----------------- ADMIN PANEL LAYOUT -----------------
async def render_admin_panel(query_or_update):
    statuses = get_all_password_statuses()
    keyboard = []
    for pwd, st in statuses.items():
        icon = "🟢 ACTIVE" if st == "ACTIVE" else "🔴 INACTIVE"
        keyboard.append([InlineKeyboardButton(f"Sandi '{pwd}': {icon}", callback_data=f"admin_toggle_pwd_{pwd}")])
    
    keyboard.append([InlineKeyboardButton("📄 Export Laporan Realtime TXT", callback_data="admin_export_all_deposits")])
    keyboard.append([InlineKeyboardButton("« Kembali ke Menu Utama", callback_data="menu_utama")])

    pesan = (
        "⚙️ *PANEL KONTROL ADMIN*\n"
        "═══════════════════════\n"
        "Atur status password aktif & unduh laporan setoran realtime di bawah ini:"
    )

    if hasattr(query_or_update, 'edit_message_text'):
        await query_or_update.edit_message_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await query_or_update.message.reply_text(pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

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

    elif data.startswith("admin_toggle_pwd_"):
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        target_pwd = data.replace('admin_toggle_pwd_', '')
        statuses = get_all_password_statuses()
        
        if target_pwd in statuses:
            new_st = 'INACTIVE' if statuses[target_pwd] == 'ACTIVE' else 'ACTIVE'
            set_password_status(target_pwd, new_st)
            await query.answer(f"✅ Sandi `{target_pwd}` diubah menjadi {new_st}!", show_alert=True)

        await render_admin_panel(query)

    elif data == "admin_export_all_deposits":
        if user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Akses khusus Admin!", show_alert=True)
            return

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT d.user_id, u.username, d.gmail, d.password, d.status, d.created_at 
            FROM deposits d 
            LEFT JOIN users u ON d.user_id = u.user_id 
            ORDER BY d.id ASC
        ''')
        all_deposits = cursor.fetchall()
        cursor.close()
        conn.close()

        if not all_deposits:
            await query.answer("⚠️ Belum ada riwayat setoran sama sekali di database.", show_alert=True)
            return

        wib_now_date = datetime.now(timezone(timedelta(hours=7))).strftime('%Y%m%d_%H%M%S')
        txt_lines = []
        txt_lines.append("==================================================")
        txt_lines.append(f" LAPORAN REALTIME SETORAN GMAIL - {get_wib_time()}")
        txt_lines.append("==================================================\n")

        current_user = None
        setoran_index = 1

        for uid, uname, gmail, pwd, status, dt in all_deposits:
            user_tag = f"@{uname}" if uname else f"User_ID:{uid}"
            
            # Memisahkan grup jika user berganti
            if current_user != uid:
                if current_user is not None:
                    txt_lines.append("\n" + "-"*50 + "\n")
                current_user = uid
                txt_lines.append(f"📌 PENYETOR: {user_tag} (ID: {uid})")
                txt_lines.append("--------------------------------------------------")
                setoran_index = 1

            time_str = dt if dt else "Waktu Tidak Terdata"
            txt_lines.append(f"{setoran_index}. {gmail}:{pwd} | Status: {status} | Jam: {time_str}")
            setoran_index += 1

        txt_lines.append("\n==================================================")
        txt_lines.append(f" TOTAL EMAIL TERDATAR: {len(all_deposits)} AKUN")
        txt_lines.append("==================================================")

        report_content = "\n".join(txt_lines)
        file_bytes = io.BytesIO(report_content.encode('utf-8'))
        file_bytes.name = f"Laporan_Realtime_Setoran_{wib_now_date}.txt"

        await context.bot.send_document(
            chat_id=user.id,
            document=file_bytes,
            caption=f"📄 *LAPORAN REALTIME SETORAN GMAIL*\n\n📅 *Waktu Cetak:* `{get_wib_time()}`\n📊 *Total Akun Aktif dalam Laporan:* {len(all_deposits)} Akun",
            parse_mode='Markdown'
        )

# ----------------- INPUT PROCESSOR -----------------
async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    text = update.message.text.strip() if update.message.text else ""
    
    # 1. Handling Reply Keyboard Button Cepat
    if text == "🔄 Refresh / Start":
        await start(update, context)
        return
    elif text == "📜 Daftar Setoran Saya":
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
                
                waktu = dt if dt else get_wib_time()
                pesan += f"📧 `{g_mail}`\n└ Status: *{status_icon}*\n└ Waktu: `{waktu}`\n\n"

        await update.message.reply_text(pesan, reply_markup=back_keyboard(), parse_mode='Markdown')
        return
    elif text == "💰 Cek Saldo":
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
        await update.message.reply_text(pesan, reply_markup=back_keyboard(), parse_mode='Markdown')
        return
    elif text == "💬 Hubungi CS":
        await update.message.reply_text(f"💬 Silakan hubungi Customer Service di Telegram: @{CS_USERNAME}")
        return

    mode = context.user_data.get('mode')

    if not mode:
        return

    # 2. Handling Input Penarikan Dana (Rekening/E-Wallet)
    if mode == 'WAITING_REK':
        context.user_data['wd_rekening'] = text
        context.user_data['mode'] = 'WAITING_AN'
        
        pesan = (
            f"👤 *INPUT ATAS NAMA (A/N)*\n"
            f"═══════════════════════\n"
            f"Silakan ketik dan kirimkan *Nama Pemilik Rekening / Akun E-Wallet*:"
        )
        await update.message.reply_text(pesan, reply_markup=cancel_keyboard(), parse_mode='Markdown')
        return

    elif mode == 'WAITING_AN':
        atas_nama = text
        nominal = context.user_data.get('wd_nominal', 0)
        metode = context.user_data.get('wd_metode', '-')
        rekening = context.user_data.get('wd_rekening', '-')

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT balance FROM users WHERE user_id = %s', (user.id,))
        res = cursor.fetchone()
        current_balance = res[0] if res else 0

        if current_balance < nominal:
            await update.message.reply_text("❌ Saldo tidak mencukupi untuk melakukan transaksi ini!", reply_markup=back_keyboard())
            context.user_data.clear()
            cursor.close()
            conn.close()
            return

        cursor.execute('UPDATE users SET balance = balance - %s WHERE user_id = %s', (nominal, user.id))
        
        wib_now = get_wib_time()
        cursor.execute('''
            INSERT INTO withdrawals (user_id, nominal, metode, rekening, atas_nama, status, created_at)
            VALUES (%s, %s, %s, %s, %s, 'PENDING', %s) RETURNING id
        ''', (user.id, nominal, metode, rekening, atas_nama, wib_now))
        wd_id = cursor.fetchone()[0]

        conn.commit()
        cursor.close()
        conn.close()

        context.user_data.clear()

        # Alert Pengguna
        await update.message.reply_text(
            f"✅ *PERMOHONAN PENARIKAN BERHASIL DIBUAT*\n"
            f"═══════════════════════\n"
            f"🆔 *ID Transaksi:* `#WD{wd_id}`\n"
            f"💵 *Nominal Pencairan:* Rp {nominal:,}\n"
            f"🏦 *Metode:* {metode}\n"
            f"🔢 *No. Rek/HP:* `{rekening}`\n"
            f"👤 *Atas Nama:* `{atas_nama}`\n"
            f"🕒 *Waktu:* `{wib_now}`\n\n"
            f"📌 _Pencairan kamu sedang diverifikasi dan diproses oleh Admin._",
            reply_markup=back_keyboard(),
            parse_mode='Markdown'
        )

        # Alert Admin
        user_tag = f"@{user.username}" if user.username else f"User {user.id}"
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                f"🚨 *PERMOHONAN PENARIKAN DANA BARU!*\n"
                f"═══════════════════════\n"
                f"🆔 *ID WD:* `#WD{wd_id}`\n"
                f"👤 *Penyetor:* {user_tag} (`{user.id}`)\n"
                f"💵 *Nominal:* Rp {nominal:,}\n"
                f"🏦 *Metode:* {metode}\n"
                f"🔢 *No. Rek/HP:* `{rekening}`\n"
                f"👤 *A/N:* `{atas_nama}`\n"
                f"🕒 *Waktu:* `{wib_now}`"
            ),
            parse_mode='Markdown'
        )
        return

    # 3. Handling Setoran Gmail (Satuan & Bulking)
    raw_text = ""
    if update.message.document:
        doc = update.message.document
        if not doc.file_name.endswith('.txt'):
            await update.message.reply_text("❌ Mohon kirimkan file dengan format `.txt` saja!", reply_markup=cancel_keyboard())
            return
        
        file_obj = await context.bot.get_file(doc.file_id)
        downloaded = await file_obj.download_as_bytearray()
        raw_text = downloaded.decode('utf-8', errors='ignore')
    elif update.message.text:
        raw_text = update.message.text

    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]

    if not lines:
        await update.message.reply_text("⚠️ Data yang kamu kirimkan kosong atau format tidak terbaca.", reply_markup=cancel_keyboard())
        return

    allowed_pwds = get_current_allowed_passwords()
    if not allowed_pwds:
        await update.message.reply_text("❌ Tidak ada password yang diaktifkan oleh admin saat ini. Setoran dibatalkan.", reply_markup=back_keyboard())
        context.user_data.clear()
        return

    valid_deposits = []
    failed_logs = []

    conn = get_db()
    cursor = conn.cursor()

    if mode == 'SATUAN':
        for idx, line in enumerate(lines, 1):
            if ":" not in line:
                failed_logs.append(f"Baris {idx}: Format salah (Wajib `email:password`) -> `{line}`")
                continue

            parts = line.split(":", 1)
            gmail = parts[0].strip().lower()
            pwd = parts[1].strip()

            if not gmail.endswith("@gmail.com"):
                failed_logs.append(f"Baris {idx}: Email bukan @gmail.com -> `{line}`")
                continue

            if pwd not in allowed_pwds:
                failed_logs.append(f"Baris {idx}: Password `{pwd}` tidak aktif/invalid -> `{line}`")
                continue

            cursor.execute("SELECT id FROM deposits WHERE TRIM(LOWER(gmail)) = %s", (gmail,))
            if cursor.fetchone():
                failed_logs.append(f"Baris {idx}: Email sudah pernah terdaftar di bot (Duplikat) -> `{gmail}`")
                continue

            valid_deposits.append((gmail, pwd))

    elif mode == 'BULKING_INPUT_EMAILS':
        bulk_pwd = context.user_data.get('bulk_password')
        if bulk_pwd not in allowed_pwds:
            await update.message.reply_text(f"❌ Sandi `{bulk_pwd}` telah dinonaktifkan Admin! Setoran dibatalkan.", reply_markup=back_keyboard())
            context.user_data.clear()
            cursor.close()
            conn.close()
            return

        if len(lines) > MAX_BULK_LIMIT:
            await update.message.reply_text(
                f"❌ Jumlah baris setoran melebihi batas maksimal bulking!\n"
                f"• Batas Maksimal: *{MAX_BULK_LIMIT} Akun*\n"
                f"• Kiriman Kamu: *{len(lines)} Baris*\n\n"
                f"Silakan kurangi jumlah baris dan setor ulang.",
                reply_markup=cancel_keyboard(),
                parse_mode='Markdown'
            )
            cursor.close()
            conn.close()
            return

        for idx, line in enumerate(lines, 1):
            if ":" in line:
                gmail = line.split(":", 1)[0].strip().lower()
            else:
                gmail = line.strip().lower()

            if not gmail.endswith("@gmail.com"):
                failed_logs.append(f"Baris {idx}: Bukan email @gmail.com -> `{line}`")
                continue

            cursor.execute("SELECT id FROM deposits WHERE TRIM(LOWER(gmail)) = %s", (gmail,))
            if cursor.fetchone():
                failed_logs.append(f"Baris {idx}: Email duplikat/sudah ada di database -> `{gmail}`")
                continue

            valid_deposits.append((gmail, bulk_pwd))

    # Masukkan Data Valid ke Database
    wib_now = get_wib_time()
    for g, p in valid_deposits:
        cursor.execute(
            "INSERT INTO deposits (user_id, gmail, password, status, created_at) VALUES (%s, %s, %s, 'PENDING', %s)",
            (user.id, g, p, wib_now)
        )

    conn.commit()
    cursor.close()
    conn.close()

    context.user_data.clear()

    # Kirim Laporan ke Penyetor
    pesan_hasil = f"📥 *LAPORAN PENERIMAAN SETORAN GMAIL*\n═══════════════════════\n"
    pesan_hasil += f"✅ *Berhasil Disetor:* {len(valid_deposits)} Akun\n"
    pesan_hasil += f"❌ *Gagal / Ditolak:* {len(failed_logs)} Baris\n"
    pesan_hasil += f"🕒 *Waktu Setor:* `{wib_now}`\n═══════════════════════\n"

    if valid_deposits:
        pesan_hasil += "\n📋 *Daftar Gmail Disetor:*\n"
        for g, p in valid_deposits:
            pesan_hasil += f"• `{g}:{p}`\n"

    if failed_logs:
        pesan_hasil += "\n⚠️ *Detail Baris Gagal:*\n"
        for fl in failed_logs[:10]: # Tampilkan max 10 log gagal
            pesan_hasil += f"• {fl}\n"
        if len(failed_logs) > 10:
            pesan_hasil += f"• _...dan {len(failed_logs) - 10} baris gagal lainnya._\n"

    await update.message.reply_text(pesan_hasil, reply_markup=back_keyboard(), parse_mode='Markdown')

    # Notifikasi Setoran Baru ke Admin
    if valid_deposits:
        user_tag = f"@{user.username}" if user.username else f"User_ID:{user.id}"
        admin_alert = (
            f"🔔 *SETORAN GMAIL BARU MASUK!*\n"
            f"═══════════════════════\n"
            f"👤 *Penyetor:* {user_tag} (`{user.id}`)\n"
            f"📦 *Jumlah Akun:* {len(valid_deposits)} Akun\n"
            f"🕒 *Waktu:* `{wib_now}`\n"
            f"═══════════════════════\n"
            f"💡 _Gunakan menu Admin Panel untuk mengekspor data realtime._"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_alert, parse_mode='Markdown')
        except Exception:
            pass

# ----------------- MAIN APP INITIALIZATION -----------------
def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.MimeType("text/plain"), handle_message_input))

    print("🤖 Bot Telegram V30 Berhasil Dijalankan...")
    app.run_polling()

if __name__ == '__main__':
    main()
