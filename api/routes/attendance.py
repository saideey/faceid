from flask import Blueprint, request, jsonify, g
from database import get_db, AttendanceLog, Employee, Department
from utils.decorators import company_admin_required
from utils.helpers import success_response, error_response, parse_date
from datetime import datetime, date, timedelta
from sqlalchemy import and_, func

attendance_bp = Blueprint('attendance', __name__)


@attendance_bp.route('/today', methods=['GET'])
@company_admin_required
def get_today_attendance():
    """Get today's attendance for company"""
    try:
        db = get_db()

        today = date.today()

        # Get all attendance logs for today
        logs = db.query(AttendanceLog).join(Employee).filter(
            and_(
                AttendanceLog.company_id == g.company_id,
                AttendanceLog.date == today
            )
        ).order_by(AttendanceLog.check_in_time.desc()).all()

        # Get total active employees
        total_employees = db.query(Employee).filter_by(
            company_id=g.company_id,
            status='active'
        ).count()

        # Calculate statistics
        present_count = len(logs)
        late_count = len([l for l in logs if l.late_minutes > 0])
        on_time_count = present_count - late_count
        absent_count = total_employees - present_count

        # Format results
        result_logs = []
        for log in logs:
            log_dict = log.to_dict()
            log_dict['employee_name'] = log.employee.full_name
            log_dict['department_name'] = log.employee.department.name if log.employee.department else None
            result_logs.append(log_dict)

        db.close()

        return success_response({
            'date': today.isoformat(),
            'statistics': {
                'total_employees': total_employees,
                'present': present_count,
                'absent': absent_count,
                'on_time': on_time_count,
                'late': late_count
            },
            'attendance': result_logs
        })

    except Exception as e:
        return error_response(f"Failed to get today's attendance: {str(e)}", 500)


@attendance_bp.route('/date-range', methods=['GET'])
@company_admin_required
def get_date_range_attendance():
    """Get attendance for a date range with filters"""
    try:
        # Get query parameters
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        employee_id = request.args.get('employee_id')
        department_id = request.args.get('department_id')
        branch_id = request.args.get('branch_id')  # ✅ ADDED
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))

        if not start_date_str or not end_date_str:
            return error_response("start_date and end_date are required", 400)

        # Parse dates
        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)

        if not start_date or not end_date:
            return error_response("Invalid date format. Use YYYY-MM-DD", 400)

        if start_date > end_date:
            return error_response("start_date must be before or equal to end_date", 400)

        db = get_db()

        # Build query
        query = db.query(AttendanceLog).join(Employee).filter(
            and_(
                AttendanceLog.company_id == g.company_id,
                AttendanceLog.date >= start_date,
                AttendanceLog.date <= end_date
            )
        )

        if employee_id:
            query = query.filter(AttendanceLog.employee_id == employee_id)

        if department_id:
            query = query.filter(Employee.department_id == department_id)

        if branch_id:
            query = query.filter(Employee.branch_id == branch_id)

        # Get total count
        total = query.count()

        # Paginate
        logs = query.order_by(AttendanceLog.date.desc(), AttendanceLog.check_in_time.desc()).offset(
            (page - 1) * per_page).limit(per_page).all()

        # Format results
        result_logs = []
        for log in logs:
            log_dict = log.to_dict()
            log_dict['employee_name'] = log.employee.full_name
            log_dict['employee_no'] = log.employee.employee_no
            log_dict['department_name'] = log.employee.department.name if log.employee.department else None
            result_logs.append(log_dict)

        db.close()

        return success_response({
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'attendance': result_logs,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })

    except Exception as e:
        return error_response(f"Failed to get attendance: {str(e)}", 500)


@attendance_bp.route('/employee/<employee_id>', methods=['GET'])
@company_admin_required
def get_employee_attendance(employee_id):
    """Get attendance history for specific employee"""
    try:
        # Get query parameters
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))

        db = get_db()

        # Verify employee exists and belongs to company
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            db.close()
            return error_response("Employee not found", 404)

        # Build query
        query = db.query(AttendanceLog).filter_by(employee_id=employee_id)

        if start_date_str:
            start_date = parse_date(start_date_str)
            if start_date:
                query = query.filter(AttendanceLog.date >= start_date)

        if end_date_str:
            end_date = parse_date(end_date_str)
            if end_date:
                query = query.filter(AttendanceLog.date <= end_date)

        # Get total count
        total = query.count()

        # Paginate
        logs = query.order_by(AttendanceLog.date.desc()).offset((page - 1) * per_page).limit(per_page).all()

        # Calculate statistics
        total_days = len(logs)
        total_late = len([l for l in logs if l.late_minutes > 0])
        total_late_minutes = sum(l.late_minutes for l in logs)
        total_work_minutes = sum(l.total_work_minutes for l in logs if l.total_work_minutes)
        avg_work_hours = (total_work_minutes / 60 / total_days) if total_days > 0 else 0

        # Format results
        result_logs = [log.to_dict() for log in logs]

        db.close()

        return success_response({
            'employee': employee.to_dict(),
            'statistics': {
                'total_days': total_days,
                'days_late': total_late,
                'total_late_minutes': total_late_minutes,
                'total_work_hours': round(total_work_minutes / 60, 2),
                'avg_work_hours': round(avg_work_hours, 2)
            },
            'attendance': result_logs,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })

    except Exception as e:
        return error_response(f"Failed to get employee attendance: {str(e)}", 500)


@attendance_bp.route('/statistics', methods=['GET'])
@company_admin_required
def get_statistics():
    """Get attendance statistics for a specific date"""
    try:
        date_str = request.args.get('date', date.today().isoformat())
        target_date = parse_date(date_str)

        if not target_date:
            return error_response("Invalid date format. Use YYYY-MM-DD", 400)

        from services.report_service import get_daily_statistics

        stats, error = get_daily_statistics(g.company_id, target_date)

        if error:
            return error_response(error, 500)

        return success_response(stats)

    except Exception as e:
        return error_response(f"Failed to get statistics: {str(e)}", 500)


@attendance_bp.route('/absent-employees', methods=['GET'])
@company_admin_required
def get_absent_employees():
    """Get list of employees who are absent today"""
    try:
        db = get_db()

        today = date.today()

        # Get all active employees
        all_employees = db.query(Employee).filter_by(
            company_id=g.company_id,
            status='active'
        ).all()

        # Get employees who checked in today
        present_employee_ids = db.query(AttendanceLog.employee_id).filter(
            and_(
                AttendanceLog.company_id == g.company_id,
                AttendanceLog.date == today
            )
        ).all()

        present_ids = [emp_id[0] for emp_id in present_employee_ids]

        # Find absent employees
        absent_employees = [emp for emp in all_employees if emp.id not in present_ids]

        # Format results
        result = []
        for emp in absent_employees:
            emp_dict = emp.to_dict()
            emp_dict['department_name'] = emp.department.name if emp.department else None
            result.append(emp_dict)

        db.close()

        return success_response({
            'date': today.isoformat(),
            'total_absent': len(absent_employees),
            'absent_employees': result
        })

    except Exception as e:
        return error_response(f"Failed to get absent employees: {str(e)}", 500)


@attendance_bp.route('/late-employees', methods=['GET'])
@company_admin_required
def get_late_employees():
    """Get list of employees who were late today"""
    try:
        db = get_db()

        today = date.today()

        # Get all late employees for today
        late_logs = db.query(AttendanceLog).join(Employee).filter(
            and_(
                AttendanceLog.company_id == g.company_id,
                AttendanceLog.date == today,
                AttendanceLog.late_minutes > 0
            )
        ).order_by(AttendanceLog.late_minutes.desc()).all()

        # Format results
        result = []
        for log in late_logs:
            result.append({
                'employee_id': str(log.employee_id),
                'employee_no': log.employee.employee_no,
                'employee_name': log.employee.full_name,
                'department_name': log.employee.department.name if log.employee.department else None,
                'check_in_time': log.check_in_time.isoformat() if log.check_in_time else None,
                'late_minutes': log.late_minutes,
                'work_start_time': log.employee.work_start_time.strftime('%H:%M:%S')
            })

        db.close()

        return success_response({
            'date': today.isoformat(),
            'total_late': len(late_logs),
            'late_employees': result
        })

    except Exception as e:
        return error_response(f"Failed to get late employees: {str(e)}", 500)


# ... existing imports ...

@attendance_bp.route('/custom-range', methods=['GET'])
@company_admin_required
def get_custom_range_attendance():
    """Get attendance statistics for custom date range with detailed breakdown"""
    try:
        # Get query parameters
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        employee_id = request.args.get('employee_id')
        department_id = request.args.get('department_id')

        if not start_date_str or not end_date_str:
            return error_response("start_date and end_date are required", 400)

        from utils.helpers import parse_date
        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)

        if not start_date or not end_date:
            return error_response("Invalid date format. Use YYYY-MM-DD", 400)

        if start_date > end_date:
            return error_response("start_date must be before or equal to end_date", 400)

        db = get_db()

        # Build query
        query = db.query(AttendanceLog).join(Employee).filter(
            and_(
                AttendanceLog.company_id == g.company_id,
                AttendanceLog.date >= start_date,
                AttendanceLog.date <= end_date
            )
        )

        if employee_id:
            query = query.filter(AttendanceLog.employee_id == employee_id)

        if department_id:
            query = query.filter(Employee.department_id == department_id)

        logs = query.order_by(AttendanceLog.date.desc(), AttendanceLog.check_in_time.desc()).all()

        # Calculate overall statistics
        total_days = (end_date - start_date).days + 1
        total_present = len(logs)
        total_late = len([l for l in logs if l.late_minutes > 0])
        on_time_count = total_present - total_late
        total_late_minutes = sum(l.late_minutes for l in logs)
        total_work_minutes = sum(l.total_work_minutes for l in logs if l.total_work_minutes)

        # Get total active employees for absence calculation
        total_employees = db.query(Employee).filter_by(
            company_id=g.company_id,
            status='active'
        ).count()

        if employee_id:
            total_employees = 1

        total_absent = (total_employees * total_days) - total_present

        # Format results with detailed info
        result_logs = []
        for log in logs:
            log_dict = log.to_dict()
            log_dict['employee_name'] = log.employee.full_name
            log_dict['employee_no'] = log.employee.employee_no
            log_dict['department_name'] = log.employee.department.name if log.employee.department else None
            log_dict['status'] = 'on_time' if log.late_minutes == 0 else 'late'
            log_dict['work_hours'] = round(log.total_work_minutes / 60, 2) if log.total_work_minutes else 0
            result_logs.append(log_dict)

        db.close()

        return success_response({
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'total_days': total_days
            },
            'statistics': {
                'total_employees': total_employees,
                'total_present': total_present,
                'total_absent': total_absent,
                'on_time': on_time_count,
                'late': total_late,
                'total_late_minutes': total_late_minutes,
                'total_work_hours': round(total_work_minutes / 60, 2),
                'avg_work_hours': round(total_work_minutes / 60 / total_present, 2) if total_present > 0 else 0
            },
            'attendance': result_logs
        })

    except Exception as e:
        return error_response(f"Failed to get custom range attendance: {str(e)}", 500)


@attendance_bp.route('/employee/<employee_id>/calendar', methods=['GET'])
@company_admin_required
def get_employee_calendar(employee_id):
    """Get employee attendance calendar with daily status for UI display"""
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if not start_date_str or not end_date_str:
            return error_response("start_date and end_date are required", 400)

        from utils.helpers import parse_date
        from datetime import timedelta

        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)

        if not start_date or not end_date:
            return error_response("Invalid date format. Use YYYY-MM-DD", 400)

        db = get_db()

        # Verify employee
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            db.close()
            return error_response("Employee not found", 404)

        # Get all attendance logs for the period
        logs = db.query(AttendanceLog).filter(
            and_(
                AttendanceLog.employee_id == employee_id,
                AttendanceLog.date >= start_date,
                AttendanceLog.date <= end_date
            )
        ).all()

        # Create a dictionary for quick lookup
        logs_dict = {log.date: log for log in logs}

        # Generate calendar data for each day
        calendar_data = []
        current_date = start_date

        while current_date <= end_date:
            log = logs_dict.get(current_date)

            if log:
                day_data = {
                    'date': current_date.isoformat(),
                    'day_of_week': current_date.strftime('%A'),
                    'status': 'late' if log.late_minutes > 0 else 'on_time',
                    'check_in_time': log.check_in_time.strftime('%H:%M:%S') if log.check_in_time else None,
                    'check_out_time': log.check_out_time.strftime('%H:%M:%S') if log.check_out_time else None,
                    'late_minutes': log.late_minutes,
                    'work_hours': round(log.total_work_minutes / 60, 2) if log.total_work_minutes else 0,
                    'overtime_minutes': log.overtime_minutes,
                    'present': True
                }
            else:
                day_data = {
                    'date': current_date.isoformat(),
                    'day_of_week': current_date.strftime('%A'),
                    'status': 'absent',
                    'check_in_time': None,
                    'check_out_time': None,
                    'late_minutes': 0,
                    'work_hours': 0,
                    'overtime_minutes': 0,
                    'present': False
                }

            calendar_data.append(day_data)
            current_date += timedelta(days=1)

        # Calculate summary
        present_days = len([d for d in calendar_data if d['present']])
        late_days = len([d for d in calendar_data if d['status'] == 'late'])
        on_time_days = len([d for d in calendar_data if d['status'] == 'on_time'])
        absent_days = len([d for d in calendar_data if d['status'] == 'absent'])

        db.close()

        return success_response({
            'employee': employee.to_dict(),
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'total_days': len(calendar_data)
            },
            'summary': {
                'present_days': present_days,
                'late_days': late_days,
                'on_time_days': on_time_days,
                'absent_days': absent_days
            },
            'calendar': calendar_data
        })

    except Exception as e:
        return error_response(f"Failed to get employee calendar: {str(e)}", 500)