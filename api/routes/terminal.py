from flask import Blueprint, request, jsonify
from datetime import datetime
import re
import json
import logging
import pytz

# Database imports
from database import get_db as get_database_connection
from database import Employee, AttendanceLog, CompanySettings, Company, Branch

# Service imports
from services.attendance_service import process_check_in, process_check_out
from services.penalty_service import create_penalty_for_lateness

# Utils
from utils.helpers import success_response, error_response

terminal_bp = Blueprint('terminal', __name__)
logger = logging.getLogger(__name__)


# ==========================================
# POLIMORFIK DATA EXTRACTION
# Hikvision terminallar turli formatda yuboradi
# ==========================================
def extract_terminal_data(request_obj):
    """
    Hikvision terminalidan kelgan ma'lumotlarni turli formatlardan olish:
    1. multipart/form-data (event_log maydoni)
    2. multipart/form-data (AccessControllerEvent maydoni) - YANGI
    3. raw JSON body (re.search bilan) - ESKI FORMAT

    Returns:
        dict yoki None
    """
    data = None

    # ==========================================
    # 1-USUL: multipart/form-data (event_log)
    # ==========================================
    if request_obj.form and 'event_log' in request_obj.form:
        try:
            event_log_str = request_obj.form.get('event_log')
            logger.info("📦 FORMAT: multipart/form-data (event_log)")
            data = json.loads(event_log_str)
            logger.info("✅ event_log dan JSON muvaffaqiyatli parsed")

            # Rasm fayli bormi?
            if request_obj.files:
                for key, file in request_obj.files.items():
                    logger.info(f"📸 Rasm: {key} = {file.filename}")

            return data
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ event_log JSON parse xatolik: {e}")

    # ==========================================
    # 2-USUL: multipart/form-data (AccessControllerEvent)
    # Ba'zi terminallar shu formatda yuboradi
    # ==========================================
    if request_obj.form and 'AccessControllerEvent' in request_obj.form:
        try:
            event_str = request_obj.form.get('AccessControllerEvent')
            logger.info("📦 FORMAT: multipart/form-data (AccessControllerEvent)")
            data = json.loads(event_str)
            logger.info("✅ AccessControllerEvent dan JSON muvaffaqiyatli parsed")

            # Rasm fayli bormi?
            if request_obj.files:
                for key, file in request_obj.files.items():
                    logger.info(f"📸 Rasm: {key} = {file.filename}")

            return data
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ AccessControllerEvent JSON parse xatolik: {e}")

    # ==========================================
    # 3-USUL: multipart/form-data (har qanday kalit)
    # Universal fallback - birinchi JSON qiymatni olish
    # ==========================================
    if request_obj.form:
        for key, value in request_obj.form.items():
            try:
                logger.info(f"📦 FORMAT: multipart/form-data (kalit: {key})")
                data = json.loads(value)
                logger.info(f"✅ {key} dan JSON muvaffaqiyatli parsed")

                # Rasm fayli bormi?
                if request_obj.files:
                    for fkey, file in request_obj.files.items():
                        logger.info(f"📸 Rasm: {fkey} = {file.filename}")

                return data
            except json.JSONDecodeError:
                continue  # Keyingi kalitni sinab ko'rish

    # ==========================================
    # 4-USUL: raw JSON body (ESKI LOGIKA)
    # ==========================================
    try:
        raw_data = request_obj.get_data().decode('utf-8', errors='ignore')
        if raw_data:
            logger.info("📦 FORMAT: raw body")
            json_match = re.search(r'({.*})', raw_data, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                data = json.loads(json_str)
                logger.info("✅ raw body dan JSON muvaffaqiyatli parsed")
                return data
    except Exception as e:
        logger.error(f"❌ raw data parse xatolik: {e}")

    return None


def parse_hikvision_datetime(date_string):
    """
    Parse Hikvision datetime format to Tashkent timezone
    Supports multiple formats:
    - 2026-01-03T18:30:00
    - 2026-01-03 18:30:00
    - 2026-01-03T18:30:00+05:00
    """
    try:
        tashkent_tz = pytz.timezone('Asia/Tashkent')

        # Remove timezone info if present (we'll add Tashkent TZ)
        clean_time = date_string.strip()

        # Remove +05:00 or similar timezone suffixes
        if '+' in clean_time:
            clean_time = clean_time.split('+')[0]
        if '-' in clean_time and clean_time.count('-') > 2:  # has timezone like -05:00
            parts = clean_time.rsplit('-', 1)
            clean_time = parts[0]

        # Replace T with space
        clean_time = clean_time.replace('T', ' ')

        # Try to parse
        dt_obj = datetime.strptime(clean_time.strip(), '%Y-%m-%d %H:%M:%S')

        # Localize to Tashkent timezone
        dt_aware = tashkent_tz.localize(dt_obj)

        logger.info(f"✅ DateTime parsed successfully: {date_string} -> {dt_aware.strftime('%Y-%m-%d %H:%M:%S')}")

        return dt_aware

    except Exception as e:
        logger.error(f"❌ Failed to parse datetime: {date_string}, error: {e}")
        # Return current time as fallback
        tashkent_tz = pytz.timezone('Asia/Tashkent')
        fallback_time = datetime.now(tashkent_tz)
        logger.warning(f"⚠️ Using current time as fallback: {fallback_time.strftime('%Y-%m-%d %H:%M:%S')}")
        return fallback_time


@terminal_bp.route('/<company_id>/<branch_id>/checkin', methods=['POST'])
def terminal_checkin_with_branch(company_id, branch_id):
    """
    KIRISH TERMINALI - Faqat kirish uchun

    Qo'llab-quvvatlanadigan formatlar:
    1. multipart/form-data (event_log) - YANGI
    2. raw JSON body - MAVJUD
    """
    db = None

    try:
        # ✅ LOG INCOMING REQUEST
        logger.info("=" * 70)
        logger.info("🔵 KIRISH SIGNALI KELDI (CHECK-IN)")
        logger.info(f"📦 Content-Type: {request.content_type}")
        logger.info("=" * 70)

        # ==========================================
        # POLIMORFIK DATA EXTRACTION
        # ==========================================
        data = extract_terminal_data(request)

        if not data:
            logger.warning("⚠️ No JSON found in request")
            return "OK", 200

        # ✅ LOG PARSED JSON
        logger.info("🔵 PARSED JSON:")
        logger.info(json.dumps(data, indent=2, ensure_ascii=False))

        # Check event type
        event_type = data.get('eventType')
        logger.info(f"🔵 EVENT TYPE: {event_type}")

        if event_type != 'AccessControllerEvent':
            logger.warning(f"⚠️ Skipping event type: {event_type}")
            return "OK", 200

        event_info = data.get('AccessControllerEvent', {})

        # ==========================================
        # YANGI: subEventType TEKSHIRUVI
        # 75 = Yuz muvaffaqiyatli tanilgan
        # Boshqa qiymatlar (21, 8, 9...) = Eshik/Tizim signallari
        # ==========================================
        sub_event_type = event_info.get('subEventType')
        logger.info(f"🔵 SUB-EVENT TYPE: {sub_event_type}")

        if sub_event_type != 75:
            logger.info(f"ℹ️ Keraksiz signal (Eshik/Tizim): {sub_event_type}. O'tkazib yuborildi.")
            return "OK", 200

        # ✅ LOG ALL POSSIBLE EMPLOYEE ID FIELDS
        logger.info("🔵 CHECKING EMPLOYEE ID FIELDS:")
        logger.info(f"  - employeeNoString: {event_info.get('employeeNoString')}")
        logger.info(f"  - employeeNo: {event_info.get('employeeNo')}")
        logger.info(f"  - cardNo: {event_info.get('cardNo')}")
        logger.info(f"  - personID: {event_info.get('personID')}")

        # Try multiple fields for employee ID
        emp_id = (
                event_info.get('employeeNoString') or
                event_info.get('employeeNo') or
                event_info.get('cardNo') or
                event_info.get('personID')
        )

        if not emp_id:
            logger.warning("❌ No employee ID in check-in event")
            logger.warning(f"AccessControllerEvent content: {json.dumps(event_info, indent=2)}")
            return "OK", 200

        # ✅ HAR DOIM TASHKENT VAQTINI ISHLATISH
        tashkent_tz = pytz.timezone('Asia/Tashkent')
        attendance_time = datetime.now(tashkent_tz)

        raw_time = data.get('dateTime', '')
        logger.info(f"⏰ Terminal vaqti: {raw_time} (IGNORE qilinmoqda)")
        logger.info(f"✅ Server vaqti (Tashkent): {attendance_time.strftime('%Y-%m-%d %H:%M:%S')}")

        # Get device info
        name = event_info.get('name', 'NOMA\'LUM XODIM')
        device_name = data.get('deviceName', 'Check-In Terminal')
        ip_address = data.get('ipAddress', request.remote_addr)

        logger.info("=" * 70)
        logger.info(f"🟢 KIRISH TERMINALI")
        logger.info(f"🏢 COMPANY ID: {company_id}")
        logger.info(f"🏪 BRANCH ID: {branch_id}")
        logger.info(f"✅ TANILDI: {name}")
        logger.info(f"🆔 EMPLOYEE ID: {emp_id}")
        logger.info(f"📱 TERMINAL: {device_name}")
        logger.info(f"⏰ VAQT: {attendance_time.strftime('%d.%m.%Y %H:%M:%S')}")
        logger.info("=" * 70)

        # Rest of the code remains the same...
        db = get_database_connection()

        company = db.query(Company).filter_by(id=company_id).first()
        if not company:
            logger.error(f"❌ KOMPANIYA TOPILMADI: {company_id}")
            db.close()
            return jsonify({
                'success': False,
                'error': f'Kompaniya topilmadi: {company_id}'
            }), 404

        logger.info(f"🏢 Kompaniya: {company.company_name}")

        branch = db.query(Branch).filter_by(
            id=branch_id,
            company_id=company_id
        ).first()

        if not branch:
            logger.error(f"❌ FILIAL TOPILMADI: {branch_id}")
            db.close()
            return jsonify({
                'success': False,
                'error': f'Filial topilmadi: {branch_id}'
            }), 404

        logger.info(f"🏪 Filial: {branch.name}")

        employee = db.query(Employee).filter_by(
            employee_no=str(emp_id),
            company_id=company_id,
            branch_id=branch_id,
            status='active'
        ).first()

        if not employee:
            logger.warning(f"❌ XODIM TOPILMADI: ID={emp_id} in branch={branch.name}")
            db.close()
            return jsonify({
                'success': False,
                'error': f'Xodim topilmadi: {emp_id}',
                'message': f'⚠️ XODIM TOPILMADI\n🆔 ID: {emp_id}\n🏢 {company.company_name}\n🏪 {branch.name}\n📍 Sistemaga qo\'shing!'
            }), 200

        logger.info(f"👤 Xodim: {employee.full_name}")

        employee_name = employee.full_name
        employee_number = employee.employee_no
        employee_dept = employee.department.name if employee.department else ''
        employee_position = employee.position or ''
        company_name = company.company_name
        branch_name = branch.name

        company_settings = db.query(CompanySettings).filter_by(
            company_id=company_id
        ).first()

        if not company_settings:
            logger.error(f"⚠️ Kompaniya sozlamalari topilmadi")
            db.close()
            return "OK", 200

        try:
            attendance_log = process_check_in(
                employee=employee,
                check_in_time=attendance_time,
                device_info={
                    'device_name': device_name,
                    'ip_address': ip_address,
                    'verify_mode': 'face'
                }
            )

            attendance_log.branch_id = branch_id

            # Jarima summasi hisoblash (Telegram uchun)
            penalty_amount = 0.0
            late_count_this_month = 0

            if attendance_log.late_minutes > 0:
                logger.info(f"⚠️ KECHIKISH: {attendance_log.late_minutes} daqiqa")

                # Bu oyda necha marta kechikkan (bugungi kun ham qo'shiladi)
                from datetime import date as dt_date
                from sqlalchemy import extract, and_
                today_date = attendance_time.date()
                late_count_this_month = db.query(AttendanceLog).filter(
                    and_(
                        AttendanceLog.employee_id == employee.id,
                        AttendanceLog.late_minutes > 0,
                        extract('month', AttendanceLog.date) == today_date.month,
                        extract('year', AttendanceLog.date) == today_date.year,
                    )
                ).count()

                # Stavkani aniqlash (3 bosqichli)
                late_penalty_first  = getattr(company_settings, 'late_penalty_first',  1000.0) or 1000.0
                late_penalty_second = getattr(company_settings, 'late_penalty_second', 3000.0) or 3000.0
                late_penalty_third  = getattr(company_settings, 'late_penalty_third',  5000.0) or 5000.0

                if late_count_this_month <= 1:
                    rate = late_penalty_first
                elif late_count_this_month == 2:
                    rate = late_penalty_second
                else:
                    rate = late_penalty_third

                penalty_amount = attendance_log.late_minutes * rate

                create_penalty_for_lateness(
                    employee=employee,
                    attendance_log=attendance_log,
                    late_minutes=attendance_log.late_minutes,
                    settings=company_settings
                )

            db.commit()

            # ── TELEGRAM XABARI ──────────────────────────────
            try:
                from services.telegram_service import notify_checkin
                notify_checkin(
                    company_id=company_id,
                    employee_name=employee_name,
                    late_minutes=attendance_log.late_minutes or 0,
                    check_in_time=attendance_log.check_in_time,
                    dept=employee_dept,
                    position=employee_position,
                    penalty_amount=penalty_amount,
                    late_count_month=late_count_this_month,
                )
            except Exception as tg_err:
                logger.warning(f"⚠️ Telegram xabar yuborishda xato: {tg_err}")
            # ─────────────────────────────────────────────────

            status_emoji = "⚠️" if attendance_log.late_minutes > 0 else "✅"
            status_text = f"KECHIKDI ({attendance_log.late_minutes} min)" if attendance_log.late_minutes > 0 else "VAQTIDA"

            logger.info(f"{status_emoji} KIRISH MUVAFFAQIYATLI: {employee_name} - {status_text}")
            logger.info("=" * 70)

            db.close()

            return jsonify({
                'success': True,
                'type': 'check_in',
                'message': f'{status_emoji} KIRISH QILINDI',
                'employee': employee_name,
                'employee_no': employee_number,
                'company': company_name,
                'branch': branch_name,
                'time': attendance_time.strftime('%H:%M'),
                'status': status_text,
                'late_minutes': attendance_log.late_minutes
            }), 200

        except Exception as check_in_error:
            logger.error(f"❌ KIRISH XATOLIK: {str(check_in_error)}", exc_info=True)
            if db:
                db.rollback()
                db.close()
            return "OK", 200

    except Exception as e:
        logger.error(f"❌ UMUMIY XATOLIK: {str(e)}", exc_info=True)
        if db:
            try:
                db.rollback()
                db.close()
            except:
                pass
        return "OK", 200


@terminal_bp.route('/<company_id>/<branch_id>/checkout', methods=['POST'])
def terminal_checkout_with_branch(company_id, branch_id):
    """
    CHIQISH TERMINALI - Faqat chiqish uchun

    Qo'llab-quvvatlanadigan formatlar:
    1. multipart/form-data (event_log) - YANGI
    2. raw JSON body - MAVJUD
    """
    db = None

    try:
        # ✅ LOG INCOMING REQUEST
        logger.info("=" * 70)
        logger.info("🔴 CHIQISH SIGNALI KELDI (CHECK-OUT)")
        logger.info(f"📦 Content-Type: {request.content_type}")
        logger.info("=" * 70)

        # ==========================================
        # POLIMORFIK DATA EXTRACTION
        # ==========================================
        data = extract_terminal_data(request)

        if not data:
            logger.warning("⚠️ No JSON found in request")
            return "OK", 200

        # ✅ LOG PARSED JSON
        logger.info("🔴 PARSED JSON:")
        logger.info(json.dumps(data, indent=2, ensure_ascii=False))

        event_type = data.get('eventType')
        logger.info(f"🔴 EVENT TYPE: {event_type}")

        if event_type != 'AccessControllerEvent':
            logger.warning(f"⚠️ Skipping event type: {event_type}")
            return "OK", 200

        event_info = data.get('AccessControllerEvent', {})

        # ==========================================
        # YANGI: subEventType TEKSHIRUVI
        # 75 = Yuz muvaffaqiyatli tanilgan
        # Boshqa qiymatlar (21, 8, 9...) = Eshik/Tizim signallari
        # ==========================================
        sub_event_type = event_info.get('subEventType')
        logger.info(f"🔴 SUB-EVENT TYPE: {sub_event_type}")

        if sub_event_type != 75:
            logger.info(f"ℹ️ Keraksiz signal (Eshik/Tizim): {sub_event_type}. O'tkazib yuborildi.")
            return "OK", 200

        # ✅ LOG ALL POSSIBLE EMPLOYEE ID FIELDS
        logger.info("🔴 CHECKING EMPLOYEE ID FIELDS:")
        logger.info(f"  - employeeNoString: {event_info.get('employeeNoString')}")
        logger.info(f"  - employeeNo: {event_info.get('employeeNo')}")
        logger.info(f"  - cardNo: {event_info.get('cardNo')}")
        logger.info(f"  - personID: {event_info.get('personID')}")

        # Try multiple fields for employee ID
        emp_id = (
                event_info.get('employeeNoString') or
                event_info.get('employeeNo') or
                event_info.get('cardNo') or
                event_info.get('personID')
        )

        if not emp_id:
            logger.warning("❌ No employee ID in check-out event")
            logger.warning(f"AccessControllerEvent content: {json.dumps(event_info, indent=2)}")
            return "OK", 200

        # ✅ HAR DOIM TASHKENT VAQTINI ISHLATISH
        tashkent_tz = pytz.timezone('Asia/Tashkent')
        attendance_time = datetime.now(tashkent_tz)

        raw_time = data.get('dateTime', '')
        logger.info(f"⏰ Terminal vaqti: {raw_time} (IGNORE qilinmoqda)")
        logger.info(f"✅ Server vaqti (Tashkent): {attendance_time.strftime('%Y-%m-%d %H:%M:%S')}")

        name = event_info.get('name', 'NOMA\'LUM XODIM')
        device_name = data.get('deviceName', 'Check-Out Terminal')
        ip_address = data.get('ipAddress', request.remote_addr)

        logger.info("=" * 70)
        logger.info(f"🔴 CHIQISH TERMINALI")
        logger.info(f"🏢 COMPANY ID: {company_id}")
        logger.info(f"🏪 BRANCH ID: {branch_id}")
        logger.info(f"✅ TANILDI: {name}")
        logger.info(f"🆔 EMPLOYEE ID: {emp_id}")
        logger.info(f"📱 TERMINAL: {device_name}")
        logger.info(f"⏰ VAQT: {attendance_time.strftime('%d.%m.%Y %H:%M:%S')}")
        logger.info("=" * 70)

        # Rest of checkout code remains the same...
        db = get_database_connection()

        company = db.query(Company).filter_by(id=company_id).first()
        if not company:
            logger.error(f"❌ KOMPANIYA TOPILMADI: {company_id}")
            db.close()
            return jsonify({
                'success': False,
                'error': f'Kompaniya topilmadi: {company_id}'
            }), 404

        logger.info(f"🏢 Kompaniya: {company.company_name}")

        branch = db.query(Branch).filter_by(
            id=branch_id,
            company_id=company_id
        ).first()

        if not branch:
            logger.error(f"❌ FILIAL TOPILMADI: {branch_id}")
            db.close()
            return jsonify({
                'success': False,
                'error': f'Filial topilmadi: {branch_id}'
            }), 404

        logger.info(f"🏪 Filial: {branch.name}")

        employee = db.query(Employee).filter_by(
            employee_no=str(emp_id),
            company_id=company_id,
            branch_id=branch_id,
            status='active'
        ).first()

        if not employee:
            logger.warning(f"❌ XODIM TOPILMADI: ID={emp_id}")
            db.close()
            return jsonify({
                'success': False,
                'error': f'Xodim topilmadi: {emp_id}',
                'message': f'⚠️ XODIM TOPILMADI\n🆔 ID: {emp_id}\n🏢 {company.company_name}\n🏪 {branch.name}'
            }), 200

        logger.info(f"👤 Xodim: {employee.full_name}")

        employee_name = employee.full_name
        employee_number = employee.employee_no
        company_name = company.company_name
        branch_name = branch.name

        today = attendance_time.date()
        existing_log = db.query(AttendanceLog).filter_by(
            employee_id=employee.id,
            date=today
        ).first()

        if not existing_log or not existing_log.check_in_time:
            logger.warning(f"⚠️ BUGUN KIRISH QILINMAGAN: {employee_name}")
            db.close()
            return jsonify({
                'success': False,
                'error': 'Bugun kirish qilinmagan',
                'message': f'⚠️ XATOLIK\n👤 {employee_name}\n🏢 {company_name}\n🏪 {branch_name}\n📍 Bugun kirish qilmagan!'
            }), 200

        if existing_log.check_out_time:
            logger.info(
                f"🔄 CHIQISH YANGILANMOQDA: {employee_name} "
                f"oldingi chiqish {existing_log.check_out_time.strftime('%H:%M')}, "
                f"yangi chiqish {attendance_time.strftime('%H:%M')}"
            )

        try:
            updated_log = process_check_out(
                employee=employee,
                check_out_time=attendance_time
            )

            work_hours = round(updated_log.total_work_minutes / 60, 2) if updated_log.total_work_minutes else 0

            # ── TELEGRAM XABARI ──────────────────────────────
            try:
                from services.telegram_service import notify_checkout
                notify_checkout(
                    company_id=company_id,
                    employee_name=employee_name,
                    check_out_time=updated_log.check_out_time,
                    total_work_minutes=updated_log.total_work_minutes or 0,
                    early_leave_minutes=updated_log.early_leave_minutes or 0,
                )
            except Exception as tg_err:
                logger.warning(f"⚠️ Telegram checkout xabar xatosi: {tg_err}")
            # ─────────────────────────────────────────────────

            logger.info(f"✅ CHIQISH MUVAFFAQIYATLI: {employee_name}")
            logger.info(f"⏱️ ISH VAQTI: {work_hours} soat")
            logger.info(f"🟢 KIRISH: {updated_log.check_in_time.strftime('%H:%M')}")
            logger.info(f"🔴 CHIQISH: {attendance_time.strftime('%H:%M')}")
            logger.info("=" * 70)

            db.close()

            return jsonify({
                'success': True,
                'type': 'check_out',
                'message': '✅ CHIQISH QILINDI',
                'employee': employee_name,
                'employee_no': employee_number,
                'company': company_name,
                'branch': branch_name,
                'check_in_time': updated_log.check_in_time.strftime('%H:%M'),
                'check_out_time': attendance_time.strftime('%H:%M'),
                'work_hours': work_hours,
                'overtime_minutes': updated_log.overtime_minutes or 0
            }), 200

        except Exception as check_out_error:
            logger.error(f"❌ CHIQISH XATOLIK: {str(check_out_error)}", exc_info=True)
            if db:
                db.rollback()
                db.close()
            return "OK", 200

    except Exception as e:
        logger.error(f"❌ UMUMIY XATOLIK: {str(e)}", exc_info=True)
        if db:
            try:
                db.rollback()
                db.close()
            except:
                pass
        return "OK", 200


@terminal_bp.route('/test', methods=['GET'])
def test_terminal():
    """Test endpoint"""
    return jsonify({
        'status': 'active',
        'message': 'Hikvision Terminal Integration - Multi-Tenant with Branches',
        'supported_formats': [
            'multipart/form-data (event_log field) - YANGI',
            'raw JSON body - MAVJUD'
        ],
        'url_format': {
            'check_in': '/api/terminal/{company_id}/{branch_id}/checkin',
            'check_out': '/api/terminal/{company_id}/{branch_id}/checkout'
        },
        'example': {
            'company': 'Giperstroy',
            'company_id': 'e7fba3ce-beae-48b1-a2b2-3075c24250d2',
            'branches': [
                {
                    'name': 'Ombor',
                    'branch_id': 'branch-123',
                    'check_in_url': '/api/terminal/e7fba3ce-beae-48b1-a2b2-3075c24250d2/branch-123/checkin',
                    'check_out_url': '/api/terminal/e7fba3ce-beae-48b1-a2b2-3075c24250d2/branch-123/checkout'
                },
                {
                    'name': 'Do\'kon',
                    'branch_id': 'branch-456',
                    'check_in_url': '/api/terminal/e7fba3ce-beae-48b1-a2b2-3075c24250d2/branch-456/checkin',
                    'check_out_url': '/api/terminal/e7fba3ce-beae-48b1-a2b2-3075c24250d2/branch-456/checkout'
                }
            ]
        },
        'time': datetime.now().isoformat()
    }), 200