"""
Telegram Bot Service
====================
Xodimlar kelganda/ketganda guruhga xabar yuboradi.
"""

import os
import logging
import requests
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

TASHKENT_TZ = pytz.timezone('Asia/Tashkent')


def get_bot_token():
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN topilmadi")
    return token


def send_message(chat_id: str, text: str, parse_mode: str = 'HTML') -> bool:
    """Telegram guruhga xabar yuborish"""
    token = get_bot_token()
    if not token:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {'chat_id': str(chat_id), 'text': text, 'parse_mode': parse_mode}
        logger.info(f"Telegram xabar yuborilmoqda: chat_id={chat_id}")
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get('ok'):
            logger.info(f"Telegram OK: chat_id={chat_id}")
            return True
        else:
            logger.error(f"Telegram API xato: {data.get('description')} chat_id={chat_id}")
            return False
    except Exception as e:
        logger.error(f"Telegram xato: {e}")
        return False


def _get_settings(company_id: str):
    """Har safar yangi DB session bilan TelegramSettings olish"""
    try:
        from database import get_db, TelegramSettings
        db = get_db()
        try:
            s = db.query(TelegramSettings).filter_by(company_id=str(company_id)).first()
            logger.info(f"[TG] Settings query: company_id={company_id}, found={s is not None}")
            if not s:
                logger.warning(f"[TG] telegram_settings jadvali bo'sh yoki company_id mos kelmadi: {company_id}")
                return None
            logger.info(f"[TG] is_enabled={s.is_enabled}, group_chat_id={s.group_chat_id}, notify_checkin={s.notify_checkin}, notify_late={s.notify_late}")
            if not s.is_enabled:
                logger.warning(f"[TG] Bot o'chirilgan (is_enabled=False). Telegram sahifasidan yoqing!")
                return None
            if not s.group_chat_id:
                logger.warning(f"[TG] group_chat_id bo'sh. Telegram sahifasidan guruh ID kiriting!")
                return None
            return s
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[TG] _get_settings XATO: {e}", exc_info=True)
        return None


def _fmt_time(dt):
    if not dt:
        return datetime.now(TASHKENT_TZ).strftime('%H:%M')
    try:
        if dt.tzinfo is None:
            dt = TASHKENT_TZ.localize(dt)
        return dt.astimezone(TASHKENT_TZ).strftime('%H:%M')
    except Exception:
        return '—'


def _fmt_date(dt):
    if not dt:
        return datetime.now(TASHKENT_TZ).strftime('%d.%m.%Y')
    try:
        if dt.tzinfo is None:
            dt = TASHKENT_TZ.localize(dt)
        return dt.astimezone(TASHKENT_TZ).strftime('%d.%m.%Y')
    except Exception:
        return '—'


def notify_checkin(company_id: str, employee_name: str, late_minutes: int,
                   check_in_time, dept: str = '', position: str = '',
                   penalty_amount: float = 0.0, late_count_month: int = 0):
    """Xodim kelganida guruhga xabar"""
    try:
        settings = _get_settings(company_id)
        if not settings:
            return False

        late = late_minutes or 0
        time_str = _fmt_time(check_in_time)
        date_str = _fmt_date(check_in_time)
        sub = ' | '.join(filter(None, [dept, position]))

        if late > 0:
            if not settings.notify_late:
                logger.info("notify_late=False, o'tkazildi")
                return False

            # Oylik kechikish soni badge
            count_badge = ''
            if late_count_month == 1:
                count_badge = ' (bu oy 1-marta)'
            elif late_count_month == 2:
                count_badge = ' (bu oy 2-marta)'
            elif late_count_month >= 3:
                count_badge = f' (bu oy {late_count_month}-marta)'

            text = (
                f"⚠️ <b>KECHIKDI</b>{count_badge}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>{employee_name}</b>\n"
            )
            if sub:
                text += f"🏢 {sub}\n"
            text += f"🕐 Keldi: <b>{time_str}</b>  ({date_str})\n"
            text += f"⏱ Kechikish: <b>{late} daqiqa</b>\n"
            if penalty_amount > 0:
                text += f"💸 Jarima: <b>{penalty_amount:,.0f} so'm</b>\n"
            text += "━━━━━━━━━━━━━━━━━━"

        else:
            if not settings.notify_checkin:
                logger.info("notify_checkin=False, o'tkazildi")
                return False
            text = (
                f"✅ <b>KELDI</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>{employee_name}</b>\n"
            )
            if sub:
                text += f"🏢 {sub}\n"
            text += (
                f"🕐 Vaqt: <b>{time_str}</b>  ({date_str})\n"
                f"━━━━━━━━━━━━━━━━━━"
            )

        return send_message(settings.group_chat_id, text)

    except Exception as e:
        logger.error(f"notify_checkin xato: {e}", exc_info=True)
        return False


def notify_checkout(company_id: str, employee_name: str, check_out_time,
                    total_work_minutes: int = 0, early_leave_minutes: int = 0):
    """Xodim ketganida guruhga xabar"""
    try:
        settings = _get_settings(company_id)
        if not settings:
            return False
        if not settings.notify_checkout:
            return False

        time_str = _fmt_time(check_out_time)
        mins = total_work_minutes or 0
        early = early_leave_minutes or 0
        early_text = f"\n⚠️ Erta ketish: <b>{early} daqiqa</b>" if early > 0 else ""

        text = (
            f"🚪 <b>KETDI</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>{employee_name}</b>\n"
            f"🕐 Ketdi: <b>{time_str}</b>\n"
            f"⏳ Ish vaqti: <b>{mins//60}s {mins%60}d</b>"
            f"{early_text}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        return send_message(settings.group_chat_id, text)

    except Exception as e:
        logger.error(f"notify_checkout xato: {e}", exc_info=True)
        return False


def get_or_create_telegram_settings(company_id: str, db_session):
    from database import TelegramSettings
    import uuid
    s = db_session.query(TelegramSettings).filter_by(company_id=str(company_id)).first()
    if not s:
        s = TelegramSettings(
            id=str(uuid.uuid4()),
            company_id=str(company_id),
            is_enabled=False,
            notify_checkin=True,
            notify_checkout=False,
            notify_late=True,
            notify_absent=False,
        )
        db_session.add(s)
        db_session.flush()
    return s
