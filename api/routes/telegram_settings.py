"""
Telegram Bot Settings Routes
/api/telegram/
"""
import os
import requests
import logging
from flask import Blueprint, request, g
from database import get_db, TelegramSettings, TelegramUser
from utils.decorators import company_admin_required
from utils.helpers import success_response, error_response

telegram_bp = Blueprint('telegram', __name__)
logger = logging.getLogger(__name__)


def get_bot_token():
    return os.getenv('TELEGRAM_BOT_TOKEN', '')


def verify_bot_token(token: str) -> dict:
    """Bot tokenni tekshirish"""
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get('ok'):
            return {'valid': True, 'bot': data['result']}
        return {'valid': False, 'error': data.get('description', 'Invalid token')}
    except Exception as e:
        return {'valid': False, 'error': str(e)}


# ── SOZLAMALARNI OLISH / YANGILASH ────────────────────

@telegram_bp.route('/settings', methods=['GET'])
@company_admin_required
def get_telegram_settings():
    """Kompaniyaning Telegram sozlamalarini olish"""
    db = get_db()
    try:
        from services.telegram_service import get_or_create_telegram_settings
        settings = get_or_create_telegram_settings(g.company_id, db)
        db.commit()

        # Global bot token bor-yo'qligini ham qaytaramiz
        token = get_bot_token()
        result = settings.to_dict()
        result['bot_configured'] = bool(token)
        result['bot_token_hint'] = f"...{token[-8:]}" if len(token) > 8 else ('set' if token else 'not set')

        return success_response(result)
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        db.close()


@telegram_bp.route('/settings', methods=['PUT'])
@company_admin_required
def update_telegram_settings():
    """
    Telegram sozlamalarini yangilash.
    Body: {
        "group_chat_id": "-1001234567890",
        "group_name": "Ishchilar guruhi",
        "notify_checkin": true,
        "notify_checkout": false,
        "notify_late": true,
        "notify_absent": false,
        "is_enabled": true
    }
    """
    db = get_db()
    try:
        data = request.get_json()
        if not data:
            return error_response("Request body required", 400)

        from services.telegram_service import get_or_create_telegram_settings
        settings = get_or_create_telegram_settings(g.company_id, db)

        if 'group_chat_id' in data:
            settings.group_chat_id = str(data['group_chat_id']).strip() if data['group_chat_id'] else None
        if 'group_name' in data:
            settings.group_name = data['group_name']
        if 'notify_checkin' in data:
            settings.notify_checkin = bool(data['notify_checkin'])
        if 'notify_checkout' in data:
            settings.notify_checkout = bool(data['notify_checkout'])
        if 'notify_late' in data:
            settings.notify_late = bool(data['notify_late'])
        if 'notify_absent' in data:
            settings.notify_absent = bool(data['notify_absent'])
        if 'is_enabled' in data:
            settings.is_enabled = bool(data['is_enabled'])

        db.commit()
        db.refresh(settings)

        return success_response(settings.to_dict(), message="Telegram sozlamalari saqlandi")
    except Exception as e:
        db.rollback()
        return error_response(str(e), 500)
    finally:
        db.close()


# ── BOT TEKSHIRISH VA TEST ────────────────────────────

@telegram_bp.route('/test-bot', methods=['GET'])
@company_admin_required
def test_bot():
    """Bot token va guruhga ulanishni tekshirish"""
    token = get_bot_token()
    if not token:
        return error_response(
            "TELEGRAM_BOT_TOKEN muhit o'zgaruvchisi o'rnatilmagan. "
            "docker-compose.yml ga TELEGRAM_BOT_TOKEN=... qo'shing.", 400
        )

    result = verify_bot_token(token)
    if not result['valid']:
        return error_response(f"Bot token noto'g'ri: {result['error']}", 400)

    return success_response({
        'bot_valid': True,
        'bot_username': result['bot'].get('username'),
        'bot_name': result['bot'].get('first_name'),
    }, message="Bot ulandi!")


@telegram_bp.route('/test-message', methods=['POST'])
@company_admin_required
def test_message():
    """
    Guruhga test xabar yuborish.
    Body: { "message": "Test xabar" }
    """
    db = get_db()
    try:
        data = request.get_json() or {}
        message = data.get('message', '🔔 Bu test xabari — Davomat tizimi ulandi!')

        settings = db.query(TelegramSettings).filter_by(company_id=g.company_id).first()
        if not settings or not settings.group_chat_id:
            return error_response("Guruh ID sozlanmagan. Avval group_chat_id ni saqlang.", 400)

        from services.telegram_service import send_message
        ok = send_message(settings.group_chat_id, f"🔔 <b>Test xabar</b>\n{message}")

        if ok:
            return success_response({'sent': True}, message="Test xabar yuborildi!")
        else:
            return error_response(
                "Xabar yuborib bo'lmadi. Chat ID to'g'riligini va bot guruhda adminligini tekshiring.", 400
            )
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        db.close()


# ── XODIMLAR RO'YXATI (Telegram bog'lanishi) ─────────

@telegram_bp.route('/users', methods=['GET'])
@company_admin_required
def list_telegram_users():
    """Telegram orqali ro'yxatdan o'tgan xodimlar"""
    db = get_db()
    try:
        users = db.query(TelegramUser).filter_by(company_id=g.company_id).all()
        return success_response([u.to_dict() for u in users])
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        db.close()


@telegram_bp.route('/users/<user_id>', methods=['DELETE'])
@company_admin_required
def delete_telegram_user(user_id):
    """Xodimning Telegram bog'lanishini o'chirish"""
    db = get_db()
    try:
        tu = db.query(TelegramUser).filter_by(
            id=user_id, company_id=g.company_id
        ).first()
        if not tu:
            return error_response("Topilmadi", 404)
        db.delete(tu)
        db.commit()
        return success_response({'id': user_id}, message="O'chirildi")
    except Exception as e:
        db.rollback()
        return error_response(str(e), 500)
    finally:
        db.close()
