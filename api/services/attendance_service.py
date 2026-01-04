"""
Attendance Service - Davomat tizimi xizmati
Haftalik jadvaldan foydalanadi
"""

from database import get_db, AttendanceLog, Employee, EmployeeSchedule
from datetime import datetime, time as datetime_time, date as datetime_date
import pytz
import logging

logger = logging.getLogger(__name__)


def get_employee_work_time_for_date(employee, check_date, db_session=None):
    """
    Berilgan sana uchun xodimning ish vaqtini olish
    Agar schedule mavjud bo'lsa - schedule'dan, aks holda employee.work_start_time dan

    Args:
        employee: Employee object
        check_date: date object
        db_session: optional - agar berilmasa yangi session ochiladi

    Returns: (work_start_time, work_end_time, is_day_off)
    """
    # Agar session berilgan bo'lsa uni ishlat, aks holda yangi session och
    should_close = False
    if db_session is None:
        db = get_db()
        should_close = True
    else:
        db = db_session

    try:
        # Get day of week (1=Monday, 7=Sunday)
        day_of_week = check_date.isoweekday()

        # Try to get schedule for this day
        schedule = db.query(EmployeeSchedule).filter_by(
            employee_id=employee.id,
            day_of_week=day_of_week
        ).first()

        if schedule:
            # Use schedule
            return (
                schedule.work_start_time,
                schedule.work_end_time,
                schedule.is_day_off
            )
        else:
            # Use employee's default work times
            return (
                employee.work_start_time,
                employee.work_end_time,
                False  # Not a day off by default
            )
    finally:
        if should_close:
            db.close()


def parse_time_field(time_value):
    """Parse time field - can be string or time object"""
    if time_value is None:
        return None

    if isinstance(time_value, datetime_time):
        return time_value

    if isinstance(time_value, str):
        try:
            parts = time_value.split(':')
            return datetime_time(int(parts[0]), int(parts[1]))
        except:
            return None

    return None


def process_check_in(employee, check_in_time, device_info=None):
    """
    Kirish vaqtini qayta ishlash
    MUHIM: Agar bugun allaqachon kirish qilingan bo'lsa, yangi kirish qabul qilinmaydi!

    Args:
        employee: Employee object
        check_in_time: DateTime with timezone
        device_info: dict with device_name, ip_address, verify_mode

    Returns:
        AttendanceLog object
    """
    db = get_db()

    try:
        # Get date
        check_date = check_in_time.date()

        # Get work time for this date (considering schedule) - o'z sessionimizni beramiz
        work_start_time, work_end_time, is_day_off = get_employee_work_time_for_date(employee, check_date, db)

        # Parse times
        work_start = parse_time_field(work_start_time)

        # Check if already checked in today
        existing_log = db.query(AttendanceLog).filter_by(
            employee_id=employee.id,
            date=check_date
        ).first()

        # ✅ YANGI MANTIQ: Agar allaqachon kirish qilingan bo'lsa, yangi kirish qabul qilinmaydi
        if existing_log and existing_log.check_in_time:
            logger.warning(
                f"❌ KIRISH RAD ETILDI: {employee.employee_no} bugun {existing_log.check_in_time.strftime('%H:%M')} da allaqachon kirish qilgan. "
                f"Yangi kirish vaqti {check_in_time.strftime('%H:%M')} qabul qilinmadi."
            )
            # Eski kirish vaqtini qaytaramiz, yangilamaymiz!
            return existing_log

        # Calculate late minutes
        late_minutes = 0

        if work_start and not is_day_off:
            # Combine date and time for comparison
            tashkent_tz = pytz.timezone('Asia/Tashkent')
            scheduled_start = tashkent_tz.localize(
                datetime.combine(check_date, work_start)
            )

            # Get grace period from company settings
            from middleware.company_middleware import get_company_settings
            company_settings = get_company_settings(employee.company_id)
            grace_period = company_settings.late_threshold_minutes if company_settings else 15

            # Calculate late time
            time_diff = (check_in_time - scheduled_start).total_seconds() / 60

            if time_diff > grace_period:
                late_minutes = int(time_diff - grace_period)

        # Create or update attendance log
        if existing_log:
            # Agar existing_log bor lekin check_in_time yo'q bo'lsa (mumkin emas lekin xavfsizlik uchun)
            attendance_log = existing_log
            attendance_log.check_in_time = check_in_time
            attendance_log.late_minutes = late_minutes
        else:
            # Yangi kirish yaratish
            attendance_log = AttendanceLog(
                company_id=employee.company_id,
                branch_id=employee.branch_id,
                employee_id=employee.id,
                employee_no=employee.employee_no,
                date=check_date,
                check_in_time=check_in_time,
                late_minutes=late_minutes
            )
            db.add(attendance_log)

        # Set device info if provided
        if device_info:
            attendance_log.device_name = device_info.get('device_name')
            attendance_log.ip_address = device_info.get('ip_address')
            attendance_log.verify_mode = device_info.get('verify_mode')

        db.commit()
        db.refresh(attendance_log)

        logger.info(f"✅ Kirish qabul qilindi: {employee.employee_no} at {check_in_time}, late: {late_minutes} min")

        return attendance_log

    except Exception as e:
        db.rollback()
        logger.error(f"Error processing check-in: {str(e)}", exc_info=True)
        raise
    finally:
        db.close()


def process_check_out(employee, check_out_time):
    """
    Chiqish vaqtini qayta ishlash
    MUHIM: Har safar yangi chiqish vaqti kelsa, eski chiqish yangilanadi (eng oxirgi chiqish qabul qilinadi)

    Args:
        employee: Employee object
        check_out_time: DateTime with timezone

    Returns:
        AttendanceLog object
    """
    db = get_db()

    try:
        # Get date
        check_date = check_out_time.date()

        # Find today's attendance log
        attendance_log = db.query(AttendanceLog).filter_by(
            employee_id=employee.id,
            date=check_date
        ).first()

        if not attendance_log:
            logger.error(f"No check-in found for employee {employee.employee_no} on {check_date}")
            raise Exception("No check-in record found for today")

        if not attendance_log.check_in_time:
            logger.error(f"Check-in time is null for employee {employee.employee_no}")
            raise Exception("Check-in time not found")

        # ✅ YANGI MANTIQ: Agar allaqachon chiqish qilingan bo'lsa, yangi chiqish vaqti bilan yangilaymiz
        if attendance_log.check_out_time:
            logger.info(
                f"🔄 CHIQISH YANGILANDI: {employee.employee_no} oldingi chiqish {attendance_log.check_out_time.strftime('%H:%M')}, "
                f"yangi chiqish {check_out_time.strftime('%H:%M')}"
            )
            # Davom etamiz va yangilaymiz

        # Set check-out time (har doim eng oxirgi chiqish)
        attendance_log.check_out_time = check_out_time

        # Get work time for this date (considering schedule) - o'z sessionimizni beramiz
        work_start_time, work_end_time, is_day_off = get_employee_work_time_for_date(employee, check_date, db)

        # Parse times
        work_end = parse_time_field(work_end_time)

        # Calculate total work minutes
        work_duration = (check_out_time - attendance_log.check_in_time).total_seconds() / 60

        # Subtract lunch break
        lunch_break = employee.lunch_break_duration or 60
        total_work_minutes = int(work_duration - lunch_break)

        if total_work_minutes < 0:
            total_work_minutes = 0

        attendance_log.total_work_minutes = total_work_minutes

        # Calculate early leave and overtime
        early_leave_minutes = 0
        overtime_minutes = 0

        if work_end and not is_day_off:
            tashkent_tz = pytz.timezone('Asia/Tashkent')
            scheduled_end = tashkent_tz.localize(
                datetime.combine(check_date, work_end)
            )

            time_diff = (check_out_time - scheduled_end).total_seconds() / 60

            if time_diff < 0:
                # Left early
                early_leave_minutes = int(abs(time_diff))
            else:
                # Overtime
                overtime_minutes = int(time_diff)

        attendance_log.early_leave_minutes = early_leave_minutes
        attendance_log.overtime_minutes = overtime_minutes

        db.commit()
        db.refresh(attendance_log)

        logger.info(f"✅ Chiqish qabul qilindi: {employee.employee_no} at {check_out_time}, total: {total_work_minutes} min")

        return attendance_log

    except Exception as e:
        db.rollback()
        logger.error(f"Error processing check-out: {str(e)}", exc_info=True)
        raise
    finally:
        db.close()


def get_today_attendance(employee_id):
    """Get today's attendance log for an employee"""
    db = get_db()

    try:
        today = datetime.now(pytz.timezone('Asia/Tashkent')).date()

        attendance_log = db.query(AttendanceLog).filter_by(
            employee_id=employee_id,
            date=today
        ).first()

        return attendance_log

    finally:
        db.close()


def get_attendance_by_date_range(employee_id, start_date, end_date):
    """Get attendance logs for an employee within a date range"""
    db = get_db()

    try:
        logs = db.query(AttendanceLog).filter(
            AttendanceLog.employee_id == employee_id,
            AttendanceLog.date >= start_date,
            AttendanceLog.date <= end_date
        ).order_by(AttendanceLog.date.desc()).all()

        return logs

    finally:
        db.close()