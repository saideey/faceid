"""
Reports Blueprint - Attendance and Salary Reports
Provides data for charts and Excel exports
"""
from flask import Blueprint, request, send_file, current_app
from database import get_db, Employee, AttendanceLog, Penalty, Bonus, EmployeeSchedule
from sqlalchemy import func, and_, case
from sqlalchemy.orm import joinedload
from datetime import datetime, date, timedelta
import xlsxwriter
import logging
import io
import jwt
import calendar

reports_bp = Blueprint('reports', __name__)
logger = logging.getLogger(__name__)


def get_auth_company_id():
    """Extract company_id from JWT token"""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return None

    try:
        token = auth_header.replace('Bearer ', '')
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload.get('company_id')
    except:
        return None


@reports_bp.route('/attendance', methods=['GET'])
def attendance_report():
    """
    Attendance report data for charts

    Query params:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
        branch_id: optional
        department_id: optional
    """
    company_id = get_auth_company_id()
    if not company_id:
        logger.error("❌ No company_id in attendance report")
        return {'error': 'Unauthorized', 'success': False}, 401

    logger.info(f"📊 Attendance report requested for company: {company_id}")

    db = get_db()

    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        branch_id = request.args.get('branch_id')
        department_id = request.args.get('department_id')

        logger.info(f"📅 Date range: {start_date_str} to {end_date_str}")

        if not start_date_str or not end_date_str:
            logger.error("❌ Missing dates")
            return {'error': 'start_date and end_date required', 'success': False}, 400

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        # Build query
        query = db.query(AttendanceLog).join(Employee).filter(
            Employee.company_id == company_id,
            AttendanceLog.date >= start_date,
            AttendanceLog.date <= end_date
        )

        if branch_id and branch_id != '':
            query = query.filter(Employee.branch_id == branch_id)
        if department_id and department_id != '':
            query = query.filter(Employee.department_id == department_id)

        logs = query.all()

        logger.info(f"📋 Found {len(logs)} attendance records")

        # Daily stats
        daily_stats = {}
        for log in logs:
            date_key = log.date.isoformat()
            if date_key not in daily_stats:
                daily_stats[date_key] = {'date': date_key, 'present': 0, 'late': 0, 'total_minutes': 0}

            daily_stats[date_key]['present'] += 1
            if log.late_minutes and log.late_minutes > 0:
                daily_stats[date_key]['late'] += 1
            if log.total_work_minutes:
                daily_stats[date_key]['total_minutes'] += log.total_work_minutes

        # Convert to list and sort
        daily_data = sorted(daily_stats.values(), key=lambda x: x['date'])

        # Summary
        total_logs = len(logs)
        total_late = sum(1 for log in logs if log.late_minutes and log.late_minutes > 0)
        total_work_hours = sum(log.total_work_minutes or 0 for log in logs) / 60

        logger.info(f"✅ Report generated successfully")

        return {
            'success': True,
            'data': {
                'daily': daily_data,
                'summary': {
                    'total_records': total_logs,
                    'total_late': total_late,
                    'late_percentage': round((total_late / total_logs * 100) if total_logs > 0 else 0, 1),
                    'total_work_hours': round(total_work_hours, 1),
                    'average_work_hours': round(total_work_hours / len(daily_data) if daily_data else 0, 1)
                }
            }
        }, 200

    except Exception as e:
        logger.error(f"❌ Error in attendance report: {e}", exc_info=True)
        return {'error': str(e), 'success': False}, 500
    finally:
        db.close()


@reports_bp.route('/salary', methods=['GET'])
def salary_report():
    """
    Salary report data for charts

    Query params:
        month: 1-12
        year: YYYY
        branch_id: optional
    """
    company_id = get_auth_company_id()
    if not company_id:
        logger.error("❌ No company_id in salary report")
        return {'error': 'Unauthorized', 'success': False}, 401

    logger.info(f"💰 Salary report requested for company: {company_id}")

    db = get_db()

    try:
        month = int(request.args.get('month', datetime.now().month))
        year = int(request.args.get('year', datetime.now().year))
        branch_id = request.args.get('branch_id')

        logger.info(f"📅 Month: {month}, Year: {year}")

        # Date range
        start_date = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        end_date = date(year, month, last_day)

        # Get employees
        query = db.query(Employee).filter(Employee.company_id == company_id)
        if branch_id and branch_id != '':
            query = query.filter(Employee.branch_id == branch_id)

        employees = query.all()

        logger.info(f"👥 Found {len(employees)} employees")

        # Calculate totals
        total_base_salary = sum(emp.salary or 0 for emp in employees if emp.salary_type == 'monthly')

        # Get penalties
        penalties = db.query(func.sum(Penalty.amount)).join(Employee).filter(
            Employee.company_id == company_id,
            Penalty.date >= start_date,
            Penalty.date <= end_date,
            Penalty.is_waived == False
        ).scalar() or 0

        # Get bonuses
        bonuses = db.query(func.sum(Bonus.amount)).join(Employee).filter(
            Employee.company_id == company_id,
            Bonus.date >= start_date,
            Bonus.date <= end_date
        ).scalar() or 0

        logger.info(f"💵 Salary: {total_base_salary}, Penalties: {penalties}, Bonuses: {bonuses}")

        # Department breakdown
        dept_data = db.query(
            Employee.department_id,
            func.count(Employee.id).label('count'),
            func.sum(Employee.salary).label('total_salary')
        ).filter(
            Employee.company_id == company_id
        ).group_by(Employee.department_id).all()

        logger.info(f"✅ Salary report generated successfully")

        return {
            'success': True,
            'data': {
                'summary': {
                    'total_employees': len(employees),
                    'total_base_salary': float(total_base_salary),
                    'total_penalties': float(penalties),
                    'total_bonuses': float(bonuses),
                    'net_payroll': float(total_base_salary - penalties + bonuses)
                },
                'by_department': [
                    {
                        'department_id': d[0],
                        'count': d[1],
                        'total': float(d[2] or 0)
                    }
                    for d in dept_data
                ]
            }
        }, 200

    except Exception as e:
        logger.error(f"❌ Error in salary report: {e}", exc_info=True)
        return {'error': str(e), 'success': False}, 500
    finally:
        db.close()


@reports_bp.route('/export/attendance', methods=['GET'])
def export_attendance():
    """Export attendance report to Excel"""
    company_id = get_auth_company_id()
    if not company_id:
        return {'error': 'Unauthorized'}, 401

    db = get_db()

    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        branch_id = request.args.get('branch_id')

        if not start_date_str or not end_date_str:
            return {'error': 'start_date and end_date required'}, 400

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        # Get data
        query = db.query(AttendanceLog).join(Employee).filter(
            Employee.company_id == company_id,
            AttendanceLog.date >= start_date,
            AttendanceLog.date <= end_date
        ).options(joinedload(AttendanceLog.employee))

        if branch_id:
            query = query.filter(Employee.branch_id == branch_id)

        logs = query.order_by(AttendanceLog.date.desc()).all()

        # Create Excel
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Davomat')

        # Formats
        header_fmt = workbook.add_format({
            'bold': True, 'font_color': 'white', 'bg_color': '#1F4E78',
            'font_name': 'Calibri', 'font_size': 11, 'align': 'center', 'border': 1
        })

        cell_fmt = workbook.add_format({
            'font_name': 'Calibri', 'font_size': 10, 'border': 1
        })

        cell_center = workbook.add_format({
            'font_name': 'Calibri', 'font_size': 10, 'border': 1, 'align': 'center'
        })

        late_fmt = workbook.add_format({
            'font_name': 'Calibri', 'font_size': 10, 'border': 1,
            'bg_color': '#FFF3CD', 'align': 'center'
        })

        # Headers
        headers = ['Sana', 'Xodim', 'Filial', 'Kirish', 'Chiqish', 'Ish vaqti', 'Kech qolish', 'Holat']
        widths = [12, 30, 20, 12, 12, 12, 12, 15]

        for i, width in enumerate(widths):
            worksheet.set_column(i, i, width)

        worksheet.set_row(0, 30)
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_fmt)

        # Data
        for row, log in enumerate(logs, start=1):
            worksheet.set_row(row, 20)

            status = 'O\'z vaqtida' if not log.late_minutes or log.late_minutes == 0 else f'Kech: {log.late_minutes} min'
            late_cell_fmt = late_fmt if log.late_minutes and log.late_minutes > 0 else cell_center

            worksheet.write(row, 0, log.date.strftime('%d.%m.%Y'), cell_center)
            worksheet.write(row, 1, log.employee.full_name, cell_fmt)
            worksheet.write(row, 2, log.employee.branch.name if log.employee.branch else '-', cell_fmt)
            worksheet.write(row, 3, log.check_in_time.strftime('%H:%M') if log.check_in_time else '-', cell_center)
            worksheet.write(row, 4, log.check_out_time.strftime('%H:%M') if log.check_out_time else '-', cell_center)
            worksheet.write(row, 5, f"{int(log.total_work_minutes/60)}:{int(log.total_work_minutes%60):02d}" if log.total_work_minutes else '-', cell_center)
            worksheet.write(row, 6, log.late_minutes or 0, cell_center)
            worksheet.write(row, 7, status, late_cell_fmt)

        workbook.close()
        output.seek(0)

        filename = f"Davomat_{start_date_str}_to_{end_date_str}.xlsx"

        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"Error exporting attendance: {e}", exc_info=True)
        return {'error': str(e)}, 500
    finally:
        db.close()


@reports_bp.route('/export/salary', methods=['GET'])
def export_salary():
    """Export salary report to Excel"""
    company_id = get_auth_company_id()
    if not company_id:
        return {'error': 'Unauthorized'}, 401

    # This can use the existing salary export from salary.py
    # Or create custom summary report here
    return {'message': 'Use /api/salary/custom-report or /api/export/employees'}, 200