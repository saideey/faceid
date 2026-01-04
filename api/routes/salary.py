from flask import Blueprint, request, jsonify, g
from database import get_db, Employee, Penalty, Bonus, AttendanceLog, CompanySettings, EmployeeSchedule
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


def get_employee_expected_days(employee, start_date, end_date, for_daily_rate=False):
    """
    Xodimning schedule asosida expected work days hisoblash

    MUHIM: Kunlik stavka uchun DOIM to'liq oy ishlatiladi!

    Args:
        employee: Employee object
        start_date: Boshlanish sanasi
        end_date: Tugash sanasi
        for_daily_rate: Agar True bo'lsa, to'liq oy asosida hisoblaydi

    Returns: (expected_days, schedule_dict)
    schedule_dict = {
        1: {'start': '09:00', 'end': '18:00', 'is_off': False},  # Monday
        2: {'start': '12:00', 'end': '15:00', 'is_off': False},  # Tuesday
        ...
        5: {'start': None, 'end': None, 'is_off': True},  # Friday (dam olish)
    }
    """
    db = get_db()

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

    db.close()
    return expected_days, schedule_dict


def calculate_employee_salary(employee, start_date, end_date, company_settings):
    """
    PROFESSIONAL xodim oyligini hisoblash

    QOIDA:
    1. Kunlik stavka DOIM to'liq oy asosida hisoblanadi
    2. Ishlangan kunlar faqat sana oralig'ida hisoblanadi
    3. Har bir jarima/bonus batafsil ko'rsatiladi

    Returns: {
        'base_salary': float,
        'salary_type': 'monthly' or 'daily',
        'calculation_method': 'full_month_based',
        'full_month_work_days': int,  # To'liq oyda nechta ish kuni
        'daily_rate': float,  # Kunlik stavka (to'liq oy asosida)
        'period_work_days': int,  # Sana oralig'ida nechta ish kuni
        'worked_days': int,
        'absence_days': int,
        'late_details': [...],  # Har bir kech qolish sanasi bilan
        'calculated_salary': float,
        'final_salary': float,
        'detailed_breakdown': {...}
    }
    """
    db = get_db()

    try:
        # Base salary
        base_salary = employee.salary or 0
        salary_type = employee.salary_type or 'monthly'

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
                # Check if this day is an off day for the employee
                day_of_week = log.date.isoweekday()  # 1=Mon, 7=Sun
                is_off_day = schedule_dict.get(day_of_week, False)

                # Check if before hire date
                is_before_hire = employee.hire_date and log.date < employee.hire_date

                # Only count late minutes if:
                # 1. Not an off day
                # 2. Not before hire date
                if not is_off_day and not is_before_hire:
                    late_details.append({
                        'date': log.date.isoformat(),
                        'late_minutes': log.late_minutes,
                        'check_in_time': str(log.check_in_time) if log.check_in_time else None
                    })
                    total_late_minutes += log.late_minutes
                else:
                    logger.info(f"⚪ {log.date}: Late ignored - {'Off day' if is_off_day else 'Before hire date'}")

        # Calculate automatic late penalty
        auto_late_penalty = 0
        late_penalty_per_minute = 0
        if company_settings and getattr(company_settings, 'auto_penalty_enabled', False):
            late_penalty_per_minute = getattr(company_settings, 'late_penalty_per_minute', 0)
            if late_penalty_per_minute > 0 and total_late_minutes > 0:
                auto_late_penalty = total_late_minutes * late_penalty_per_minute
                logger.info(
                    f"🔴 Late penalty: {total_late_minutes} min × {late_penalty_per_minute:,.0f} = {auto_late_penalty:,.0f}")

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
            # QOIDA: Kunlik stavka DOIM to'liq oy asosida hisoblanadi!
            full_month_work_days, schedule_dict = get_employee_expected_days(
                employee, start_date, end_date, for_daily_rate=True
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
                employee, effective_start_date, actual_end_date, for_daily_rate=False
            )

            logger.info(f"📅 DAVR: {effective_start_date} to {actual_end_date}")
            logger.info(f"📅 Davr ish kunlari: {period_expected_days}")
            logger.info(f"✅ Ishlangan: {worked_days} kun")

            # STEP 3: Calculate salary for worked days
            calculated_salary = daily_rate * worked_days

            # STEP 4: Calculate absence penalty (only for days after hire_date)
            absence_days = max(0, period_expected_days - worked_days)
            absence_penalty = 0
            if company_settings and absence_days > 0:
                absence_penalty_per_day = getattr(company_settings, 'absence_penalty_amount', 0)
                if absence_penalty_per_day > 0:
                    absence_penalty = absence_days * absence_penalty_per_day
                    logger.info(
                        f"⚠️ Kelmaslik: {absence_days} kun × {absence_penalty_per_day:,.0f} = {absence_penalty:,.0f}")

            expected_days = period_expected_days

        else:
            # Daily salary
            calculated_salary = base_salary * worked_days
            daily_rate = base_salary
            absence_penalty = 0
            absence_days = 0
            expected_days = 0
            full_month_work_days = 0
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
                    'absence_days': absence_days
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
                        'details': late_details
                    },
                    'absence_penalty': {
                        'days': absence_days,
                        'rate_per_day': getattr(company_settings, 'absence_penalty_amount',
                                                0) if company_settings else 0,
                        'amount': round(absence_penalty, 2)
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
                }
            }
        }

    finally:
        db.close()
    """
    Xodimning oyligini hisoblash

    Returns: {
        'base_salary': float,
        'salary_type': 'monthly' or 'daily',
        'worked_days': int,
        'total_work_hours': float,
        'late_minutes': int,
        'penalty_amount': float,
        'bonus_amount': float,
        'final_salary': float,
        'deduction_details': {...},
        'breakdown': {...}
    }
    """
    db = get_db()

    try:
        # Base salary
        base_salary = employee.salary or 0
        salary_type = employee.salary_type or 'monthly'

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

        # Calculate total late minutes
        total_late_minutes = sum(log.late_minutes or 0 for log in attendance_logs)

        # Calculate automatic late penalty based on company settings
        auto_late_penalty = 0
        if company_settings and company_settings.auto_penalty_enabled:
            late_penalty_per_minute = company_settings.late_penalty_per_minute or 0
            if late_penalty_per_minute > 0 and total_late_minutes > 0:
                auto_late_penalty = total_late_minutes * late_penalty_per_minute
                logger.info(
                    f"🔴 Auto late penalty: {total_late_minutes} min × {late_penalty_per_minute:,.0f} = {auto_late_penalty:,.0f} UZS")

        # Get manual penalties from database (faqat active penalties - waived va excused emas)
        penalties = db.query(Penalty).filter(
            Penalty.employee_id == employee.id,
            Penalty.date >= start_date,
            Penalty.date <= end_date,
            Penalty.is_waived == False,
            Penalty.is_excused == False
        ).all()

        # Total penalty = manual penalties + auto late penalty
        manual_penalty_amount = sum(p.amount for p in penalties)
        total_penalty_amount = manual_penalty_amount + auto_late_penalty

        # Get bonuses
        bonuses = db.query(Bonus).filter(
            Bonus.employee_id == employee.id,
            Bonus.date >= start_date,
            Bonus.date <= end_date
        ).all()

        total_bonus_amount = sum(b.amount for b in bonuses)

        # Calculate salary based on type
        if salary_type == 'monthly':
            # Monthly salary - use employee's individual schedule
            expected_days, schedule_dict = get_employee_expected_days(employee, start_date, end_date)

            logger.info(f"📅 {employee.full_name}: expected={expected_days}, worked={worked_days}")

            # Calculate absence penalty for days not worked (only for past days)
            absence_days = max(0, expected_days - worked_days)
            absence_penalty = 0
            if company_settings and absence_days > 0:
                absence_penalty_per_day = getattr(company_settings, 'absence_penalty_amount', 0)
                if absence_penalty_per_day > 0:
                    absence_penalty = absence_days * absence_penalty_per_day
                    logger.info(
                        f"⚠️ Absence penalty: {absence_days} days × {absence_penalty_per_day:,.0f} = {absence_penalty:,.0f} UZS")

            # Calculate based on worked days
            if expected_days > 0:
                daily_rate = base_salary / expected_days
                calculated_salary = daily_rate * worked_days
                logger.info(
                    f"💰 Salary: {base_salary:,.0f} / {expected_days} days × {worked_days} = {calculated_salary:,.0f}")
            else:
                calculated_salary = base_salary

        else:
            # Daily salary
            calculated_salary = base_salary * worked_days
            absence_penalty = 0
            expected_days = 0
            absence_days = 0

        # Total penalty = manual + auto late + absence
        total_penalty_amount = manual_penalty_amount + auto_late_penalty + absence_penalty

        # Final salary = calculated - penalties + bonuses
        final_salary = calculated_salary - total_penalty_amount + total_bonus_amount

        # Make sure final salary is not negative
        if final_salary < 0:
            final_salary = 0

        return {
            'base_salary': base_salary,
            'salary_type': salary_type,
            'worked_days': worked_days,
            'expected_days': expected_days if salary_type == 'monthly' else None,
            'absence_days': absence_days if salary_type == 'monthly' else 0,
            'total_work_hours': total_work_hours,
            'late_minutes': total_late_minutes,
            'penalty_count': len(penalties),
            'penalty_amount': total_penalty_amount,
            'auto_late_penalty': auto_late_penalty,
            'absence_penalty': absence_penalty if salary_type == 'monthly' else 0,
            'manual_penalty': manual_penalty_amount,
            'bonus_count': len(bonuses),
            'bonus_amount': total_bonus_amount,
            'calculated_salary': calculated_salary,
            'final_salary': final_salary,
            'deduction_details': {
                'manual_penalties': manual_penalty_amount,
                'auto_late_penalty': auto_late_penalty,
                'absence_penalty': absence_penalty if salary_type == 'monthly' else 0,
                'total_penalties': total_penalty_amount,
                'total_bonuses': total_bonus_amount,
                'net_deduction': total_penalty_amount - total_bonus_amount
            },
            'breakdown': {
                'base': base_salary,
                'calculated': calculated_salary,
                'penalties': -total_penalty_amount,
                'bonuses': total_bonus_amount,
                'final': final_salary
            }
        }

    finally:
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
            "late_minutes": 600,  // 10 soat
            "penalty_amount": 200000,
            "bonus_amount": 50000,
            "final_salary": 2850000
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

        # Calculate salary
        salary_result = calculate_employee_salary(employee, start_date, end_date, company_settings)

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

        for employee in employees:
            salary_result = calculate_employee_salary(employee, start_date, end_date, company_settings)

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

        return success_response({
            'period': {
                'start_date': start_date_str,
                'end_date': end_date_str
            },
            'employees': results,
            'summary': {
                'total_employees': len(results),
                'total_salaries': total_salaries,
                'total_penalties': total_penalties,
                'total_bonuses': total_bonuses,
                'net_payroll': total_salaries
            }
        })

    except Exception as e:
        logger.error(f"Error bulk calculating salary: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/monthly-report', methods=['POST'])
@require_auth
@load_company_context
def monthly_salary_report():
    """
    Oylik maosh hisoboti - barcha xodimlar uchun

    Body: {
        "month": 1,  // 1-12
        "year": 2025,
        "branch_id": "..."  // Optional
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        month = data.get('month')
        year = data.get('year')

        if not month or not year:
            return error_response("month and year are required", 400)

        if month < 1 or month > 12:
            return error_response("month must be 1-12", 400)

        # Calculate date range for the month
        start_date = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        end_date = date(year, month, last_day)

        # Get company settings
        company_settings = db.query(CompanySettings).filter_by(
            company_id=g.company_id
        ).first()

        # Get employees with eager loading for branch and department
        query = db.query(Employee).options(
            joinedload(Employee.branch),
            joinedload(Employee.department)
        ).filter_by(
            company_id=g.company_id,
            status='active'
        )

        branch_id = data.get('branch_id')
        if branch_id:
            query = query.filter_by(branch_id=branch_id)

        employees = query.order_by(Employee.employee_no).all()

        # Calculate salary for each employee
        results = []
        total_salaries = 0
        total_penalties = 0
        total_bonuses = 0

        for employee in employees:
            salary_result = calculate_employee_salary(employee, start_date, end_date, company_settings)

            results.append({
                'employee_id': employee.id,
                'employee_no': employee.employee_no,
                'full_name': employee.full_name,
                'position': employee.position,
                'branch_name': employee.branch.name if employee.branch else None,
                'department_name': employee.department.name if employee.department else None,
                'base_salary': salary_result['base_salary'],
                'salary_type': salary_result['salary_type'],
                'worked_days': salary_result['worked_days'],
                'expected_days': salary_result.get('expected_days', 0),
                'absence_days': salary_result.get('absence_days', 0),
                'daily_rate': salary_result.get('daily_rate', 0),
                'late_minutes': salary_result['late_minutes'],
                'late_details': salary_result.get('late_details', []),
                'late_penalty_per_minute': salary_result.get('late_penalty_per_minute', 0),
                'auto_late_penalty': salary_result.get('auto_late_penalty', 0),
                'absence_penalty': salary_result.get('absence_penalty', 0),
                'manual_penalty': salary_result.get('manual_penalty', 0),
                'penalty_amount': salary_result['penalty_amount'],
                'bonus_amount': salary_result['bonus_amount'],
                'calculated_salary': salary_result.get('calculated_salary', 0),
                'final_salary': salary_result['final_salary'],
                'detailed_breakdown': salary_result.get('detailed_breakdown', {})
            })

            total_salaries += salary_result['final_salary']
            total_penalties += salary_result['penalty_amount']
            total_bonuses += salary_result['bonus_amount']

        return success_response({
            'period': {
                'month': month,
                'year': year,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat()
            },
            'employees': results,
            'summary': {
                'total_employees': len(results),
                'total_salaries': total_salaries,
                'total_penalties': total_penalties,
                'total_bonuses': total_bonuses,
                'net_payroll': total_salaries
            }
        })

    except Exception as e:
        logger.error(f"Error generating monthly report: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/custom-report', methods=['POST'])
@require_auth
@load_company_context
def custom_date_range_report():
    """
    Custom date range salary report - 4-yanvardan 10-yanvargacha kabi

    Body: {
        "start_date": "2026-01-04",
        "end_date": "2026-01-10",
        "branch_id": "..." // Optional
    }

    OR for monthly (backward compatibility):
    {
        "month": 1,
        "year": 2026,
        "branch_id": "..." // Optional
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        # Check if monthly mode or custom date range
        if 'month' in data and 'year' in data:
            # Monthly mode - convert to date range
            month = data.get('month')
            year = data.get('year')

            if month < 1 or month > 12:
                return error_response("month must be 1-12", 400)

            start_date = date(year, month, 1)
            last_day = calendar.monthrange(year, month)[1]
            end_date = date(year, month, last_day)

        elif 'start_date' in data and 'end_date' in data:
            # Custom date range mode
            from utils.helpers import parse_date
            start_date = parse_date(data['start_date'])
            end_date = parse_date(data['end_date'])

            if not start_date or not end_date:
                return error_response("Invalid date format. Use YYYY-MM-DD", 400)

            if start_date > end_date:
                return error_response("start_date must be before or equal to end_date", 400)
        else:
            return error_response("Provide either (month, year) or (start_date, end_date)", 400)

        # Get company settings
        company_settings = db.query(CompanySettings).filter_by(
            company_id=g.company_id
        ).first()

        # Get employees with eager loading for branch and department
        query = db.query(Employee).options(
            joinedload(Employee.branch),
            joinedload(Employee.department)
        ).filter_by(
            company_id=g.company_id,
            status='active'
        )

        branch_id = data.get('branch_id')
        if branch_id:
            query = query.filter_by(branch_id=branch_id)

        employees = query.order_by(Employee.employee_no).all()

        # Calculate salary for each employee
        results = []
        total_salaries = 0
        total_penalties = 0
        total_bonuses = 0

        for employee in employees:
            salary_result = calculate_employee_salary(employee, start_date, end_date, company_settings)

            results.append({
                'employee_id': employee.id,
                'employee_no': employee.employee_no,
                'full_name': employee.full_name,
                'position': employee.position,
                'branch_name': employee.branch.name if employee.branch else None,
                'department_name': employee.department.name if employee.department else None,
                'base_salary': salary_result['base_salary'],
                'salary_type': salary_result['salary_type'],
                'worked_days': salary_result['worked_days'],
                'expected_days': salary_result.get('expected_days'),
                'absence_days': salary_result.get('absence_days', 0),
                'late_minutes': salary_result['late_minutes'],
                'penalty_amount': salary_result['penalty_amount'],
                'auto_late_penalty': salary_result.get('auto_late_penalty', 0),
                'absence_penalty': salary_result.get('absence_penalty', 0),
                'bonus_amount': salary_result['bonus_amount'],
                'calculated_salary': salary_result['calculated_salary'],
                'final_salary': salary_result['final_salary']
            })

            total_salaries += salary_result['final_salary']
            total_penalties += salary_result['penalty_amount']
            total_bonuses += salary_result['bonus_amount']

        return success_response({
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'total_days': (end_date - start_date).days + 1
            },
            'employees': results,
            'summary': {
                'total_employees': len(results),
                'total_salaries': total_salaries,
                'total_penalties': total_penalties,
                'total_bonuses': total_bonuses,
                'net_payroll': total_salaries
            }
        })

    except Exception as e:
        logger.error(f"Error generating custom report: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/late-ranking', methods=['GET'])
@require_auth
@load_company_context
def get_late_ranking():
    """
    Eng ko'p kech qolganlar reytingi

    Query params:
    - start_date, end_date
    - limit (default: 20)
    - branch_id (optional)
    - order: 'most' (eng ko'p) yoki 'least' (eng kam) - default: 'most'

    Response: [
        {
            "rank": 1,
            "employee_no": "001",
            "full_name": "Ali Valiyev",
            "total_late_minutes": 600,  // 10 soat
            "total_late_hours": 10.0,
            "late_days": 15,
            "avg_late_minutes": 40,
            "penalty_amount": 200000
        }
    ]
    """
    db = get_db()

    try:
        limit = int(request.args.get('limit', 20))
        order = request.args.get('order', 'most')  # 'most' or 'least'

        # Build query
        query = db.query(
            Employee.id,
            Employee.employee_no,
            Employee.full_name,
            Employee.branch_id,
            Employee.position,
            func.sum(AttendanceLog.late_minutes).label('total_late_minutes'),
            func.count(AttendanceLog.id).filter(AttendanceLog.late_minutes > 0).label('late_days')
        ).join(
            AttendanceLog, Employee.id == AttendanceLog.employee_id
        ).filter(
            Employee.company_id == g.company_id,
            Employee.status == 'active'
        )

        # Date filters
        start_date = request.args.get('start_date')
        if start_date:
            query = query.filter(AttendanceLog.date >= start_date)

        end_date = request.args.get('end_date')
        if end_date:
            query = query.filter(AttendanceLog.date <= end_date)

        # Branch filter
        branch_id = request.args.get('branch_id')
        if branch_id:
            query = query.filter(Employee.branch_id == branch_id)

        # Group by employee
        query = query.group_by(
            Employee.id,
            Employee.employee_no,
            Employee.full_name,
            Employee.branch_id,
            Employee.position
        )

        # Order
        if order == 'least':
            query = query.order_by(func.sum(AttendanceLog.late_minutes).asc())
        else:
            query = query.order_by(func.sum(AttendanceLog.late_minutes).desc())

        query = query.limit(limit)

        results = query.all()

        # Get penalties for these employees
        ranking = []
        for idx, result in enumerate(results, 1):
            # Get penalties
            penalty_query = db.query(func.sum(Penalty.amount)).filter(
                Penalty.employee_id == result.id,
                Penalty.is_waived == False,
                Penalty.is_excused == False
            )

            if start_date:
                penalty_query = penalty_query.filter(Penalty.date >= start_date)
            if end_date:
                penalty_query = penalty_query.filter(Penalty.date <= end_date)

            penalty_amount = penalty_query.scalar() or 0

            total_late_minutes = result.total_late_minutes or 0

            ranking.append({
                'rank': idx,
                'employee_id': result.id,
                'employee_no': result.employee_no,
                'full_name': result.full_name,
                'position': result.position,
                'branch_id': result.branch_id,
                'total_late_minutes': int(total_late_minutes),
                'total_late_hours': round(total_late_minutes / 60, 2),
                'late_days': result.late_days,
                'avg_late_minutes': round(total_late_minutes / result.late_days, 2) if result.late_days > 0 else 0,
                'penalty_amount': float(penalty_amount)
            })

        return success_response({
            'ranking': ranking,
            'order': order,
            'total_count': len(ranking)
        })

    except Exception as e:
        logger.error(f"Error getting late ranking: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@salary_bp.route('/attendance-ranking', methods=['GET'])
@require_auth
@load_company_context
def get_attendance_ranking():
    """
    Davomat bo'yicha eng yaxshi xodimlar reytingi

    Query params:
    - start_date, end_date
    - limit (default: 20)
    - branch_id (optional)

    Response: [
        {
            "rank": 1,
            "employee_no": "001",
            "full_name": "Ali Valiyev",
            "total_days": 22,
            "on_time_days": 20,
            "late_days": 2,
            "total_late_minutes": 30,
            "attendance_rate": 95.45,
            "bonus_amount": 200000
        }
    ]
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

        by_branch = {}
        by_department = {}

        for employee in employees:
            salary_result = calculate_employee_salary(employee, start_date, end_date, company_settings)

            total_payroll += salary_result['final_salary']
            total_penalties += salary_result['penalty_amount']
            total_bonuses += salary_result['bonus_amount']

            # Group by branch
            branch_key = employee.branch.name if employee.branch else 'No Branch'
            if branch_key not in by_branch:
                by_branch[branch_key] = {
                    'employee_count': 0,
                    'total_salary': 0,
                    'total_penalties': 0,
                    'total_bonuses': 0
                }
            by_branch[branch_key]['employee_count'] += 1
            by_branch[branch_key]['total_salary'] += salary_result['final_salary']
            by_branch[branch_key]['total_penalties'] += salary_result['penalty_amount']
            by_branch[branch_key]['total_bonuses'] += salary_result['bonus_amount']

            # Group by department
            dept_key = employee.department.name if employee.department else 'No Department'
            if dept_key not in by_department:
                by_department[dept_key] = {
                    'employee_count': 0,
                    'total_salary': 0,
                    'total_penalties': 0,
                    'total_bonuses': 0
                }
            by_department[dept_key]['employee_count'] += 1
            by_department[dept_key]['total_salary'] += salary_result['final_salary']
            by_department[dept_key]['total_penalties'] += salary_result['penalty_amount']
            by_department[dept_key]['total_bonuses'] += salary_result['bonus_amount']

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
                'total_payroll': total_payroll
            },
            'by_branch': by_branch_list,
            'by_department': by_department_list
        })

    except Exception as e:
        logger.error(f"Error getting payroll summary: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()