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
    """
    db = None

    try:
        # ✅ LOG RAW DATA
        raw_data = request.get_data().decode('utf-8', errors='ignore')
        logger.info("=" * 70)
        logger.info("🔵 RAW DATA RECEIVED (CHECK-IN):")
        logger.info(raw_data)
        logger.info("=" * 70)

        # Get raw data
        json_match = re.search(r'({.*})', raw_data, re.DOTALL)
        if not json_match:
            logger.warning("⚠️ No JSON found in request")
            return "OK", 200

        json_str = json_match.group(1)
        data = json.loads(json_str)

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

            if attendance_log.late_minutes > 0:
                logger.info(f"⚠️ KECHIKISH: {attendance_log.late_minutes} daqiqa")
                create_penalty_for_lateness(
                    employee=employee,
                    attendance_log=attendance_log,
                    late_minutes=attendance_log.late_minutes,
                    settings=company_settings
                )

            db.commit()

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
    """
    db = None

    try:
        # ✅ LOG RAW DATA
        raw_data = request.get_data().decode('utf-8', errors='ignore')
        logger.info("=" * 70)
        logger.info("🔴 RAW DATA RECEIVED (CHECK-OUT):")
        logger.info(raw_data)
        logger.info("=" * 70)

        json_match = re.search(r'({.*})', raw_data, re.DOTALL)
        if not json_match:
            logger.warning("⚠️ No JSON found in request")
            return "OK", 200

        json_str = json_match.group(1)
        data = json.loads(json_str)

        # ✅ LOG PARSED JSON
        logger.info("🔴 PARSED JSON:")
        logger.info(json.dumps(data, indent=2, ensure_ascii=False))

        event_type = data.get('eventType')
        logger.info(f"🔴 EVENT TYPE: {event_type}")

        if event_type != 'AccessControllerEvent':
            logger.warning(f"⚠️ Skipping event type: {event_type}")
            return "OK", 200

        event_info = data.get('AccessControllerEvent', {})

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