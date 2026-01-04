from flask import Blueprint, request, jsonify, g
from database import get_db, Bonus, Employee, AttendanceLog
from middleware.auth_middleware import require_auth
from middleware.company_middleware import load_company_context
from utils.helpers import success_response, error_response
from datetime import datetime, timedelta, date
from sqlalchemy import func
import pytz
import logging

bonus_bp = Blueprint('bonus', __name__)
logger = logging.getLogger(__name__)


@bonus_bp.route('/', methods=['GET'])
@require_auth
@load_company_context
def list_bonuses():
    """
    Bonuslarni ko'rish (filter bilan)

    Query params:
    - employee_id: Xodim ID
    - bonus_type: Bonus turi (perfect_attendance, early_arrival, overtime, manual)
    - start_date: Boshlanish sanasi (YYYY-MM-DD)
    - end_date: Tugash sanasi (YYYY-MM-DD)
    - page, per_page
    """
    db = get_db()

    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))

        # Base query
        query = db.query(Bonus).filter_by(company_id=g.company_id)

        # Filters
        employee_id = request.args.get('employee_id')
        if employee_id:
            query = query.filter_by(employee_id=employee_id)

        bonus_type = request.args.get('bonus_type')
        if bonus_type:
            query = query.filter_by(bonus_type=bonus_type)

        start_date = request.args.get('start_date')
        if start_date:
            query = query.filter(Bonus.date >= start_date)

        end_date = request.args.get('end_date')
        if end_date:
            query = query.filter(Bonus.date <= end_date)

        # Order by date desc
        query = query.order_by(Bonus.date.desc())

        # Pagination
        total = query.count()
        bonuses = query.offset((page - 1) * per_page).limit(per_page).all()

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
        logger.error(f"Error listing bonuses: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@bonus_bp.route('/<bonus_id>', methods=['GET'])
@require_auth
@load_company_context
def get_bonus(bonus_id):
    """Bitta bonusni ko'rish"""
    db = get_db()

    try:
        bonus = db.query(Bonus).filter_by(
            id=bonus_id,
            company_id=g.company_id
        ).first()

        if not bonus:
            return error_response("Bonus not found", 404)

        return success_response(bonus.to_dict())

    except Exception as e:
        logger.error(f"Error getting bonus: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@bonus_bp.route('/create', methods=['POST'])
@require_auth
@load_company_context
def create_manual_bonus():
    """
    Qo'lda bonus yaratish

    Body: {
        "employee_id": "...",
        "bonus_type": "manual",
        "amount": 100000,
        "date": "2025-01-15",
        "reason": "Sababi - a'lo ish natijasi"
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        # Validate required fields
        required = ['employee_id', 'amount', 'date']
        missing = [f for f in required if not data.get(f)]
        if missing:
            return error_response(f"Missing required fields: {', '.join(missing)}", 400)

        # Verify employee
        employee = db.query(Employee).filter_by(
            id=data['employee_id'],
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        # Parse date
        bonus_date = datetime.strptime(data['date'], '%Y-%m-%d').date()

        # Create bonus
        bonus = Bonus(
            company_id=g.company_id,
            employee_id=data['employee_id'],
            bonus_type=data.get('bonus_type', 'manual'),
            date=bonus_date,
            amount=float(data['amount']),
            reason=data.get('reason'),
            given_by=g.user_id
        )

        db.add(bonus)
        db.commit()
        db.refresh(bonus)

        logger.info(f"Manual bonus created: {bonus.id} for employee {employee.employee_no}, amount: {bonus.amount}")

        return success_response(bonus.to_dict(), "Bonus created successfully", 201)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating bonus: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@bonus_bp.route('/bulk-create', methods=['POST'])
@require_auth
@load_company_context
def bulk_create_bonuses():
    """
    Bir nechta xodim uchun bonus yaratish

    Body: {
        "employee_ids": ["id1", "id2", "id3"],
        "bonus_type": "manual",
        "amount": 100000,
        "date": "2025-01-15",
        "reason": "Yangi yil bonusi"
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        employee_ids = data.get('employee_ids')
        if not employee_ids or not isinstance(employee_ids, list):
            return error_response("employee_ids array is required", 400)

        if not data.get('amount') or not data.get('date'):
            return error_response("amount and date are required", 400)

        # Parse date
        bonus_date = datetime.strptime(data['date'], '%Y-%m-%d').date()

        # Verify employees
        employees = db.query(Employee).filter(
            Employee.company_id == g.company_id,
            Employee.id.in_(employee_ids)
        ).all()

        if len(employees) != len(employee_ids):
            return error_response("Some employees not found", 404)

        # Create bonuses
        created_bonuses = []
        for employee in employees:
            bonus = Bonus(
                company_id=g.company_id,
                employee_id=employee.id,
                bonus_type=data.get('bonus_type', 'manual'),
                date=bonus_date,
                amount=float(data['amount']),
                reason=data.get('reason'),
                given_by=g.user_id
            )
            db.add(bonus)
            created_bonuses.append(bonus)

        db.commit()

        for bonus in created_bonuses:
            db.refresh(bonus)

        logger.info(f"Bulk bonus created: {len(created_bonuses)} bonuses by {g.user_id}")

        return success_response({
            'created_count': len(created_bonuses),
            'bonuses': [b.to_dict() for b in created_bonuses]
        }, f"{len(created_bonuses)} bonuses created successfully", 201)

    except Exception as e:
        db.rollback()
        logger.error(f"Error bulk creating bonuses: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@bonus_bp.route('/<bonus_id>', methods=['DELETE'])
@require_auth
@load_company_context
def delete_bonus(bonus_id):
    """Bonusni o'chirish (faqat manual bonuses)"""
    db = get_db()

    try:
        bonus = db.query(Bonus).filter_by(
            id=bonus_id,
            company_id=g.company_id
        ).first()

        if not bonus:
            return error_response("Bonus not found", 404)

        # Only allow deleting manual bonuses
        if bonus.bonus_type != 'manual':
            return error_response("Only manual bonuses can be deleted", 400)

        db.delete(bonus)
        db.commit()

        logger.info(f"Bonus deleted: {bonus_id}")

        return success_response(None, "Bonus deleted successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting bonus: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@bonus_bp.route('/employee/<employee_id>/summary', methods=['GET'])
@require_auth
@load_company_context
def get_employee_bonus_summary(employee_id):
    """
    Xodimning bonus xulosasi

    Query params:
    - start_date, end_date

    Response: {
        "total_bonuses": 5,
        "total_amount": 500000,
        "by_type": {
            "perfect_attendance": {"count": 2, "amount": 200000},
            "early_arrival": {"count": 1, "amount": 100000},
            "overtime": {"count": 1, "amount": 150000},
            "manual": {"count": 1, "amount": 50000}
        }
    }
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

        # Base query
        query = db.query(Bonus).filter_by(
            company_id=g.company_id,
            employee_id=employee_id
        )

        # Date filters
        start_date = request.args.get('start_date')
        if start_date:
            query = query.filter(Bonus.date >= start_date)

        end_date = request.args.get('end_date')
        if end_date:
            query = query.filter(Bonus.date <= end_date)

        all_bonuses = query.all()

        # Calculate summary
        total_bonuses = len(all_bonuses)
        total_amount = sum(b.amount for b in all_bonuses)

        # Group by type
        by_type = {}
        for bonus_type in ['perfect_attendance', 'early_arrival', 'overtime', 'manual']:
            type_bonuses = [b for b in all_bonuses if b.bonus_type == bonus_type]
            by_type[bonus_type] = {
                'count': len(type_bonuses),
                'amount': sum(b.amount for b in type_bonuses)
            }

        return success_response({
            'employee': {
                'id': employee.id,
                'full_name': employee.full_name,
                'employee_no': employee.employee_no
            },
            'total_bonuses': total_bonuses,
            'total_amount': total_amount,
            'by_type': by_type
        })

    except Exception as e:
        logger.error(f"Error getting bonus summary: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@bonus_bp.route('/auto-calculate/perfect-attendance', methods=['POST'])
@require_auth
@load_company_context
def auto_calculate_perfect_attendance():
    """
    Mukammal davomat uchun avtomatik bonus hisoblash

    Body: {
        "month": 1,  // 1-12
        "year": 2025,
        "bonus_amount": 200000,  // Har bir xodim uchun
        "employee_ids": ["id1", "id2"]  // Optional - agar bo'lmasa barcha xodimlar
    }

    Shartlar:
    - Bir marta ham kech qolmagan
    - Bir marta ham erta ketmagan
    - Hech qanday sababsiz yo'qlik bo'lmagan
    """
    db = get_db()

    try:
        data = request.get_json()

        month = data.get('month')
        year = data.get('year')
        bonus_amount = data.get('bonus_amount')

        if not month or not year or not bonus_amount:
            return error_response("month, year, and bonus_amount are required", 400)

        if month < 1 or month > 12:
            return error_response("month must be 1-12", 400)

        # Calculate date range for the month
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year, 12, 31)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)

        # Get employees to check
        employee_ids = data.get('employee_ids')
        if employee_ids:
            employees = db.query(Employee).filter(
                Employee.company_id == g.company_id,
                Employee.id.in_(employee_ids),
                Employee.status == 'active'
            ).all()
        else:
            employees = db.query(Employee).filter_by(
                company_id=g.company_id,
                status='active'
            ).all()

        perfect_employees = []

        for employee in employees:
            # Check attendance logs for this month
            logs = db.query(AttendanceLog).filter(
                AttendanceLog.employee_id == employee.id,
                AttendanceLog.date >= start_date,
                AttendanceLog.date <= end_date
            ).all()

            if not logs:
                # No attendance records
                continue

            # Check for perfect attendance
            is_perfect = True
            for log in logs:
                if log.late_minutes > 0 or log.early_leave_minutes > 0:
                    is_perfect = False
                    break

            # Check for absences (days without attendance)
            # Get total working days in month
            from database import EmployeeSchedule
            schedules = db.query(EmployeeSchedule).filter_by(
                employee_id=employee.id
            ).all()

            # Count expected working days
            expected_days = 0
            current = start_date
            while current <= end_date:
                day_of_week = current.isoweekday()

                # Check if this day is a working day
                if schedules:
                    schedule = next((s for s in schedules if s.day_of_week == day_of_week), None)
                    if schedule and not schedule.is_day_off:
                        expected_days += 1
                    elif not schedule:
                        # No schedule for this day, use default (Mon-Fri)
                        if day_of_week <= 5:
                            expected_days += 1
                else:
                    # No schedule, assume Mon-Fri
                    if day_of_week <= 5:
                        expected_days += 1

                current += timedelta(days=1)

            # Compare with actual attendance
            if len(logs) < expected_days:
                is_perfect = False

            if is_perfect:
                perfect_employees.append(employee)

        # Create bonuses for perfect employees
        created_bonuses = []
        for employee in perfect_employees:
            # Check if bonus already exists
            existing = db.query(Bonus).filter_by(
                company_id=g.company_id,
                employee_id=employee.id,
                bonus_type='perfect_attendance',
                date=end_date
            ).first()

            if existing:
                continue

            bonus = Bonus(
                company_id=g.company_id,
                employee_id=employee.id,
                bonus_type='perfect_attendance',
                date=end_date,
                amount=float(bonus_amount),
                reason=f"Mukammal davomat - {year}-{month:02d}",
                given_by=g.user_id
            )
            db.add(bonus)
            created_bonuses.append(bonus)

        db.commit()

        for bonus in created_bonuses:
            db.refresh(bonus)

        logger.info(f"Perfect attendance bonuses: {len(created_bonuses)} created for {year}-{month}")

        return success_response({
            'total_employees_checked': len(employees),
            'perfect_employees': len(perfect_employees),
            'bonuses_created': len(created_bonuses),
            'bonuses': [b.to_dict() for b in created_bonuses]
        }, f"{len(created_bonuses)} perfect attendance bonuses created")

    except Exception as e:
        db.rollback()
        logger.error(f"Error calculating perfect attendance: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@bonus_bp.route('/auto-calculate/early-arrival', methods=['POST'])
@require_auth
@load_company_context
def auto_calculate_early_arrival():
    """
    Erta kelganlik uchun avtomatik bonus

    Body: {
        "start_date": "2025-01-01",
        "end_date": "2025-01-31",
        "early_minutes_threshold": 15,  // Kamida 15 daqiqa erta
        "min_early_days": 10,  // Kamida 10 kun erta kelgan bo'lishi kerak
        "bonus_amount": 100000
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')
        early_threshold = data.get('early_minutes_threshold', 15)
        min_days = data.get('min_early_days', 10)
        bonus_amount = data.get('bonus_amount')

        if not start_date_str or not end_date_str or not bonus_amount:
            return error_response("start_date, end_date, and bonus_amount are required", 400)

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        # Get all active employees
        employees = db.query(Employee).filter_by(
            company_id=g.company_id,
            status='active'
        ).all()

        eligible_employees = []

        for employee in employees:
            # Get attendance logs
            logs = db.query(AttendanceLog).filter(
                AttendanceLog.employee_id == employee.id,
                AttendanceLog.date >= start_date,
                AttendanceLog.date <= end_date,
                AttendanceLog.check_in_time.isnot(None)
            ).all()

            # Count early arrivals
            early_count = 0
            for log in logs:
                # Check if arrived early
                from services.attendance_service import get_employee_work_time_for_date
                work_start, _, _ = get_employee_work_time_for_date(employee, log.date)

                if work_start:
                    tashkent_tz = pytz.timezone('Asia/Tashkent')
                    scheduled_start = tashkent_tz.localize(
                        datetime.combine(log.date, work_start)
                    )

                    # Calculate minutes early
                    time_diff = (scheduled_start - log.check_in_time).total_seconds() / 60

                    if time_diff >= early_threshold:
                        early_count += 1

            if early_count >= min_days:
                eligible_employees.append((employee, early_count))

        # Create bonuses
        created_bonuses = []
        for employee, early_days in eligible_employees:
            bonus = Bonus(
                company_id=g.company_id,
                employee_id=employee.id,
                bonus_type='early_arrival',
                date=end_date,
                amount=float(bonus_amount),
                reason=f"Erta kelganlik - {early_days} kun",
                given_by=g.user_id
            )
            db.add(bonus)
            created_bonuses.append(bonus)

        db.commit()

        for bonus in created_bonuses:
            db.refresh(bonus)

        logger.info(f"Early arrival bonuses: {len(created_bonuses)} created")

        return success_response({
            'bonuses_created': len(created_bonuses),
            'bonuses': [b.to_dict() for b in created_bonuses]
        }, f"{len(created_bonuses)} early arrival bonuses created")

    except Exception as e:
        db.rollback()
        logger.error(f"Error calculating early arrival: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@bonus_bp.route('/leaderboard', methods=['GET'])
@require_auth
@load_company_context
def get_bonus_leaderboard():
    """
    Eng ko'p bonus olgan xodimlar reytingi

    Query params:
    - start_date, end_date
    - limit (default: 10)
    - branch_id (optional)
    """
    db = get_db()

    try:
        limit = int(request.args.get('limit', 10))

        # Base query
        query = db.query(
            Employee.id,
            Employee.employee_no,
            Employee.full_name,
            Employee.branch_id,
            func.count(Bonus.id).label('bonus_count'),
            func.sum(Bonus.amount).label('total_amount')
        ).join(
            Bonus, Employee.id == Bonus.employee_id
        ).filter(
            Employee.company_id == g.company_id
        )

        # Date filters
        start_date = request.args.get('start_date')
        if start_date:
            query = query.filter(Bonus.date >= start_date)

        end_date = request.args.get('end_date')
        if end_date:
            query = query.filter(Bonus.date <= end_date)

        # Branch filter
        branch_id = request.args.get('branch_id')
        if branch_id:
            query = query.filter(Employee.branch_id == branch_id)

        # Group and order
        query = query.group_by(
            Employee.id,
            Employee.employee_no,
            Employee.full_name,
            Employee.branch_id
        ).order_by(
            func.sum(Bonus.amount).desc()
        ).limit(limit)

        results = query.all()

        leaderboard = []
        for idx, result in enumerate(results, 1):
            leaderboard.append({
                'rank': idx,
                'employee_id': result.id,
                'employee_no': result.employee_no,
                'full_name': result.full_name,
                'branch_id': result.branch_id,
                'bonus_count': result.bonus_count,
                'total_amount': float(result.total_amount) if result.total_amount else 0
            })

        return success_response({
            'leaderboard': leaderboard
        })

    except Exception as e:
        logger.error(f"Error getting leaderboard: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()