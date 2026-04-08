"""
Attendance Service - Davomat tizimi xizmati
Haftalik jadvaldan foydalanadi + WorkTimeOverride va SpecialDayOff qo'llab-quvvatlanadi
"""

from database import get_db, AttendanceLog, Employee, EmployeeSchedule, WorkTimeOverride, SpecialDayOff
from datetime import datetime, time as datetime_time, date as datetime_date
import pytz
import logging

logger = logging.getLogger(__name__)


def get_active_overrides_for_employee(employee, check_date, db_session):
    """
    Berilgan sana uchun xodimga tegishli barcha aktiv WorkTimeOverride larni olish.
    Ustuvorlik tartibi: employee > department > branch > company
    """
    from sqlalchemy import and_, or_

    overrides = db_session.query(WorkTimeOverride).filter(
        and_(
            WorkTimeOverride.company_id == employee.company_id,
            WorkTimeOverride.is_active == True,
            WorkTimeOverride.start_date <= check_date,
            WorkTimeOverride.end_date >= check_date,
            or_(
                WorkTimeOverride.employee_id == employee.id,
                and_(WorkTimeOverride.department_id == employee.department_id,
                     WorkTimeOverride.department_id.isnot(None)),
                and_(WorkTimeOverride.branch_id == employee.branch_id,
                     WorkTimeOverride.branch_id.isnot(None)),
                and_(WorkTimeOverride.employee_id.is_(None),
                     WorkTimeOverride.department_id.is_(None),
                     WorkTimeOverride.branch_id.is_(None))
            )
        )
    ).all()

    # Ustuvorlik: employee > department > branch > company
    for o in overrides:
        if o.employee_id == employee.id:
            return o
    for o in overrides:
        if o.department_id and o.department_id == employee.department_id:
            return o
    for o in overrides:
        if o.branch_id and o.branch_id == employee.branch_id:
            return o
    for o in overrides:
        if not o.employee_id and not o.department_id and not o.branch_id:
            return o
    return None


def get_active_special_day_off_for_employee(employee, check_date, db_session):
    """
    Berilgan sana uchun xodimga tegishli SpecialDayOff ni olish.
    Ustuvorlik: employee > department > branch > company
    """
    from sqlalchemy import and_, or_

    events = db_session.query(SpecialDayOff).filter(
        and_(
            SpecialDayOff.company_id == employee.company_id,
            SpecialDayOff.is_active == True,
            SpecialDayOff.start_date <= check_date,
            SpecialDayOff.end_date >= check_date,
            or_(
                SpecialDayOff.employee_id == employee.id,
                and_(SpecialDayOff.department_id == employee.department_id,
                     SpecialDayOff.department_id.isnot(None)),
                and_(SpecialDayOff.branch_id == employee.branch_id,
                     SpecialDayOff.branch_id.isnot(None)),
                and_(SpecialDayOff.employee_id.is_(None),
                     SpecialDayOff.department_id.is_(None),
                     SpecialDayOff.branch_id.is_(None))
            )
        )
    ).all()

    # Ustuvorlik: employee > department > branch > company
    for e in events:
        if e.employee_id == employee.id:
            return e
    for e in events:
        if e.department_id and e.department_id == employee.department_id:
            return e
    for e in events:
        if e.branch_id and e.branch_id == employee.branch_id:
            return e
    for e in events:
        if not e.employee_id and not e.department_id and not e.branch_id:
            return e
    return None


def get_employee_work_time_for_date(employee, check_date, db_session=None):
    """
    Berilgan sana uchun xodimning ish vaqtini olish.
    Ustuvorlik tartibi:
    1. SpecialDayOff (day_off) -> dam olish kuni
    2. WorkTimeOverride -> vaqtni o'zgartirish
    3. EmployeeSchedule -> haftalik jadval
    4. employee.work_start/end_time -> standart vaqt

    Returns: (work_start_time, work_end_time, is_day_off, special_event)
    """
    should_close = False
    if db_session is None:
        db = get_db()
        should_close = True
    else:
        db = db_session

    try:
        # 1. SpecialDayOff tekshirish
        special_event = get_active_special_day_off_for_employee(employee, check_date, db)

        if special_event and special_event.event_type == 'day_off':
            logger.info(f"📅 SpecialDayOff (day_off): {employee.employee_no} - {special_event.title}")
            return (None, None, True, special_event)

        # 2. Haftalik jadval (EmployeeSchedule)
        day_of_week = check_date.isoweekday()
        schedule = db.query(EmployeeSchedule).filter_by(
            employee_id=employee.id,
            day_of_week=day_of_week
        ).first()

        if schedule and schedule.is_day_off:
            return (None, None, True, None)

        # Bazaviy vaqtlarni aniqlash
        if schedule:
            base_start = schedule.work_start_time
            base_end = schedule.work_end_time
        else:
            base_start = employee.work_start_time
            base_end = employee.work_end_time

        # 3. WorkTimeOverride tekshirish (agar topilsa ustiga yozadi)
        override = get_active_overrides_for_employee(employee, check_date, db)
        if override:
            logger.info(f"⏰ WorkTimeOverride: {employee.employee_no} - {override.title}")
            if override.work_start_time:
                base_start = override.work_start_time
            if override.work_end_time:
                base_end = override.work_end_time

        return (base_start, base_end, False, special_event)

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
    Kirish vaqtini qayta ishlash.
    WorkTimeOverride va SpecialDayOff ni hisobga oladi.
    """
    db = get_db()

    try:
        check_date = check_in_time.date()

        # Ish vaqtini olish (override va special events bilan)
        work_start_time, work_end_time, is_day_off, special_event = get_employee_work_time_for_date(
            employee, check_date, db
        )

        work_start = parse_time_field(work_start_time)

        # Bugun allaqachon kirish bor-yo'qligini tekshirish
        existing_log = db.query(AttendanceLog).filter_by(
            employee_id=employee.id,
            date=check_date
        ).first()

        if existing_log and existing_log.check_in_time:
            logger.warning(
                f"❌ KIRISH RAD ETILDI: {employee.employee_no} bugun {existing_log.check_in_time.strftime('%H:%M')} da allaqachon kirish qilgan."
            )
            return existing_log

        # Kechikish hisoblash
        late_minutes = 0

        if work_start and not is_day_off:
            tashkent_tz = pytz.timezone('Asia/Tashkent')
            scheduled_start = tashkent_tz.localize(
                datetime.combine(check_date, work_start)
            )

            from middleware.company_middleware import get_company_settings
            company_settings = get_company_settings(employee.company_id)
            grace_period = company_settings.late_threshold_minutes if company_settings else 15

            time_diff = (check_in_time - scheduled_start).total_seconds() / 60

            # SpecialDayOff late_start bo'lsa - kechikish jarima yo'q
            if special_event and special_event.event_type == 'late_start':
                effective_start = parse_time_field(special_event.override_start_time) or work_start
                scheduled_start_special = tashkent_tz.localize(
                    datetime.combine(check_date, effective_start)
                )
                time_diff_special = (check_in_time - scheduled_start_special).total_seconds() / 60
                if time_diff_special > grace_period:
                    late_minutes = int(time_diff_special - grace_period)
                else:
                    late_minutes = 0
                logger.info(f"⏰ late_start event: {employee.employee_no}, adjusted late: {late_minutes} min")
            elif time_diff > grace_period:
                late_minutes = int(time_diff - grace_period)

        # Davomat yozuvini yaratish yoki yangilash
        if existing_log:
            attendance_log = existing_log
            attendance_log.check_in_time = check_in_time
            attendance_log.late_minutes = late_minutes
        else:
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

        if device_info:
            attendance_log.device_name = device_info.get('device_name')
            attendance_log.ip_address = device_info.get('ip_address')
            attendance_log.verify_mode = device_info.get('verify_mode')

        db.commit()
        db.refresh(attendance_log)

        logger.info(f"✅ Kirish: {employee.employee_no} at {check_in_time}, late: {late_minutes} min")
        return attendance_log

    except Exception as e:
        db.rollback()
        logger.error(f"Error processing check-in: {str(e)}", exc_info=True)
        raise
    finally:
        db.close()


def process_check_out(employee, check_out_time):
    """
    Chiqish vaqtini qayta ishlash.
    WorkTimeOverride va SpecialDayOff (early_leave) ni hisobga oladi.
    """
    db = get_db()

    try:
        check_date = check_out_time.date()

        attendance_log = db.query(AttendanceLog).filter_by(
            employee_id=employee.id,
            date=check_date
        ).first()

        if not attendance_log:
            logger.error(f"No check-in found for {employee.employee_no} on {check_date}")
            raise Exception("No check-in record found for today")

        if not attendance_log.check_in_time:
            raise Exception("Check-in time not found")

        if attendance_log.check_out_time:
            logger.info(f"🔄 CHIQISH YANGILANDI: {employee.employee_no}")

        attendance_log.check_out_time = check_out_time

        # Ish vaqtini olish (override va special events bilan)
        work_start_time, work_end_time, is_day_off, special_event = get_employee_work_time_for_date(
            employee, check_date, db
        )

        work_end = parse_time_field(work_end_time)

        # Jami ish vaqtini hisoblash
        work_duration = (check_out_time - attendance_log.check_in_time).total_seconds() / 60
        lunch_break = employee.lunch_break_duration or 60
        total_work_minutes = int(work_duration - lunch_break)
        if total_work_minutes < 0:
            total_work_minutes = 0

        attendance_log.total_work_minutes = total_work_minutes

        # Erta ketish va ortiqcha ish vaqtini hisoblash
        early_leave_minutes = 0
        overtime_minutes = 0

        if work_end and not is_day_off:
            tashkent_tz = pytz.timezone('Asia/Tashkent')
            scheduled_end = tashkent_tz.localize(
                datetime.combine(check_date, work_end)
            )

            time_diff = (check_out_time - scheduled_end).total_seconds() / 60

            # SpecialDayOff early_leave bo'lsa - erta ketish jarima yo'q
            if special_event and special_event.event_type == 'early_leave':
                effective_end = parse_time_field(special_event.override_end_time) or work_end
                scheduled_end_special = tashkent_tz.localize(
                    datetime.combine(check_date, effective_end)
                )
                time_diff_special = (check_out_time - scheduled_end_special).total_seconds() / 60
                if time_diff_special < 0:
                    early_leave_minutes = int(abs(time_diff_special))
                else:
                    overtime_minutes = int(time_diff_special)
                    early_leave_minutes = 0
                logger.info(f"⏰ early_leave event: {employee.employee_no}, adjusted early_leave: {early_leave_minutes} min")
            elif time_diff < 0:
                early_leave_minutes = int(abs(time_diff))
            else:
                overtime_minutes = int(time_diff)

        attendance_log.early_leave_minutes = early_leave_minutes
        attendance_log.overtime_minutes = overtime_minutes

        db.commit()
        db.refresh(attendance_log)

        logger.info(f"✅ Chiqish: {employee.employee_no} at {check_out_time}, early_leave: {early_leave_minutes} min")
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



