"""
Professional Excel Export for Employees
Uses xlsxwriter - most reliable Excel library
"""
from flask import Blueprint, request, send_file, current_app
from database import get_db, Employee
from sqlalchemy.orm import joinedload
import xlsxwriter
from datetime import datetime
import logging
import io
import jwt

export_bp = Blueprint('export', __name__)
logger = logging.getLogger(__name__)


def create_employees_excel(employees):
    """Create Excel file with xlsxwriter - guaranteed to work"""

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet('Xodimlar')

    # Formats
    header_fmt = workbook.add_format({
        'bold': True, 'font_color': 'white', 'bg_color': '#1F4E78',
        'font_name': 'Calibri', 'font_size': 11, 'align': 'center',
        'valign': 'vcenter', 'border': 1
    })

    cell_fmt = workbook.add_format({
        'font_name': 'Calibri', 'font_size': 10, 'border': 1,
        'align': 'left', 'valign': 'vcenter'
    })

    cell_center = workbook.add_format({
        'font_name': 'Calibri', 'font_size': 10, 'border': 1,
        'align': 'center', 'valign': 'vcenter'
    })

    cell_gray = workbook.add_format({
        'font_name': 'Calibri', 'font_size': 10, 'border': 1,
        'align': 'left', 'valign': 'vcenter', 'bg_color': '#F8F9FA'
    })

    cell_center_gray = workbook.add_format({
        'font_name': 'Calibri', 'font_size': 10, 'border': 1,
        'align': 'center', 'valign': 'vcenter', 'bg_color': '#F8F9FA'
    })

    salary_fmt = workbook.add_format({
        'font_name': 'Calibri', 'font_size': 10, 'bold': True,
        'border': 1, 'align': 'right', 'num_format': '#,##0'
    })

    salary_gray = workbook.add_format({
        'font_name': 'Calibri', 'font_size': 10, 'bold': True,
        'border': 1, 'align': 'right', 'num_format': '#,##0',
        'bg_color': '#F8F9FA'
    })

    status_active = workbook.add_format({
        'font_name': 'Calibri', 'font_size': 10, 'bold': True,
        'border': 1, 'align': 'center', 'bg_color': '#D1F2EB'
    })

    status_inactive = workbook.add_format({
        'font_name': 'Calibri', 'font_size': 10, 'bold': True,
        'border': 1, 'align': 'center', 'bg_color': '#F8D7DA'
    })

    # Column widths
    widths = [38, 12, 30, 25, 20, 20, 16, 30, 20, 18, 14, 16, 20, 14, 12]
    for i, width in enumerate(widths):
        worksheet.set_column(i, i, width)

    # Headers
    headers = ['ID', 'Xodim №', "To'liq ismi", 'Lavozim', 'Filial', "Bo'lim",
               'Telefon', 'Email', 'Karta raqami', 'Oylik maosh', 'Maosh turi',
               'Ish boshlagan', 'Ish vaqti', 'Tushlik (daq)', 'Holat']

    worksheet.set_row(0, 35)
    for col, header in enumerate(headers):
        worksheet.write(0, col, header, header_fmt)

    worksheet.freeze_panes(1, 0)

    # Data rows
    for row, emp in enumerate(employees, start=1):
        worksheet.set_row(row, 25)
        is_gray = (row % 2 == 0)

        work_time = ''
        if emp.work_start_time and emp.work_end_time:
            work_time = f"{emp.work_start_time.strftime('%H:%M')} - {emp.work_end_time.strftime('%H:%M')}"

        salary_type = 'Oylik' if emp.salary_type == 'monthly' else 'Kunlik'
        hire_date = emp.hire_date.strftime('%d.%m.%Y') if emp.hire_date else '-'

        status_map = {'active': 'Faol', 'inactive': 'Nofaol', 'on_leave': "Ta'tilda"}
        status_text = status_map.get(emp.status, emp.status)

        c_fmt = cell_center_gray if is_gray else cell_center
        l_fmt = cell_gray if is_gray else cell_fmt
        s_fmt = salary_gray if is_gray else salary_fmt

        worksheet.write(row, 0, emp.id, c_fmt)
        worksheet.write(row, 1, emp.employee_no, c_fmt)
        worksheet.write(row, 2, emp.full_name, l_fmt)
        worksheet.write(row, 3, emp.position or '-', l_fmt)
        worksheet.write(row, 4, emp.branch.name if emp.branch else '-', l_fmt)
        worksheet.write(row, 5, emp.department.name if emp.department else '-', l_fmt)
        worksheet.write(row, 6, emp.phone or '-', c_fmt)
        worksheet.write(row, 7, emp.email or '-', l_fmt)
        worksheet.write(row, 8, emp.card_no or '-', c_fmt)
        worksheet.write(row, 9, emp.salary or 0, s_fmt)
        worksheet.write(row, 10, salary_type, c_fmt)
        worksheet.write(row, 11, hire_date, c_fmt)
        worksheet.write(row, 12, work_time or '-', c_fmt)
        worksheet.write(row, 13, emp.lunch_break_duration or 60, c_fmt)

        if emp.status == 'active':
            worksheet.write(row, 14, status_text, status_active)
        elif emp.status == 'inactive':
            worksheet.write(row, 14, status_text, status_inactive)
        else:
            worksheet.write(row, 14, status_text, c_fmt)

    # Footer
    footer_row = len(employees) + 2
    footer_fmt = workbook.add_format({'font_name': 'Calibri', 'font_size': 10, 'bold': True, 'font_color': '#1F4E78'})
    date_fmt = workbook.add_format({'font_name': 'Calibri', 'font_size': 9, 'italic': True, 'font_color': '#666666'})

    worksheet.write(footer_row, 0, f"Jami xodimlar: {len(employees)}", footer_fmt)
    worksheet.write(footer_row, 4, f"Yaratildi: {datetime.now().strftime('%d.%m.%Y %H:%M')}", date_fmt)

    workbook.close()
    output.seek(0)

    return output


@export_bp.route('/employees', methods=['GET'])
def export_employees():
    """Export employees - with manual auth check"""

    # Manual auth
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        logger.error("❌ No auth header")
        return {'error': 'Unauthorized'}, 401

    try:
        token = auth_header.replace('Bearer ', '')

        # Get secret from Flask config (same as login uses)
        secret = current_app.config.get('JWT_SECRET', 'dev-secret-key-change-me')

        logger.info(f"🔑 Using secret from config")

        payload = jwt.decode(token, secret, algorithms=['HS256'])
        company_id = payload.get('company_id')

        if not company_id:
            logger.error("❌ No company_id in token")
            return {'error': 'Invalid token'}, 401

        logger.info(f"✅ Auth OK: {company_id}")

    except jwt.ExpiredSignatureError:
        logger.error("❌ Token expired")
        return {'error': 'Token expired'}, 401
    except jwt.InvalidSignatureError:
        logger.error("❌ Invalid signature")
        return {'error': 'Invalid token signature'}, 401
    except Exception as e:
        logger.error(f"❌ Auth error: {e}")
        return {'error': 'Invalid token'}, 401

    db = get_db()

    try:
        # Filters
        branch_id = request.args.get('branch_id')
        department_id = request.args.get('department_id')
        status = request.args.get('status')

        # Query
        query = db.query(Employee).filter(
            Employee.company_id == company_id
        ).options(
            joinedload(Employee.branch),
            joinedload(Employee.department)
        )

        if branch_id:
            query = query.filter(Employee.branch_id == branch_id)
        if department_id:
            query = query.filter(Employee.department_id == department_id)
        if status:
            query = query.filter(Employee.status == status)

        employees = query.order_by(Employee.full_name).all()

        if not employees:
            logger.warning(f"⚠️ No employees for {company_id}")
            return {'error': 'Xodimlar topilmadi'}, 404

        logger.info(f"📊 Exporting {len(employees)} employees")

        # Create Excel
        excel_file = create_employees_excel(employees)

        filename = f"Xodimlar_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

        logger.info(f"✅ Sending: {filename}")

        return send_file(
            excel_file,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"❌ Export error: {e}", exc_info=True)
        return {'error': str(e)}, 500
    finally:
        db.close()