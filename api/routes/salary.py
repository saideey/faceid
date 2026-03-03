from flask import Blueprint, request, jsonify, g, send_file
from database import get_db, Employee, Penalty, Bonus, AttendanceLog, CompanySettings, EmployeeSchedule, EmployeeLeave, \
    Branch
from middleware.auth_middleware import require_auth
from middleware.company_middleware import load_company_context
from utils.helpers import success_response, error_response
from datetime import datetime, timedelta, date
from sqlalchemy import func, and_
from sqlalchemy.orm import joinedload
import calendar
import logging
import xlsxwriter
import io

salary_bp = Blueprint('salary', __name__)
logger = logging.getLogger(__name__)


def get_employee_leaves_for_period(employee_id, company_id, start_date, end_date, db=None):
    """
    Xodimning dam olish va kasal kunlarini olish

    Returns: {
        'dates': {'2025-01-15': 'rest', '2025-01-20': 'sick', ...},
        'rest_count': int,
        'sick_count': int,
        'total_count': int
    }
    """
    # Agar db berilmagan bo'lsa, yangi session ochish
    should_close_db = False
    if db is None:
        db = get_db()
        should_close_db = True

    try:
        leaves = db.query(EmployeeLeave).filter(
            and_(
                EmployeeLeave.employee_id == employee_id,
                EmployeeLeave.company_id == company_id,
                EmployeeLeave.date >= start_date,
                EmployeeLeave.date <= end_date
            )
        ).all()

        dates = {}
        rest_count = 0
        sick_count = 0

        for leave in leaves:
            date_str = leave.date.isoformat()
            dates[date_str] = leave.leave_type

            if leave.leave_type == 'rest':
                rest_count += 1
            elif leave.leave_type == 'sick':
                sick_count += 1

        return {
            'dates': dates,
            'rest_count': rest_count,
            'sick_count': sick_count,
            'total_count': rest_count + sick_count
        }
    finally:
        # Faqat bu funksiya ochgan bo'lsa yopish
        if should_close_db:
            db.close()


def get_employee_expected_days(employee, start_date, end_date, for_daily_rate=False, db=None):
    """
    Xodimning schedule asosida expected work days hisoblash

    MUHIM: Kunlik stavka uchun DOIM to'liq oy ishlatiladi!

    Args:
        employee: Employee object
        start_date: Boshlanish sanasi
        end_date: Tugash sanasi
        for_daily_rate: Agar True bo'lsa, to'liq oy asosida hisoblaydi
        db: Database session (optional)

    Returns: (expected_days, schedule_dict)
    schedule_dict = {
        1: {'start': '09:00', 'end': '18:00', 'is_off': False},  # Monday
        2: {'start': '12:00', 'end': '15:00', 'is_off': False},  # Tuesday
        ...
        5: {'start': None, 'end': None, 'is_off': True},  # Friday (dam olish)
    }
    """
    # Agar db berilmagan bo'lsa, yangi session ochish
    should_close_db = False
    if db is None:
        db = get_db()
        should_close_db = True

    # Get employee schedules
    schedules = db.query(EmployeeSchedule).filter_by(employee_id=employee.id).all()

    # Build schedule dict (1=Monday, 7=Sunday)
    schedule_dict = {}
    for sched in schedules:
        schedule_dict[sched.day_of_week] = {
            'start': str(sched.work_start_time) if sched.work_start_time else None,
            'end': str(sched.work_end_time) if sched.work_end_time else None,
            'is_off': sched.is_day_off
        }

    # If no schedule, use default (Mon-Fri, 9-18)
    if not schedule_dict:
        default_schedule = {
            1: {'start': '09:00:00', 'end': '18:00:00', 'is_off': False},  # Mon
            2: {'start': '09:00:00', 'end': '18:00:00', 'is_off': False},  # Tue
            3: {'start': '09:00:00', 'end': '18:00:00', 'is_off': False},  # Wed
            4: {'start': '09:00:00', 'end': '18:00:00', 'is_off': False},  # Thu
            5: {'start': '09:00:00', 'end': '18:00:00', 'is_off': False},  # Fri
            6: {'start': None, 'end': None, 'is_off': True},  # Sat (dam)
            7: {'start': None, 'end': None, 'is_off': True}  # Sun (dam)
        }
        schedule_dict = default_schedule

    # Determine calculation range
    if for_daily_rate:
        # For daily rate: ALWAYS use FULL MONTH of start_date
        calc_start = date(start_date.year, start_date.month, 1)
        last_day = calendar.monthrange(start_date.year, start_date.month)[1]
        calc_end = date(start_date.year, start_date.month, last_day)
    else:
        # For period calculation: use actual period (but only past days)
        today = date.today()
        calc_start = start_date
        calc_end = min(end_date, today)

    # Count expected work days in the range
    expected_days = 0
    current = calc_start

    while current <= calc_end:
        day_of_week = current.isoweekday()  # 1=Mon, 7=Sun

        day_schedule = schedule_dict.get(day_of_week, {'is_off': True})

        if not day_schedule['is_off']:
            expected_days += 1

        current += timedelta(days=1)

    # Faqat bu funksiya ochgan bo'lsa yopish
    if should_close_db:
        db.close()
    return expected_days, schedule_dict


def calculate_employee_salary(employee, start_date, end_date, company_settings, db=None):
    """
    PROFESSIONAL xodim oyligini hisoblash

    YANGI: Dam olish va kasal kunlari uchun jarima hisoblanmaydi!

    QOIDA:
    1. Kunlik stavka DOIM to'liq oy asosida hisoblanadi
    2. Ishlangan kunlar faqat sana oralig'ida hisoblanadi
    3. Har bir jarima/bonus batafsil ko'rsatiladi
    4. Dam olish/kasal kunlari uchun jarima hisoblanmaydi

    Returns: {
        'base_salary': float,
        'salary_type': 'monthly' or 'daily',
        'calculation_method': 'full_month_based',
        'full_month_work_days': int,
        'daily_rate': float,
        'period_work_days': int,
        'worked_days': int,
        'absence_days': int,
        'late_details': [...],
        'excused_days': [...],  # YANGI - Dam olish/kasal kunlari
        'calculated_salary': float,
        'final_salary': float,
        'detailed_breakdown': {...}
    }
    """
    # Agar db berilmagan bo'lsa, yangi session ochish
    should_close_db = False
    if db is None:
        db = get_db()
        should_close_db = True

    try:
        # Base salary
        base_salary = employee.salary or 0
        salary_type = employee.salary_type or 'monthly'

        # ==========================================
        # YANGI: Dam olish va kasal kunlarini olish
        # ==========================================
        employee_leaves = get_employee_leaves_for_period(
            employee.id,
            employee.company_id,
            start_date,
            end_date,
            db  # db ni parametr sifatida berish
        )
        leave_dates = employee_leaves['dates']  # {'2025-01-15': 'rest', ...}

        logger.info(f"🏖️ {employee.full_name}: {employee_leaves['total_count']} ta dam olish/kasal kun")

        # Get attendance logs for the period
        attendance_logs = db.query(AttendanceLog).filter(
            AttendanceLog.employee_id == employee.id,
            AttendanceLog.date >= start_date,
            AttendanceLog.date <= end_date
        ).all()

        # Calculate worked days and hours
        worked_days = len(attendance_logs)
        total_work_minutes = sum(log.total_work_minutes or 0 for log in attendance_logs)
        total_work_hours = round(total_work_minutes / 60, 2)

        # Get late details per date
        late_details = []
        excused_days = []  # YANGI - Jarima hisoblanmagan kunlar
        total_late_minutes = 0

        # ==========================================
        # YANGI: Erta ketish ma'lumotlari
        # ==========================================
        early_leave_details = []
        total_early_leave_minutes = 0

        # Get employee schedule to check off days
        employee_schedules = db.query(EmployeeSchedule).filter_by(employee_id=employee.id).all()
        schedule_dict = {}
        for sched in employee_schedules:
            schedule_dict[sched.day_of_week] = sched.is_day_off

        # Default schedule if not set (Mon-Fri work, Sat-Sun off)
        if not schedule_dict:
            schedule_dict = {1: False, 2: False, 3: False, 4: False, 5: False, 6: True, 7: True}

        for log in attendance_logs:
            date_str = log.date.isoformat()
            day_of_week = log.date.isoweekday()  # 1=Mon, 7=Sun
            is_off_day = schedule_dict.get(day_of_week, False)
            is_before_hire = employee.hire_date and log.date < employee.hire_date
            is_leave_day = date_str in leave_dates
            leave_type = leave_dates.get(date_str, None)

            # ==========================================
            # KECHIKISH (LATE) HISOBLASH
            # ==========================================
            if log.late_minutes and log.late_minutes > 0:
                # Only count late minutes if:
                # 1. Not an off day
                # 2. Not before hire date
                # 3. NOT a leave day (rest or sick)
                if is_off_day:
                    excused_days.append({
                        'date': date_str,
                        'reason': 'off_day',
                        'reason_text': 'Dam olish kuni (jadval)',
                        'late_minutes': log.late_minutes,
                        'early_leave_minutes': 0,
                        'penalty_saved': 0  # Will be calculated below
                    })
                    logger.info(f"⚪ {log.date}: Late ignored - Off day")
                elif is_before_hire:
                    excused_days.append({
                        'date': date_str,
                        'reason': 'before_hire',
                        'reason_text': 'Ishga kirish sanasidan oldin',
                        'late_minutes': log.late_minutes,
                        'early_leave_minutes': 0,
                        'penalty_saved': 0
                    })
                    logger.info(f"⚪ {log.date}: Late ignored - Before hire date")
                elif is_leave_day:
                    # ==========================================
                    # YANGI: Dam olish/kasal kun - jarima yo'q
                    # ==========================================
                    reason_text = 'Dam olish kuni' if leave_type == 'rest' else 'Kasal kuni'
                    excused_days.append({
                        'date': date_str,
                        'reason': leave_type,  # 'rest' or 'sick'
                        'reason_text': reason_text,
                        'late_minutes': log.late_minutes,
                        'early_leave_minutes': 0,
                        'penalty_saved': 0  # Will be calculated below
                    })
                    logger.info(f"🏖️ {log.date}: Late ignored - {reason_text} ({log.late_minutes} min)")
                else:
                    # Normal work day - count late penalty
                    late_details.append({
                        'date': date_str,
                        'late_minutes': log.late_minutes,
                        'check_in_time': str(log.check_in_time) if log.check_in_time else None
                    })
                    total_late_minutes += log.late_minutes

            # ==========================================
            # ERTA KETISH (EARLY LEAVE) HISOBLASH
            # ==========================================
            if log.early_leave_minutes and log.early_leave_minutes > 0:
                # Erta ketish ham faqat ish kunlarida hisoblanadi
                if not is_off_day and not is_before_hire and not is_leave_day:
                    early_leave_details.append({
                        'date': date_str,
                        'early_leave_minutes': log.early_leave_minutes,
                        'check_out_time': str(log.check_out_time) if log.check_out_time else None,
                        'expected_end_time': company_settings.work_end_time if company_settings else '18:00'
                    })
                    total_early_leave_minutes += log.early_leave_minutes
                    logger.info(f"🔴 {log.date}: Erta ketish {log.early_leave_minutes} daqiqa")

        # ==========================================
        # YANGI: 3 BOSQICHLI KECHIKISH JARIMASI HISOBLASH
        # ==========================================
        # Kechikish kunlarini sana bo'yicha tartiblash
        late_details.sort(key=lambda x: x['date'])

        auto_late_penalty = 0
        late_penalty_per_minute = 0  # Legacy uchun

        # 3 bosqichli stavkalar
        late_penalty_first = 1000.0  # Default
        late_penalty_second = 3000.0
        late_penalty_third = 5000.0

        if company_settings:
            late_penalty_first = getattr(company_settings, 'late_penalty_first', 1000.0)
            late_penalty_second = getattr(company_settings, 'late_penalty_second', 3000.0)
            late_penalty_third = getattr(company_settings, 'late_penalty_third', 5000.0)
            # Legacy compatibility
            late_penalty_per_minute = getattr(company_settings, 'late_penalty_per_minute', 0)

        if company_settings and getattr(company_settings, 'auto_penalty_enabled', False):
            # Har bir kechikish kuni uchun bosqich bo'yicha jarima hisoblash
            for idx, detail in enumerate(late_details):
                late_count = idx + 1  # Nechinchi marta kechikish
                late_mins = detail['late_minutes']

                # Bosqich bo'yicha stavkani aniqlash
                if late_count == 1:
                    rate = late_penalty_first
                    tier = '1-bosqich'
                elif late_count == 2:
                    rate = late_penalty_second
                    tier = '2-bosqich'
                else:
                    rate = late_penalty_third
                    tier = '3-bosqich'

                # Bu kun uchun jarima
                day_penalty = late_mins * rate
                auto_late_penalty += day_penalty

                # Detail ga qo'shimcha ma'lumot qo'shish
                detail['late_count'] = late_count
                detail['tier'] = tier
                detail['rate_per_minute'] = rate
                detail['penalty_amount'] = round(day_penalty, 2)

                logger.info(
                    f"🔴 Kechikish #{late_count} ({tier}): {detail['date']} - "
                    f"{late_mins} min × {rate:,.0f} = {day_penalty:,.0f} so'm"
                )

            if auto_late_penalty > 0:
                logger.info(f"🔴 JAMI KECHIKISH JARIMASI: {auto_late_penalty:,.0f} so'm")

        # ==========================================
        # YANGI: Excused days uchun penalty_saved hisoblash
        # ==========================================
        # Excused kunlar uchun qancha jarima tejalganini hisoblash
        # (O'rtacha stavka asosida)
        avg_late_rate = (late_penalty_first + late_penalty_second + late_penalty_third) / 3
        for excused in excused_days:
            if excused.get('late_minutes', 0) > 0:
                excused['penalty_saved'] = round(excused['late_minutes'] * avg_late_rate, 2)

        # Get manual penalties
        penalties = db.query(Penalty).filter(
            Penalty.employee_id == employee.id,
            Penalty.date >= start_date,
            Penalty.date <= end_date,
            Penalty.is_waived == False,
            Penalty.is_excused == False
        ).all()

        manual_penalty_amount = sum(p.amount for p in penalties)

        # Get bonuses
        bonuses = db.query(Bonus).filter(
            Bonus.employee_id == employee.id,
            Bonus.date >= start_date,
            Bonus.date <= end_date
        ).all()

        total_bonus_amount = sum(b.amount for b in bonuses)

        # === PROFESSIONAL MONTHLY CALCULATION ===
        if salary_type == 'monthly':
            # STEP 1: Calculate DAILY RATE based on FULL MONTH
            full_month_work_days, schedule_dict_full = get_employee_expected_days(
                employee, start_date, end_date, for_daily_rate=True, db=db
            )

            if full_month_work_days > 0:
                daily_rate = base_salary / full_month_work_days
            else:
                daily_rate = 0

            # Get full month dates for breakdown
            full_month_start = date(start_date.year, start_date.month, 1)
            last_day = calendar.monthrange(start_date.year, start_date.month)[1]
            full_month_end = date(start_date.year, start_date.month, last_day)
            total_days_in_month = last_day
            off_days_in_month = total_days_in_month - full_month_work_days

            logger.info(f"📊 TO'LIQ OY ({full_month_start.strftime('%B %Y')}): {total_days_in_month} kun")
            logger.info(f"📊 Dam olish kunlari: {off_days_in_month} kun")
            logger.info(f"📊 Ish kunlari: {full_month_work_days} kun")
            logger.info(f"💵 KUNLIK STAVKA: {base_salary:,.0f} / {full_month_work_days} = {daily_rate:,.2f}")

            # STEP 2: Count work days in the ACTUAL PERIOD
            today = date.today()
            actual_end_date = min(end_date, today)

            # IMPORTANT: Adjust start date if before hire date
            effective_start_date = start_date
            if employee.hire_date and start_date < employee.hire_date:
                effective_start_date = max(start_date, employee.hire_date)
                logger.info(
                    f"⚪ Adjusted start: {start_date} → {effective_start_date} (hire date: {employee.hire_date})")

            period_expected_days, _ = get_employee_expected_days(
                employee, effective_start_date, actual_end_date, for_daily_rate=False, db=db
            )

            logger.info(f"📅 DAVR: {effective_start_date} to {actual_end_date}")
            logger.info(f"📅 Davr ish kunlari: {period_expected_days}")
            logger.info(f"✅ Ishlangan: {worked_days} kun")

            # STEP 3: Calculate salary for worked days
            calculated_salary = daily_rate * worked_days

            # ==========================================
            # YANGI: Absence penalty - dam olish/kasal kunlarni hisobga olmaslik
            # ==========================================
            # Dam olish va kasal kunlar soni
            leave_days_count = employee_leaves['total_count']

            # Haqiqiy yo'q bo'lgan kunlar = kutilgan - ishlangan - dam olish/kasal
            # Bu kunlar uchun JARIMA hisoblanadi
            actual_absence_days = max(0, period_expected_days - worked_days - leave_days_count)

            # Dam olish/kasal kunlar - bu kunlar uchun jarima YO'Q
            # Ular ishga kelgan deb hisoblanadi

            absence_penalty = 0
            absence_penalty_per_day = 0
            if company_settings and actual_absence_days > 0:
                absence_penalty_per_day = getattr(company_settings, 'absence_penalty_amount', 0)
                if absence_penalty_per_day > 0:
                    absence_penalty = actual_absence_days * absence_penalty_per_day
                    logger.info(
                        f"⚠️ Kelmaslik: {actual_absence_days} kun × {absence_penalty_per_day:,.0f} = {absence_penalty:,.0f}")
                    logger.info(
                        f"🏖️ Dam olish/kasal: {leave_days_count} kun - JARIMA YO'Q")

            # ==========================================
            # YANGI: Kelmagan kunlar uchun excused_days ga qo'shish
            # ==========================================
            # Qaysi kunlarda kelmagan va u dam olish/kasal deb belgilangan
            current_date = effective_start_date
            attendance_dates = {log.date for log in attendance_logs}

            while current_date <= actual_end_date:
                date_str = current_date.isoformat()
                day_of_week = current_date.isoweekday()
                is_schedule_off = schedule_dict.get(day_of_week, False)

                # Agar ish kuni bo'lsa va kelmagan bo'lsa
                if not is_schedule_off and current_date not in attendance_dates:
                    # Dam olish yoki kasal deb belgilanganmi?
                    if date_str in leave_dates:
                        leave_type = leave_dates[date_str]
                        reason_text = 'Dam olish kuni' if leave_type == 'rest' else 'Kasal kuni'

                        # Allaqachon qo'shilmaganligini tekshirish
                        already_added = any(e['date'] == date_str for e in excused_days)
                        if not already_added:
                            excused_days.append({
                                'date': date_str,
                                'reason': leave_type,
                                'reason_text': f"{reason_text} - kelmaslik jarimasi hisoblanmadi",
                                'late_minutes': 0,
                                'penalty_saved': absence_penalty_per_day,
                                'type': 'absence'  # Kelmagan kun
                            })
                            logger.info(f"🏖️ {current_date}: Absence excused - {reason_text}")

                current_date += timedelta(days=1)

            expected_days = period_expected_days
            absence_days = actual_absence_days  # Faqat haqiqiy yo'q kunlar

        else:
            # Daily salary
            calculated_salary = base_salary * worked_days
            daily_rate = base_salary
            absence_penalty = 0
            absence_days = 0
            expected_days = 0
            full_month_work_days = 0
            leave_days_count = employee_leaves['total_count']
            # Initialize variables for breakdown (not used for daily salary)
            full_month_start = start_date
            full_month_end = end_date
            actual_end_date = end_date

        # ==========================================
        # YANGI: ERTA KETISH JARIMASI HISOBLASH
        # ==========================================
        # Formula:
        # 1. Kunlik ish daqiqalari = daily_work_hours × 60
        # 2. Daqiqalik stavka = daily_rate / kunlik_ish_daqiqalari
        # 3. Erta ketish jarimasi = erta_ketgan_daqiqalar × daqiqalik_stavka
        #
        # Misol: Kunlik maosh 200,000 so'm, 8 soat ish = 480 daqiqa
        # Daqiqalik stavka = 200,000 / 480 = 416.67 so'm
        # 60 daqiqa erta ketsa = 60 × 416.67 = 25,000 so'm jarima

        early_leave_penalty = 0
        minute_rate = 0
        daily_work_minutes = 480  # Default 8 soat = 480 daqiqa

        # Erta ketish jarimasi yoqilganmi tekshirish
        early_leave_penalty_enabled = True  # Default yoqilgan
        if company_settings:
            early_leave_penalty_enabled = getattr(company_settings, 'early_leave_penalty_enabled', True)
            daily_work_hours = getattr(company_settings, 'daily_work_hours', 8)
            daily_work_minutes = daily_work_hours * 60

        if early_leave_penalty_enabled and daily_rate > 0 and daily_work_minutes > 0:
            # Daqiqalik stavkani hisoblash
            minute_rate = daily_rate / daily_work_minutes

            if total_early_leave_minutes > 0:
                early_leave_penalty = total_early_leave_minutes * minute_rate

                logger.info(f"=" * 50)
                logger.info(f"🕐 ERTA KETISH JARIMA HISOBLASH ({employee.full_name})")
                logger.info(f"   Kunlik stavka: {daily_rate:,.2f} so'm")
                logger.info(f"   Kunlik ish vaqti: {daily_work_minutes} daqiqa ({daily_work_minutes / 60:.0f} soat)")
                logger.info(f"   Daqiqalik stavka: {minute_rate:,.2f} so'm")
                logger.info(f"   Jami erta ketish: {total_early_leave_minutes} daqiqa")
                logger.info(
                    f"   JARIMA: {total_early_leave_minutes} × {minute_rate:,.2f} = {early_leave_penalty:,.2f} so'm")
                logger.info(f"=" * 50)

                # Har bir kun uchun jarima summasini qo'shish
                for detail in early_leave_details:
                    detail['minute_rate'] = round(minute_rate, 2)
                    detail['penalty_amount'] = round(detail['early_leave_minutes'] * minute_rate, 2)

        # Total penalty = manual + auto late + absence + EARLY LEAVE
        total_penalty_amount = manual_penalty_amount + auto_late_penalty + absence_penalty + early_leave_penalty

        # Final salary = calculated - penalties + bonuses
        final_salary = calculated_salary - total_penalty_amount + total_bonus_amount

        # Make sure final salary is not negative
        if final_salary < 0:
            final_salary = 0

        # ==========================================
        # YANGI: Excused summary
        # ==========================================
        total_penalty_saved = sum(e.get('penalty_saved', 0) for e in excused_days)

        return {
            'base_salary': base_salary,
            'salary_type': salary_type,
            'calculation_method': 'full_month_based' if salary_type == 'monthly' else 'daily',

            # Full month data (for monthly)
            'full_month_work_days': full_month_work_days,
            'daily_rate': round(daily_rate, 2),

            # Period data
            'period_work_days': expected_days,
            'worked_days': worked_days,
            'expected_days': expected_days,
            'absence_days': absence_days,

            # ==========================================
            # YANGI: Dam olish/kasal kunlar
            # ==========================================
            'leave_days': {
                'rest_count': employee_leaves['rest_count'],
                'sick_count': employee_leaves['sick_count'],
                'total_count': employee_leaves['total_count']
            },

            # Work details
            'total_work_hours': total_work_hours,

            # Penalties breakdown
            'late_minutes': total_late_minutes,
            'late_details': late_details,
            'late_penalty_per_minute': late_penalty_per_minute,  # Legacy
            'auto_late_penalty': round(auto_late_penalty, 2),

            # ==========================================
            # YANGI: 3 BOSQICHLI KECHIKISH JARIMASI
            # ==========================================
            'late_penalty_tiers': {
                'first': late_penalty_first,
                'second': late_penalty_second,
                'third': late_penalty_third
            },
            'late_days_count': len(late_details),  # Necha kun kechikdi

            # ==========================================
            # YANGI: ERTA KETISH JARIMASI
            # ==========================================
            'early_leave_minutes': total_early_leave_minutes,
            'early_leave_details': early_leave_details,
            'early_leave_minute_rate': round(minute_rate, 2),
            'early_leave_penalty': round(early_leave_penalty, 2),
            'daily_work_minutes': daily_work_minutes,

            'absence_penalty': round(absence_penalty, 2),
            'manual_penalty': round(manual_penalty_amount, 2),
            'penalty_count': len(penalties),
            'penalty_amount': round(total_penalty_amount, 2),

            # ==========================================
            # YANGI: Jarima hisoblanmagan kunlar
            # ==========================================
            'excused_days': excused_days,
            'excused_summary': {
                'total_days': len(excused_days),
                'rest_days': len([e for e in excused_days if e.get('reason') == 'rest']),
                'sick_days': len([e for e in excused_days if e.get('reason') == 'sick']),
                'off_days': len([e for e in excused_days if e.get('reason') == 'off_day']),
                'total_penalty_saved': round(total_penalty_saved, 2)
            },

            # Bonuses
            'bonus_count': len(bonuses),
            'bonus_amount': round(total_bonus_amount, 2),

            # Calculations
            'calculated_salary': round(calculated_salary, 2),
            'final_salary': round(final_salary, 2),

            # Detailed breakdown for modal
            'detailed_breakdown': {
                'step_1_full_month': {
                    'month': f"{full_month_start.strftime('%B %Y')}" if salary_type == 'monthly' else None,
                    'total_days': (full_month_end - full_month_start).days + 1 if salary_type == 'monthly' else None,
                    'work_days': full_month_work_days,
                    'off_days': ((
                                         full_month_end - full_month_start).days + 1 - full_month_work_days) if salary_type == 'monthly' else None
                },
                'step_2_daily_rate': {
                    'base_salary': base_salary,
                    'work_days': full_month_work_days,
                    'daily_rate': round(daily_rate, 2),
                    'formula': f"{base_salary:,.0f} / {full_month_work_days} = {daily_rate:,.2f}" if full_month_work_days > 0 else None
                },
                'step_3_period_calculation': {
                    'period': f"{start_date} to {actual_end_date if salary_type == 'monthly' else end_date}",
                    'expected_work_days': expected_days,
                    'worked_days': worked_days,
                    'absence_days': absence_days,
                    # YANGI
                    'leave_days': employee_leaves['total_count'],
                    'leave_note': f"Dam olish: {employee_leaves['rest_count']}, Kasal: {employee_leaves['sick_count']} - jarima hisoblanmadi" if
                    employee_leaves['total_count'] > 0 else None
                },
                'step_4_gross_salary': {
                    'daily_rate': round(daily_rate, 2),
                    'worked_days': worked_days,
                    'amount': round(calculated_salary, 2),
                    'formula': f"{daily_rate:,.2f} × {worked_days} = {calculated_salary:,.2f}"
                },
                'step_5_deductions': {
                    'late_penalty': {
                        'total_minutes': total_late_minutes,
                        'rate_per_minute': late_penalty_per_minute,
                        'amount': round(auto_late_penalty, 2),
                        'details': late_details,
                        # YANGI
                        'excused_note': f"{len([e for e in excused_days if e.get('late_minutes', 0) > 0])} kun kechikish jarima hisoblanmadi" if any(
                            e.get('late_minutes', 0) > 0 for e in excused_days) else None
                    },
                    # ==========================================
                    # YANGI: ERTA KETISH JARIMASI
                    # ==========================================
                    'early_leave_penalty': {
                        'total_minutes': total_early_leave_minutes,
                        'daily_work_minutes': daily_work_minutes,
                        'daily_rate': round(daily_rate, 2),
                        'minute_rate': round(minute_rate, 2),
                        'amount': round(early_leave_penalty, 2),
                        'details': early_leave_details,
                        'formula': f"{daily_rate:,.2f} / {daily_work_minutes} = {minute_rate:,.2f} so'm/daqiqa" if minute_rate > 0 else None,
                        'calculation': f"{total_early_leave_minutes} daqiqa × {minute_rate:,.2f} = {early_leave_penalty:,.2f} so'm" if early_leave_penalty > 0 else None
                    },
                    'absence_penalty': {
                        'days': absence_days,
                        'rate_per_day': getattr(company_settings, 'absence_penalty_amount',
                                                0) if company_settings else 0,
                        'amount': round(absence_penalty, 2),
                        # YANGI
                        'excused_note': f"{employee_leaves['total_count']} kun kelmaslik jarima hisoblanmadi (dam olish/kasal)" if
                        employee_leaves['total_count'] > 0 else None
                    },
                    'manual_penalties': {
                        'count': len(penalties),
                        'amount': round(manual_penalty_amount, 2)
                    },
                    'total_deductions': round(total_penalty_amount, 2)
                },
                'step_6_bonuses': {
                    'count': len(bonuses),
                    'amount': round(total_bonus_amount, 2)
                },
                'step_7_final': {
                    'gross_salary': round(calculated_salary, 2),
                    'deductions': round(total_penalty_amount, 2),
                    'bonuses': round(total_bonus_amount, 2),
                    'net_salary': round(final_salary, 2),
                    'formula': f"{calculated_salary:,.2f} - {total_penalty_amount:,.2f} + {total_bonus_amount:,.2f} = {final_salary:,.2f}"
                },
                # YANGI
                'step_8_excused_summary': {
                    'title': 'Jarima hisoblanmagan kunlar',
                    'days': excused_days,
                    'total_saved': round(total_penalty_saved, 2),
                    'note': f"Jami {len(excused_days)} kun uchun {total_penalty_saved:,.0f} so'm jarima hisoblanmadi" if excused_days else "Barcha kunlar uchun jarima hisoblanadi"
                }
            }
        }

    finally:
        # Faqat bu funksiya ochgan bo'lsa yopish
        if should_close_db:
            db.close()


@salary_bp.route('/calculate', methods=['POST'])
@require_auth
@load_company_context
def calculate_salary():
    """
    Xodim oyligini hisoblash

    Body: {
        "employee_id": "...",
        "start_date": "2025-01-01",
        "end_date": "2025-01-31"
    }

    Response: {
        "employee": {...},
        "period": {...},
        "salary": {
            "base_salary": 3000000,
            "salary_type": "monthly",
            "worked_days": 22,
            "total_work_hours": 176,
            "late_minutes": 600,
            "penalty_amount": 200000,
            "bonus_amount": 50000,
            "final_salary": 2850000,
            "excused_days": [...],  // YANGI
            "leave_days": {...}     // YANGI
        }
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        employee_id = data.get('employee_id')
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')

        if not employee_id or not start_date_str or not end_date_str:
            return error_response("employee_id, start_date, and end_date are required", 400)

        # Parse dates
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        # Verify employee
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        # Get company settings
        company_settings = db.query(CompanySettings).filter_by(
            company_id=g.company_id
        ).first()

        # Calculate salary - db ni parametr sifatida berish
        salary_result = calculate_employee_salary(employee, start_date, end_date, company_settings, db)

        return success_response({
            'employee': {
                'id': employee.id,
                'employee_no': employee.employee_no,
                'full_name': employee.full_name,
                'position': employee.position,
                'branch_name': employee.branch.name if employee.branch else None,
                'department_name': employee.department.name if employee.department else None
            },
            'period': {
                'start_date': start_date_str,
                'end_date': end_date_str,
                'total_days': (end_date - start_date).days + 1
            },
            'salary': salary_result
        })

    except Exception as e:
        logger.error(f"Error calculating salary: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/bulk-calculate', methods=['POST'])
@require_auth
@load_company_context
def bulk_calculate_salary():
    """
    Bir nechta xodim uchun oylik hisoblash

    Body: {
        "employee_ids": ["id1", "id2", ...],  // Optional - agar bo'lmasa barcha xodimlar
        "start_date": "2025-01-01",
        "end_date": "2025-01-31",
        "branch_id": "..."  // Optional
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')

        if not start_date_str or not end_date_str:
            return error_response("start_date and end_date are required", 400)

        # Parse dates
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        # Get company settings
        company_settings = db.query(CompanySettings).filter_by(
            company_id=g.company_id
        ).first()

        # Get employees
        employee_ids = data.get('employee_ids')
        branch_id = data.get('branch_id')

        query = db.query(Employee).filter_by(
            company_id=g.company_id,
            status='active'
        )

        if employee_ids:
            query = query.filter(Employee.id.in_(employee_ids))

        if branch_id:
            query = query.filter_by(branch_id=branch_id)

        employees = query.all()

        if not employees:
            return error_response("No employees found", 404)

        # Calculate salary for each employee
        results = []
        total_salaries = 0
        total_penalties = 0
        total_bonuses = 0
        total_excused_days = 0  # YANGI

        for employee in employees:
            salary_result = calculate_employee_salary(employee, start_date, end_date, company_settings, db)

            results.append({
                'employee_id': employee.id,
                'employee_no': employee.employee_no,
                'full_name': employee.full_name,
                'branch_name': employee.branch.name if employee.branch else None,
                'department_name': employee.department.name if employee.department else None,
                'salary': salary_result
            })

            total_salaries += salary_result['final_salary']
            total_penalties += salary_result['penalty_amount']
            total_bonuses += salary_result['bonus_amount']
            total_excused_days += len(salary_result.get('excused_days', []))  # YANGI

        return success_response({
            'period': {
                'start_date': start_date_str,
                'end_date': end_date_str
            },
            'employees': results,
            'summary': {
                'total_employees': len(results),
                'total_salaries': round(total_salaries, 2),
                'total_penalties': round(total_penalties, 2),
                'total_bonuses': round(total_bonuses, 2),
                'total_excused_days': total_excused_days  # YANGI
            }
        })

    except Exception as e:
        logger.error(f"Error in bulk salary calculation: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/employee/<employee_id>/history', methods=['GET'])
@require_auth
@load_company_context
def get_salary_history(employee_id):
    """
    Xodimning oylik tarixi

    Query params:
    - months: Oxirgi nechta oy (default: 6)
    """
    db = get_db()

    try:
        # Verify employee
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        months = int(request.args.get('months', 6))

        # Get company settings
        company_settings = db.query(CompanySettings).filter_by(
            company_id=g.company_id
        ).first()

        # Calculate salary for each month
        history = []
        today = date.today()

        for i in range(months):
            # Calculate month start and end
            target_date = today - timedelta(days=30 * i)
            month_start = date(target_date.year, target_date.month, 1)
            last_day = calendar.monthrange(target_date.year, target_date.month)[1]
            month_end = date(target_date.year, target_date.month, last_day)

            # Calculate salary for this month
            salary_result = calculate_employee_salary(employee, month_start, month_end, company_settings, db)

            history.append({
                'month': month_start.strftime('%Y-%m'),
                'month_name': month_start.strftime('%B %Y'),
                'salary': salary_result
            })

        return success_response({
            'employee': {
                'id': employee.id,
                'employee_no': employee.employee_no,
                'full_name': employee.full_name,
                'position': employee.position
            },
            'history': history
        })

    except Exception as e:
        logger.error(f"Error getting salary history: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/penalties', methods=['GET'])
@require_auth
@load_company_context
def get_penalties():
    """
    Jarimalar ro'yxati

    Query params:
    - start_date, end_date
    - employee_id (optional)
    - penalty_type (optional): 'late', 'absence', 'manual'
    - page, per_page
    """
    db = get_db()

    try:
        # Parse dates
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if not start_date_str or not end_date_str:
            return error_response("start_date and end_date are required", 400)

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        # Build query
        query = db.query(Penalty).filter(
            Penalty.company_id == g.company_id,
            Penalty.date >= start_date,
            Penalty.date <= end_date
        )

        # Filters
        employee_id = request.args.get('employee_id')
        if employee_id:
            query = query.filter_by(employee_id=employee_id)

        penalty_type = request.args.get('penalty_type')
        if penalty_type:
            query = query.filter_by(penalty_type=penalty_type)

        # Pagination
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))

        total = query.count()
        penalties = query.order_by(Penalty.date.desc()).offset((page - 1) * per_page).limit(per_page).all()

        return success_response({
            'penalties': [p.to_dict() for p in penalties],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })

    except Exception as e:
        logger.error(f"Error getting penalties: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/penalties', methods=['POST'])
@require_auth
@load_company_context
def create_penalty():
    """
    Yangi jarima yaratish (manual penalty)

    Body: {
        "employee_id": "...",
        "amount": 50000,
        "reason": "...",
        "date": "2025-01-15"
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        employee_id = data.get('employee_id')
        amount = data.get('amount')
        reason = data.get('reason', '')
        date_str = data.get('date')

        if not employee_id or not amount or not date_str:
            return error_response("employee_id, amount, and date are required", 400)

        # Verify employee
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        # Create penalty
        penalty = Penalty(
            company_id=g.company_id,
            employee_id=employee_id,
            penalty_type='manual',
            amount=float(amount),
            reason=reason,
            date=datetime.strptime(date_str, '%Y-%m-%d').date()
        )

        db.add(penalty)
        db.commit()

        return success_response({
            'penalty': penalty.to_dict()
        }, 201)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating penalty: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/penalties/<penalty_id>/waive', methods=['POST'])
@require_auth
@load_company_context
def waive_penalty(penalty_id):
    """
    Jarimani bekor qilish (waive)

    Body: {
        "reason": "Sabab..."
    }
    """
    db = get_db()

    try:
        penalty = db.query(Penalty).filter_by(
            id=penalty_id,
            company_id=g.company_id
        ).first()

        if not penalty:
            return error_response("Penalty not found", 404)

        data = request.get_json() or {}

        penalty.is_waived = True
        penalty.waive_reason = data.get('reason', '')
        penalty.waived_by = g.admin_id if hasattr(g, 'admin_id') else None
        penalty.waived_at = datetime.now()

        db.commit()

        return success_response({
            'penalty': penalty.to_dict()
        })

    except Exception as e:
        db.rollback()
        logger.error(f"Error waiving penalty: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/bonuses', methods=['GET'])
@require_auth
@load_company_context
def get_bonuses():
    """
    Bonuslar ro'yxati
    """
    db = get_db()

    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if not start_date_str or not end_date_str:
            return error_response("start_date and end_date are required", 400)

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        query = db.query(Bonus).filter(
            Bonus.company_id == g.company_id,
            Bonus.date >= start_date,
            Bonus.date <= end_date
        )

        employee_id = request.args.get('employee_id')
        if employee_id:
            query = query.filter_by(employee_id=employee_id)

        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))

        total = query.count()
        bonuses = query.order_by(Bonus.date.desc()).offset((page - 1) * per_page).limit(per_page).all()

        return success_response({
            'bonuses': [b.to_dict() for b in bonuses],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })

    except Exception as e:
        logger.error(f"Error getting bonuses: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/bonuses', methods=['POST'])
@require_auth
@load_company_context
def create_bonus():
    """
    Yangi bonus yaratish

    Body: {
        "employee_id": "...",
        "amount": 100000,
        "reason": "...",
        "date": "2025-01-15",
        "bonus_type": "manual"  // 'perfect_attendance', 'early_arrival', 'overtime', 'manual'
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        employee_id = data.get('employee_id')
        amount = data.get('amount')
        reason = data.get('reason', '')
        date_str = data.get('date')
        bonus_type = data.get('bonus_type', 'manual')

        if not employee_id or not amount or not date_str:
            return error_response("employee_id, amount, and date are required", 400)

        # Verify employee
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        # Create bonus
        bonus = Bonus(
            company_id=g.company_id,
            employee_id=employee_id,
            bonus_type=bonus_type,
            amount=float(amount),
            reason=reason,
            date=datetime.strptime(date_str, '%Y-%m-%d').date(),
            given_by=g.admin_id if hasattr(g, 'admin_id') else None
        )

        db.add(bonus)
        db.commit()

        return success_response({
            'bonus': bonus.to_dict()
        }, 201)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating bonus: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/attendance-ranking', methods=['GET'])
@require_auth
@load_company_context
def get_attendance_ranking():
    """
    Davomat bo'yicha reyting (eng yaxshi xodimlar)

    Query params:
    - start_date, end_date
    - limit (default: 20)
    - branch_id (optional)
    """
    db = get_db()

    try:
        limit = int(request.args.get('limit', 20))

        # Date filters
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        if not start_date or not end_date:
            return error_response("start_date and end_date are required", 400)

        start = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()

        # Get employees
        query = db.query(Employee).filter_by(
            company_id=g.company_id,
            status='active'
        )

        branch_id = request.args.get('branch_id')
        if branch_id:
            query = query.filter_by(branch_id=branch_id)

        employees = query.all()

        # Calculate attendance metrics for each employee
        ranking_data = []

        for employee in employees:
            # Get attendance logs
            logs = db.query(AttendanceLog).filter(
                AttendanceLog.employee_id == employee.id,
                AttendanceLog.date >= start,
                AttendanceLog.date <= end
            ).all()

            if not logs:
                continue

            total_days = len(logs)
            on_time_days = sum(1 for log in logs if not log.late_minutes or log.late_minutes == 0)
            late_days = total_days - on_time_days
            total_late_minutes = sum(log.late_minutes or 0 for log in logs)

            # Calculate attendance rate (on time %)
            attendance_rate = (on_time_days / total_days * 100) if total_days > 0 else 0

            # Get bonuses
            bonus_amount = db.query(func.sum(Bonus.amount)).filter(
                Bonus.employee_id == employee.id,
                Bonus.date >= start,
                Bonus.date <= end
            ).scalar() or 0

            ranking_data.append({
                'employee_id': employee.id,
                'employee_no': employee.employee_no,
                'full_name': employee.full_name,
                'position': employee.position,
                'branch_id': employee.branch_id,
                'total_days': total_days,
                'on_time_days': on_time_days,
                'late_days': late_days,
                'total_late_minutes': total_late_minutes,
                'attendance_rate': round(attendance_rate, 2),
                'bonus_amount': float(bonus_amount)
            })

        # Sort by attendance rate (highest first), then by total days (more = better)
        ranking_data.sort(key=lambda x: (x['attendance_rate'], x['total_days']), reverse=True)

        # Add rank
        for idx, item in enumerate(ranking_data[:limit], 1):
            item['rank'] = idx

        return success_response({
            'ranking': ranking_data[:limit],
            'total_count': len(ranking_data)
        })

    except Exception as e:
        logger.error(f"Error getting attendance ranking: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/payroll-summary', methods=['GET'])
@require_auth
@load_company_context
def get_payroll_summary():
    """
    Umumiy maosh xulosasi (company/branch level)

    Query params:
    - start_date, end_date
    - branch_id (optional)

    Response: {
        "total_employees": 50,
        "total_base_salary": 150000000,
        "total_penalties": 5000000,
        "total_bonuses": 3000000,
        "total_payroll": 148000000,
        "total_excused_days": 25,  // YANGI
        "by_branch": [...],
        "by_department": [...]
    }
    """
    db = get_db()

    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if not start_date_str or not end_date_str:
            return error_response("start_date and end_date are required", 400)

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        # Get company settings
        company_settings = db.query(CompanySettings).filter_by(
            company_id=g.company_id
        ).first()

        # Get employees
        query = db.query(Employee).filter_by(
            company_id=g.company_id,
            status='active'
        )

        branch_id = request.args.get('branch_id')
        if branch_id:
            query = query.filter_by(branch_id=branch_id)

        employees = query.all()

        # Calculate totals
        total_employees = len(employees)
        total_base_salary = sum(emp.salary or 0 for emp in employees)
        total_payroll = 0
        total_penalties = 0
        total_bonuses = 0
        total_excused_days = 0  # YANGI
        total_penalty_saved = 0  # YANGI

        by_branch = {}
        by_department = {}

        for employee in employees:
            salary_result = calculate_employee_salary(employee, start_date, end_date, company_settings, db)

            total_payroll += salary_result['final_salary']
            total_penalties += salary_result['penalty_amount']
            total_bonuses += salary_result['bonus_amount']
            total_excused_days += len(salary_result.get('excused_days', []))  # YANGI
            total_penalty_saved += salary_result.get('excused_summary', {}).get('total_penalty_saved', 0)  # YANGI

            # Group by branch
            branch_key = employee.branch.name if employee.branch else 'No Branch'
            if branch_key not in by_branch:
                by_branch[branch_key] = {
                    'employee_count': 0,
                    'total_salary': 0,
                    'total_penalties': 0,
                    'total_bonuses': 0,
                    'total_excused_days': 0  # YANGI
                }
            by_branch[branch_key]['employee_count'] += 1
            by_branch[branch_key]['total_salary'] += salary_result['final_salary']
            by_branch[branch_key]['total_penalties'] += salary_result['penalty_amount']
            by_branch[branch_key]['total_bonuses'] += salary_result['bonus_amount']
            by_branch[branch_key]['total_excused_days'] += len(salary_result.get('excused_days', []))  # YANGI

            # Group by department
            dept_key = employee.department.name if employee.department else 'No Department'
            if dept_key not in by_department:
                by_department[dept_key] = {
                    'employee_count': 0,
                    'total_salary': 0,
                    'total_penalties': 0,
                    'total_bonuses': 0,
                    'total_excused_days': 0  # YANGI
                }
            by_department[dept_key]['employee_count'] += 1
            by_department[dept_key]['total_salary'] += salary_result['final_salary']
            by_department[dept_key]['total_penalties'] += salary_result['penalty_amount']
            by_department[dept_key]['total_bonuses'] += salary_result['bonus_amount']
            by_department[dept_key]['total_excused_days'] += len(salary_result.get('excused_days', []))  # YANGI

        # Convert to lists
        by_branch_list = [{'name': k, **v} for k, v in by_branch.items()]
        by_department_list = [{'name': k, **v} for k, v in by_department.items()]

        return success_response({
            'period': {
                'start_date': start_date_str,
                'end_date': end_date_str
            },
            'summary': {
                'total_employees': total_employees,
                'total_base_salary': total_base_salary,
                'total_penalties': total_penalties,
                'total_bonuses': total_bonuses,
                'total_payroll': total_payroll,
                # YANGI
                'total_excused_days': total_excused_days,
                'total_penalty_saved': round(total_penalty_saved, 2)
            },
            'by_branch': by_branch_list,
            'by_department': by_department_list
        })

    except Exception as e:
        logger.error(f"Error getting payroll summary: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


# ==========================================
# EXCEL EXPORT - OYLIK HISOBOT
# ==========================================
@salary_bp.route('/export', methods=['GET'])
@require_auth
@load_company_context
def export_salary_excel():
    """
    Oylik hisobotni Excel formatida yuklab olish

    Query params:
    - start_date: Boshlanish sanasi (YYYY-MM-DD)
    - end_date: Tugash sanasi (YYYY-MM-DD)
    - branch_id: Filial ID (optional)
    """
    db = get_db()
    try:
        # Get parameters
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        branch_id = request.args.get('branch_id')

        if not start_date_str or not end_date_str:
            return error_response("start_date va end_date kerak", 400)

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        # Get company settings
        company_settings = db.query(CompanySettings).filter_by(company_id=g.company_id).first()

        # Get employees query
        query = db.query(Employee).filter(
            Employee.company_id == g.company_id,
            Employee.status == 'active'
        )
        if branch_id:
            query = query.filter(Employee.branch_id == branch_id)

        employees = query.options(joinedload(Employee.branch)).all()

        # Calculate salary for each employee
        salary_data = []
        totals = {
            'base_salary': 0,
            'calculated_salary': 0,
            'late_penalty': 0,
            'early_leave_penalty': 0,
            'absence_penalty': 0,
            'manual_penalty': 0,
            'total_penalty': 0,
            'bonus': 0,
            'final_salary': 0
        }

        for employee in employees:
            salary = calculate_employee_salary(employee, start_date, end_date, company_settings, db)

            emp_data = {
                'full_name': employee.full_name,
                'employee_no': employee.employee_no or '',
                'branch_name': employee.branch.name if employee.branch else '-',
                'position': employee.position or '-',
                'base_salary': salary['base_salary'],
                'daily_rate': salary['daily_rate'],
                'worked_days': salary['worked_days'],
                'expected_days': salary['expected_days'],
                'late_minutes': salary['late_minutes'],
                'late_penalty': salary['auto_late_penalty'],
                'early_leave_minutes': salary.get('early_leave_minutes', 0),
                'early_leave_penalty': salary.get('early_leave_penalty', 0),
                'early_leave_minute_rate': salary.get('early_leave_minute_rate', 0),
                'absence_days': salary['absence_days'],
                'absence_penalty': salary['absence_penalty'],
                'manual_penalty': salary['manual_penalty'],
                'total_penalty': salary['penalty_amount'],
                'bonus': salary['bonus_amount'],
                'calculated_salary': salary['calculated_salary'],
                'final_salary': salary['final_salary']
            }
            salary_data.append(emp_data)

            # Update totals
            totals['base_salary'] += salary['base_salary']
            totals['calculated_salary'] += salary['calculated_salary']
            totals['late_penalty'] += salary['auto_late_penalty']
            totals['early_leave_penalty'] += salary.get('early_leave_penalty', 0)
            totals['absence_penalty'] += salary['absence_penalty']
            totals['manual_penalty'] += salary['manual_penalty']
            totals['total_penalty'] += salary['penalty_amount']
            totals['bonus'] += salary['bonus_amount']
            totals['final_salary'] += salary['final_salary']

        # Create Excel file
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})

        # ==========================================
        # SHEET 1: OYLIK JADVALI
        # ==========================================
        ws_salary = workbook.add_worksheet('Oylik jadvali')

        # Formats
        title_fmt = workbook.add_format({
            'bold': True, 'font_size': 16, 'align': 'center', 'valign': 'vcenter',
            'font_name': 'Arial'
        })
        header_fmt = workbook.add_format({
            'bold': True, 'font_color': 'white', 'bg_color': '#1F4E78',
            'font_name': 'Arial', 'font_size': 10, 'align': 'center',
            'valign': 'vcenter', 'border': 1, 'text_wrap': True
        })
        cell_fmt = workbook.add_format({
            'font_name': 'Arial', 'font_size': 10, 'border': 1, 'valign': 'vcenter'
        })
        cell_center = workbook.add_format({
            'font_name': 'Arial', 'font_size': 10, 'border': 1,
            'align': 'center', 'valign': 'vcenter'
        })
        money_fmt = workbook.add_format({
            'font_name': 'Arial', 'font_size': 10, 'border': 1,
            'num_format': '#,##0', 'align': 'right', 'valign': 'vcenter'
        })
        penalty_fmt = workbook.add_format({
            'font_name': 'Arial', 'font_size': 10, 'border': 1,
            'num_format': '#,##0', 'align': 'right', 'valign': 'vcenter',
            'font_color': 'red'
        })
        bonus_fmt = workbook.add_format({
            'font_name': 'Arial', 'font_size': 10, 'border': 1,
            'num_format': '#,##0', 'align': 'right', 'valign': 'vcenter',
            'font_color': 'green'
        })
        total_fmt = workbook.add_format({
            'bold': True, 'font_name': 'Arial', 'font_size': 10, 'border': 2,
            'num_format': '#,##0', 'align': 'right', 'valign': 'vcenter',
            'bg_color': '#E2EFDA'
        })
        total_label_fmt = workbook.add_format({
            'bold': True, 'font_name': 'Arial', 'font_size': 10, 'border': 2,
            'align': 'center', 'valign': 'vcenter', 'bg_color': '#E2EFDA'
        })

        # Title
        ws_salary.merge_range('A1:P1', f'OYLIK HISOBOT: {start_date_str} - {end_date_str}', title_fmt)
        ws_salary.set_row(0, 30)

        # Headers
        headers = [
            '№', 'Xodim', 'ID', 'Filial', 'Lavozim',
            'Asosiy\noylik', 'Kunlik\nstavka', 'Ishlangan\nkun', 'Kutilgan\nkun',
            'Kechikish\n(daq)', 'Kechikish\njarimasi',
            'Erta ketish\n(daq)', 'Erta ketish\njarimasi',
            'Jami\njarima', 'Bonus', 'Yakuniy\noylik'
        ]
        widths = [4, 25, 10, 15, 15, 15, 12, 10, 10, 10, 12, 10, 12, 12, 12, 15]

        for col, width in enumerate(widths):
            ws_salary.set_column(col, col, width)

        ws_salary.set_row(2, 40)
        for col, header in enumerate(headers):
            ws_salary.write(2, col, header, header_fmt)

        # Data rows
        for row, emp in enumerate(salary_data, start=3):
            ws_salary.set_row(row, 22)
            ws_salary.write(row, 0, row - 2, cell_center)
            ws_salary.write(row, 1, emp['full_name'], cell_fmt)
            ws_salary.write(row, 2, emp['employee_no'], cell_center)
            ws_salary.write(row, 3, emp['branch_name'], cell_fmt)
            ws_salary.write(row, 4, emp['position'], cell_fmt)
            ws_salary.write(row, 5, emp['base_salary'], money_fmt)
            ws_salary.write(row, 6, emp['daily_rate'], money_fmt)
            ws_salary.write(row, 7, emp['worked_days'], cell_center)
            ws_salary.write(row, 8, emp['expected_days'], cell_center)
            ws_salary.write(row, 9, emp['late_minutes'], cell_center)
            ws_salary.write(row, 10, emp['late_penalty'], penalty_fmt if emp['late_penalty'] > 0 else money_fmt)
            ws_salary.write(row, 11, emp['early_leave_minutes'], cell_center)
            ws_salary.write(row, 12, emp['early_leave_penalty'],
                            penalty_fmt if emp['early_leave_penalty'] > 0 else money_fmt)
            ws_salary.write(row, 13, emp['total_penalty'], penalty_fmt if emp['total_penalty'] > 0 else money_fmt)
            ws_salary.write(row, 14, emp['bonus'], bonus_fmt if emp['bonus'] > 0 else money_fmt)
            ws_salary.write(row, 15, emp['final_salary'], money_fmt)

        # Totals row
        total_row = len(salary_data) + 3
        ws_salary.set_row(total_row, 25)
        ws_salary.merge_range(total_row, 0, total_row, 4, 'JAMI:', total_label_fmt)
        ws_salary.write(total_row, 5, totals['base_salary'], total_fmt)
        ws_salary.write(total_row, 6, '', total_fmt)
        ws_salary.write(total_row, 7, '', total_fmt)
        ws_salary.write(total_row, 8, '', total_fmt)
        ws_salary.write(total_row, 9, '', total_fmt)
        ws_salary.write(total_row, 10, totals['late_penalty'], total_fmt)
        ws_salary.write(total_row, 11, '', total_fmt)
        ws_salary.write(total_row, 12, totals['early_leave_penalty'], total_fmt)
        ws_salary.write(total_row, 13, totals['total_penalty'], total_fmt)
        ws_salary.write(total_row, 14, totals['bonus'], total_fmt)
        ws_salary.write(total_row, 15, totals['final_salary'], total_fmt)

        # ==========================================
        # SHEET 2: ERTA KETISH TAFSILOTLARI
        # ==========================================
        ws_early = workbook.add_worksheet('Erta ketish tafsilotlari')

        ws_early.merge_range('A1:G1', 'ERTA KETISH JARIMALARI TAFSILOTI', title_fmt)
        ws_early.set_row(0, 30)

        early_headers = ['№', 'Xodim', 'Filial', 'Kunlik stavka', 'Daqiqalik stavka', 'Jami erta ketish (daq)',
                         'Jarima summasi']
        early_widths = [4, 25, 15, 15, 15, 18, 15]

        for col, width in enumerate(early_widths):
            ws_early.set_column(col, col, width)

        ws_early.set_row(2, 30)
        for col, header in enumerate(early_headers):
            ws_early.write(2, col, header, header_fmt)

        row_num = 3
        for idx, emp in enumerate(salary_data, start=1):
            if emp['early_leave_minutes'] > 0:
                ws_early.write(row_num, 0, idx, cell_center)
                ws_early.write(row_num, 1, emp['full_name'], cell_fmt)
                ws_early.write(row_num, 2, emp['branch_name'], cell_fmt)
                ws_early.write(row_num, 3, emp['daily_rate'], money_fmt)
                ws_early.write(row_num, 4, emp['early_leave_minute_rate'], money_fmt)
                ws_early.write(row_num, 5, emp['early_leave_minutes'], cell_center)
                ws_early.write(row_num, 6, emp['early_leave_penalty'], penalty_fmt)
                row_num += 1

        if row_num == 3:
            ws_early.write(3, 0, 'Erta ketish ma\'lumotlari yo\'q', cell_fmt)

        # ==========================================
        # SHEET 3: XULOSA
        # ==========================================
        ws_summary = workbook.add_worksheet('Xulosa')

        summary_title = workbook.add_format({
            'bold': True, 'font_size': 14, 'font_name': 'Arial'
        })
        summary_label = workbook.add_format({
            'font_name': 'Arial', 'font_size': 11, 'bold': True
        })
        summary_value = workbook.add_format({
            'font_name': 'Arial', 'font_size': 11, 'num_format': '#,##0'
        })

        ws_summary.set_column(0, 0, 30)
        ws_summary.set_column(1, 1, 20)

        ws_summary.write('A1', 'OYLIK HISOBOT XULOSASI', summary_title)
        ws_summary.write('A2', f'Davr: {start_date_str} - {end_date_str}')

        ws_summary.write('A4', 'Ko\'rsatkich', summary_label)
        ws_summary.write('B4', 'Qiymat', summary_label)

        summary_data = [
            ('Jami xodimlar soni', len(salary_data)),
            ('Jami asosiy oylik', totals['base_salary']),
            ('Jami kechikish jarimasi', totals['late_penalty']),
            ('Jami erta ketish jarimasi', totals['early_leave_penalty']),
            ('Jami kelmaslik jarimasi', totals['absence_penalty']),
            ('Jami qo\'lda jarimalar', totals['manual_penalty']),
            ('JAMI JARIMALAR', totals['total_penalty']),
            ('Jami bonuslar', totals['bonus']),
            ('YAKUNIY TO\'LOV', totals['final_salary']),
        ]

        for i, (label, value) in enumerate(summary_data, start=5):
            ws_summary.write(f'A{i}', label, summary_label)
            ws_summary.write(f'B{i}', value, summary_value)

        workbook.close()
        output.seek(0)

        filename = f"Oylik_hisobot_{start_date_str}_{end_date_str}.xlsx"

        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"Error exporting salary: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()