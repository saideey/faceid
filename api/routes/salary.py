from flask import Blueprint, request, jsonify, g
from database import get_db, Employee, Penalty, Bonus, AttendanceLog, CompanySettings, EmployeeSchedule, EmployeeLeave
from middleware.auth_middleware import require_auth
from middleware.company_middleware import load_company_context
from utils.helpers import success_response, error_response
from datetime import datetime, timedelta, date
from sqlalchemy import func, and_
from sqlalchemy.orm import joinedload
import calendar
import logging

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

        # Get employee schedule to check off days
        employee_schedules = db.query(EmployeeSchedule).filter_by(employee_id=employee.id).all()
        schedule_dict = {}
        for sched in employee_schedules:
            schedule_dict[sched.day_of_week] = sched.is_day_off

        # Default schedule if not set (Mon-Fri work, Sat-Sun off)
        if not schedule_dict:
            schedule_dict = {1: False, 2: False, 3: False, 4: False, 5: False, 6: True, 7: True}

        for log in attendance_logs:
            if log.late_minutes and log.late_minutes > 0:
                date_str = log.date.isoformat()

                # Check if this day is an off day for the employee
                day_of_week = log.date.isoweekday()  # 1=Mon, 7=Sun
                is_off_day = schedule_dict.get(day_of_week, False)

                # Check if before hire date
                is_before_hire = employee.hire_date and log.date < employee.hire_date

                # ==========================================
                # YANGI: Dam olish yoki kasal kuni tekshirish
                # ==========================================
                is_leave_day = date_str in leave_dates
                leave_type = leave_dates.get(date_str, None)

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
                        'penalty_saved': 0  # Will be calculated below
                    })
                    logger.info(f"⚪ {log.date}: Late ignored - Off day")
                elif is_before_hire:
                    excused_days.append({
                        'date': date_str,
                        'reason': 'before_hire',
                        'reason_text': 'Ishga kirish sanasidan oldin',
                        'late_minutes': log.late_minutes,
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

        # Calculate automatic late penalty
        auto_late_penalty = 0
        late_penalty_per_minute = 0
        if company_settings and getattr(company_settings, 'auto_penalty_enabled', False):
            late_penalty_per_minute = getattr(company_settings, 'late_penalty_per_minute', 0)
            if late_penalty_per_minute > 0 and total_late_minutes > 0:
                auto_late_penalty = total_late_minutes * late_penalty_per_minute
                logger.info(
                    f"🔴 Late penalty: {total_late_minutes} min × {late_penalty_per_minute:,.0f} = {auto_late_penalty:,.0f}")

        # ==========================================
        # YANGI: Excused days uchun penalty_saved hisoblash
        # ==========================================
        if late_penalty_per_minute > 0:
            for excused in excused_days:
                excused['penalty_saved'] = excused['late_minutes'] * late_penalty_per_minute

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

        # Total penalty = manual + auto late + absence
        total_penalty_amount = manual_penalty_amount + auto_late_penalty + absence_penalty

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
            'late_penalty_per_minute': late_penalty_per_minute,
            'auto_late_penalty': round(auto_late_penalty, 2),
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
            on_time_days = sum(1 for log in logs if log.late_minutes == 0)
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

        # Sort by attendance rate (highest first)
        ranking_data.sort(key=lambda x: x['attendance_rate'], reverse=True)

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