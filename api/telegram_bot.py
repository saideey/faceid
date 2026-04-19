"""
Telegram Bot Handler
====================
Xodimlar uchun self-service bot:
  /start  — Botni ishga tushirish, telefon raqami orqali tasdiqlash
  /men    — Mening bugungi davomatim
  /oylik  — Joriy oy statistikasi
  /yordam — Yordam

Ishga tushirish:
  python telegram_bot.py

Docker uchun alohida service sifatida ishlatiladi.
"""

import os
import sys
import logging
import asyncio
from datetime import datetime, date, timedelta

import pytz
from dotenv import load_dotenv

load_dotenv()

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TASHKENT_TZ = pytz.timezone('Asia/Tashkent')

try:
    from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        filters, ContextTypes, ConversationHandler
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    logger.error("python-telegram-bot o'rnatilmagan!")
    TELEGRAM_AVAILABLE = False

# Conversation states
WAITING_PHONE = 1

# ──────────────────────────────────────────────────────
# DB YORDAMCHI FUNKSIYALAR
# ──────────────────────────────────────────────────────

def get_db_session():
    """Database session olish"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database import get_db
    return get_db()


def find_employee_by_phone(phone: str, db):
    """Telefon raqam bo'yicha xodimni topish"""
    from database import Employee
    clean = phone.replace('+', '').replace(' ', '').replace('-', '')
    if len(clean) < 9:
        return None
    suffix = clean[-9:]

    employees = db.query(Employee).filter(
        Employee.status == 'active'
    ).all()

    for emp in employees:
        if not emp.phone:
            continue
        emp_clean = emp.phone.replace('+', '').replace(' ', '').replace('-', '')
        if len(emp_clean) < 9:
            continue
        if emp_clean[-9:] == suffix:
            return emp
    return None


def get_or_create_telegram_user(telegram_user_id: str, db):
    """TelegramUser olish"""
    from database import TelegramUser
    return db.query(TelegramUser).filter_by(telegram_user_id=str(telegram_user_id)).first()


# ──────────────────────────────────────────────────────
# HANDLERS
# ──────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start — Botni boshlash.
    Agar xodim allaqachon tasdiqlanmagan bo'lsa, telefon so'raydi.
    """
    user = update.effective_user
    db = get_db_session()

    try:
        tg_user = get_or_create_telegram_user(str(user.id), db)

        if tg_user and tg_user.is_verified:
            emp = tg_user.employee
            await update.message.reply_text(
                f"👋 Salom, <b>{emp.full_name}</b>!\n\n"
                f"Quyidagi buyruqlardan foydalaning:\n"
                f"📋 /men — Bugungi davomatim\n"
                f"📊 /oylik — Joriy oy statistikasi\n"
                f"📅 /hafta — Bu hafta davomati\n"
                f"❓ /yordam — Yordam",
                parse_mode='HTML'
            )
            return ConversationHandler.END

        # Tasdiqlash kerak — telefon raqam so'rash
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Telefon raqamimni ulashish", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await update.message.reply_text(
            f"👋 Salom, <b>{user.first_name}</b>!\n\n"
            f"Bu bot orqali o'z davomat ma'lumotlaringizni ko'rishingiz mumkin.\n\n"
            f"🔐 Avval <b>telefon raqamingizni</b> ulashing — "
            f"tizimda ro'yxatdan o'tganingizni tasdiqlash uchun:",
            parse_mode='HTML',
            reply_markup=keyboard
        )
        return WAITING_PHONE

    finally:
        db.close()


async def receive_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Foydalanuvchi telefon raqamini ulashdi.
    Telegram kontakt tugmasi orqali kelgan telefon Telegram tomonidan
    tasdiqlangan bo'ladi — qo'shimcha kod kerak emas.
    """
    contact = update.message.contact
    user = update.effective_user

    if not contact:
        await update.message.reply_text(
            "❌ Iltimos, <b>Telefon raqamimni ulashish</b> tugmasini bosing.",
            parse_mode='HTML'
        )
        return WAITING_PHONE

    # Faqat o'z raqamini ulashganiga ishonch hosil qilish
    if contact.user_id and contact.user_id != user.id:
        await update.message.reply_text(
            "❌ Iltimos, <b>o'zingizning</b> telefon raqamingizni ulashing "
            "(boshqa odamning kontaktini emas).",
            parse_mode='HTML'
        )
        return WAITING_PHONE

    phone = contact.phone_number
    db = get_db_session()

    try:
        employee = find_employee_by_phone(phone, db)

        if not employee:
            await update.message.reply_text(
                f"❌ <b>Topilmadi</b>\n\n"
                f"<code>{phone}</code> raqami tizimda ro'yxatdan o'tmagan.\n\n"
                f"Iltimos, HR bo'limiga murojaat qiling.",
                parse_mode='HTML',
                reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END

        from database import TelegramUser
        import uuid

        tg_user = db.query(TelegramUser).filter_by(telegram_user_id=str(user.id)).first()
        if tg_user:
            tg_user.employee_id = employee.id
            tg_user.company_id = employee.company_id
            tg_user.telegram_phone = phone
            tg_user.first_name = user.first_name
            tg_user.telegram_username = user.username
            tg_user.is_verified = True
            tg_user.verification_code = None
            tg_user.verification_expires = None
        else:
            tg_user = TelegramUser(
                id=str(uuid.uuid4()),
                employee_id=employee.id,
                company_id=employee.company_id,
                telegram_user_id=str(user.id),
                telegram_username=user.username,
                telegram_phone=phone,
                first_name=user.first_name,
                is_verified=True,
            )
            db.add(tg_user)

        db.commit()

        await update.message.reply_text(
            f"🎉 <b>Xush kelibsiz, {employee.full_name}!</b>\n\n"
            f"Siz muvaffaqiyatli ulandingiz.\n\n"
            f"Quyidagi buyruqlardan foydalaning:\n"
            f"📋 /men — Bugungi davomatim\n"
            f"📊 /oylik — Joriy oy statistikasi\n"
            f"📅 /hafta — Bu hafta davomati\n"
            f"❓ /yordam — Yordam",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    finally:
        db.close()


async def my_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/men — Bugungi davomat"""
    user = update.effective_user
    db = get_db_session()

    try:
        tg_user = get_or_create_telegram_user(str(user.id), db)
        if not tg_user or not tg_user.is_verified:
            await update.message.reply_text(
                "🔐 Avval /start buyrug'i bilan tizimga kiring."
            )
            return

        from database import AttendanceLog
        today = datetime.now(TASHKENT_TZ).date()

        log = db.query(AttendanceLog).filter_by(
            employee_id=tg_user.employee_id,
            date=today
        ).first()

        emp = tg_user.employee
        date_str = today.strftime('%d.%m.%Y')

        if not log:
            await update.message.reply_text(
                f"📋 <b>Bugungi davomat</b> ({date_str})\n\n"
                f"👤 {emp.full_name}\n\n"
                f"❌ Bugun hali kelmadingiz.",
                parse_mode='HTML'
            )
            return

        # Kirish vaqti
        check_in_str = '—'
        if log.check_in_time:
            ci = log.check_in_time
            if ci.tzinfo is None:
                ci = TASHKENT_TZ.localize(ci)
            else:
                ci = ci.astimezone(TASHKENT_TZ)
            check_in_str = ci.strftime('%H:%M')

        # Chiqish vaqti
        check_out_str = '(hali ketmadingiz)'
        if log.check_out_time:
            co = log.check_out_time
            if co.tzinfo is None:
                co = TASHKENT_TZ.localize(co)
            else:
                co = co.astimezone(TASHKENT_TZ)
            check_out_str = co.strftime('%H:%M')

        # Ish vaqti
        work_str = ''
        if log.total_work_minutes:
            h = log.total_work_minutes // 60
            m = log.total_work_minutes % 60
            work_str = f"\n⏳ Ish vaqti: <b>{h}s {m}d</b>"

        # Kechikish
        late_str = ''
        if log.late_minutes and log.late_minutes > 0:
            late_str = f"\n⚠️ Kechikish: <b>{log.late_minutes} daqiqa</b>"

        # Erta ketish
        early_str = ''
        if log.early_leave_minutes and log.early_leave_minutes > 0:
            early_str = f"\n🚪 Erta ketish: <b>{log.early_leave_minutes} daqiqa</b>"

        status_icon = '✅' if log.late_minutes == 0 else '⚠️'

        await update.message.reply_text(
            f"📋 <b>Bugungi davomat</b> ({date_str})\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>{emp.full_name}</b>\n"
            f"{status_icon} Keldi: <b>{check_in_str}</b>\n"
            f"🚪 Ketdi: <b>{check_out_str}</b>"
            f"{work_str}{late_str}{early_str}\n"
            f"━━━━━━━━━━━━━━━━━━",
            parse_mode='HTML'
        )

    finally:
        db.close()


async def my_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/oylik — Joriy oy statistikasi"""
    user = update.effective_user
    db = get_db_session()

    try:
        tg_user = get_or_create_telegram_user(str(user.id), db)
        if not tg_user or not tg_user.is_verified:
            await update.message.reply_text("🔐 /start buyrug'i bilan kiring.")
            return

        from database import AttendanceLog, Penalty

        now = datetime.now(TASHKENT_TZ)
        start_date = date(now.year, now.month, 1)
        end_date = now.date()

        logs = db.query(AttendanceLog).filter(
            AttendanceLog.employee_id == tg_user.employee_id,
            AttendanceLog.date >= start_date,
            AttendanceLog.date <= end_date
        ).all()

        penalties = db.query(Penalty).filter(
            Penalty.employee_id == tg_user.employee_id,
            Penalty.date >= start_date,
            Penalty.date <= end_date,
            Penalty.is_waived == False,
            Penalty.is_excused == False
        ).all()

        total_days = len(logs)
        late_days = sum(1 for l in logs if l.late_minutes > 0)
        total_late_mins = sum(l.late_minutes or 0 for l in logs)
        total_work_mins = sum(l.total_work_minutes or 0 for l in logs)
        total_penalty = sum(float(p.amount) for p in penalties)
        total_work_h = total_work_mins // 60
        total_work_m = total_work_mins % 60

        emp = tg_user.employee
        month_name = now.strftime('%B %Y')

        await update.message.reply_text(
            f"📊 <b>{month_name} statistikasi</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>{emp.full_name}</b>\n\n"
            f"📅 Kelgan kunlar: <b>{total_days}</b>\n"
            f"⚠️ Kechikkan kunlar: <b>{late_days}</b>\n"
            f"⏱ Jami kechikish: <b>{total_late_mins} daqiqa</b>\n"
            f"⏳ Jami ish vaqti: <b>{total_work_h}s {total_work_m}d</b>\n"
            f"💸 Jarimalar: <b>{total_penalty:,.0f} so'm</b>\n"
            f"━━━━━━━━━━━━━━━━━━",
            parse_mode='HTML'
        )

    finally:
        db.close()


async def my_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hafta — Bu hafta davomati"""
    user = update.effective_user
    db = get_db_session()

    try:
        tg_user = get_or_create_telegram_user(str(user.id), db)
        if not tg_user or not tg_user.is_verified:
            await update.message.reply_text("🔐 /start buyrug'i bilan kiring.")
            return

        from database import AttendanceLog

        today = datetime.now(TASHKENT_TZ).date()
        monday = today - timedelta(days=today.weekday())

        logs = db.query(AttendanceLog).filter(
            AttendanceLog.employee_id == tg_user.employee_id,
            AttendanceLog.date >= monday,
            AttendanceLog.date <= today
        ).order_by(AttendanceLog.date).all()

        day_names = ['Du', 'Se', 'Ch', 'Pa', 'Ju', 'Sh', 'Ya']
        lines = []
        for log in logs:
            day = day_names[log.date.weekday()]
            ci = '—'
            co = '—'
            if log.check_in_time:
                t = log.check_in_time
                if t.tzinfo is None:
                    t = TASHKENT_TZ.localize(t)
                ci = t.astimezone(TASHKENT_TZ).strftime('%H:%M')
            if log.check_out_time:
                t = log.check_out_time
                if t.tzinfo is None:
                    t = TASHKENT_TZ.localize(t)
                co = t.astimezone(TASHKENT_TZ).strftime('%H:%M')
            late_mark = f" ⚠️{log.late_minutes}d" if log.late_minutes and log.late_minutes > 0 else ""
            lines.append(f"{day} {log.date.strftime('%d.%m')}: {ci}→{co}{late_mark}")

        emp = tg_user.employee
        week_text = '\n'.join(lines) if lines else 'Bu hafta ma\'lumot yo\'q'

        await update.message.reply_text(
            f"📅 <b>Bu hafta davomati</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>{emp.full_name}</b>\n\n"
            f"<code>{week_text}</code>\n"
            f"━━━━━━━━━━━━━━━━━━",
            parse_mode='HTML'
        )

    finally:
        db.close()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/yordam"""
    await update.message.reply_text(
        "❓ <b>Yordam</b>\n\n"
        "📋 /men — Bugungi davomat\n"
        "📊 /oylik — Joriy oy statistikasi\n"
        "📅 /hafta — Bu hafta davomati\n"
        "🔄 /start — Qayta ulash\n\n"
        "<i>Muammo bo'lsa, HR bo'limiga murojaat qiling.</i>",
        parse_mode='HTML'
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Bekor qilindi.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


# ──────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────

def main():
    if not TELEGRAM_AVAILABLE:
        logger.error("python-telegram-bot o'rnatilmagan!")
        return

    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN muhit o'zgaruvchisi o'rnatilmagan!")
        return

    logger.info("🤖 Telegram bot ishga tushmoqda...")

    app = Application.builder().token(token).build()

    # Conversation handler — ro'yxatdan o'tish
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            WAITING_PHONE: [
                MessageHandler(filters.CONTACT, receive_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_contact),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler('men', my_today))
    app.add_handler(CommandHandler('oylik', my_monthly))
    app.add_handler(CommandHandler('hafta', my_week))
    app.add_handler(CommandHandler('yordam', help_command))
    app.add_handler(CommandHandler('help', help_command))

    logger.info("✅ Bot tayyor. Polling boshlandi...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
