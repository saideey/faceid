from decimal import Decimal
from database import get_db, Penalty, Employee, CompanySettings, AttendanceLog
from datetime import date


def calculate_penalty_amount(late_minutes, penalty_per_minute):
    """Calculate penalty amount based on late minutes"""
    if late_minutes <= 0:
        return Decimal('0.00')

    amount = Decimal(str(late_minutes)) * Decimal(str(penalty_per_minute))
    return amount.quantize(Decimal('0.01'))


def create_penalty_for_lateness(employee, attendance_log, late_minutes, settings):
    """Create penalty record for employee lateness"""
    db = get_db()
    try:
        if late_minutes <= 0:
            return None, "No penalty needed"

        # Calculate penalty amount
        amount = calculate_penalty_amount(late_minutes, settings.penalty_per_minute)

        # Create penalty record
        penalty = Penalty(
            company_id=employee.company_id,
            employee_id=employee.id,
            attendance_log_id=attendance_log.id,
            penalty_type='late',
            amount=amount,
            late_minutes=late_minutes,
            reason=f"Late by {late_minutes} minutes",
            date=attendance_log.date
        )

        db.add(penalty)
        db.commit()
        db.refresh(penalty)

        return penalty, None
    except Exception as e:
        db.rollback()
        return None, str(e)
    finally:
        db.close()


def create_penalty_for_early_leave(employee, attendance_log, early_leave_minutes, settings):
    """Create penalty record for early leave"""
    db = get_db()
    try:
        if early_leave_minutes <= 0:
            return None, "No penalty needed"

        # Calculate penalty amount (can use same rate or different rate)
        amount = calculate_penalty_amount(early_leave_minutes, settings.penalty_per_minute)

        # Create penalty record
        penalty = Penalty(
            company_id=employee.company_id,
            employee_id=employee.id,
            attendance_log_id=attendance_log.id,
            penalty_type='early_leave',
            amount=amount,
            late_minutes=early_leave_minutes,  # Storing minutes in this field
            reason=f"Left early by {early_leave_minutes} minutes",
            date=attendance_log.date
        )

        db.add(penalty)
        db.commit()
        db.refresh(penalty)

        return penalty, None
    except Exception as e:
        db.rollback()
        return None, str(e)
    finally:
        db.close()


def create_penalty_for_absence(employee, penalty_date, settings, reason="Absent without notice"):
    """Create penalty record for employee absence"""
    db = get_db()
    try:
        # Define absence penalty amount (could be configurable in settings)
        absence_penalty_amount = Decimal('50000.00')  # Example fixed amount

        # Create penalty record
        penalty = Penalty(
            company_id=employee.company_id,
            employee_id=employee.id,
            attendance_log_id=None,
            penalty_type='absence',
            amount=absence_penalty_amount,
            late_minutes=0,
            reason=reason,
            date=penalty_date
        )

        db.add(penalty)
        db.commit()
        db.refresh(penalty)

        return penalty, None
    except Exception as e:
        db.rollback()
        return None, str(e)
    finally:
        db.close()


def create_manual_penalty(employee_id, amount, reason, penalty_date):
    """Create manual penalty record"""
    db = get_db()
    try:
        employee = db.query(Employee).filter_by(id=employee_id).first()
        if not employee:
            return None, "Employee not found"

        # Create penalty record
        penalty = Penalty(
            company_id=employee.company_id,
            employee_id=employee.id,
            attendance_log_id=None,
            penalty_type='manual',
            amount=Decimal(str(amount)),
            late_minutes=0,
            reason=reason,
            date=penalty_date
        )

        db.add(penalty)
        db.commit()
        db.refresh(penalty)

        return penalty, None
    except Exception as e:
        db.rollback()
        return None, str(e)
    finally:
        db.close()


def get_employee_penalties(employee_id, start_date=None, end_date=None):
    """Get all penalties for an employee within date range"""
    db = get_db()
    try:
        query = db.query(Penalty).filter_by(employee_id=employee_id)

        if start_date:
            query = query.filter(Penalty.date >= start_date)

        if end_date:
            query = query.filter(Penalty.date <= end_date)

        penalties = query.order_by(Penalty.date.desc()).all()
        return penalties, None
    except Exception as e:
        return None, str(e)
    finally:
        db.close()


def calculate_total_penalties(employee_id, start_date=None, end_date=None):
    """Calculate total penalty amount for an employee"""
    penalties, error = get_employee_penalties(employee_id, start_date, end_date)

    if error:
        return Decimal('0.00'), error

    total = sum(p.amount for p in penalties)
    return total, None