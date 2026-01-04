from flask import Blueprint, request, jsonify, g
from database import get_db, Employee, Department, Company, Branch
from middleware.auth_middleware import require_auth
from middleware.company_middleware import load_company_context, check_employee_limit
from utils.helpers import success_response, error_response, save_uploaded_file, get_file_url
from config.settings import Config
import os
import logging
from datetime import datetime, time as datetime_time

employee_bp = Blueprint('employee', __name__)
logger = logging.getLogger(__name__)


def parse_time(time_str):
    """Parse time string to time object"""
    if not time_str:
        return None
    try:
        if isinstance(time_str, datetime_time):
            return time_str
        # Handle HH:MM:SS or HH:MM format
        parts = time_str.split(':')
        if len(parts) == 2:
            return datetime_time(int(parts[0]), int(parts[1]))
        elif len(parts) == 3:
            return datetime_time(int(parts[0]), int(parts[1]), int(parts[2]))
        return None
    except:
        return None


@employee_bp.route('/', methods=['GET'])
@require_auth
@load_company_context
def list_employees():
    """List all employees for the authenticated company"""
    db = get_db()

    try:
        # Get query parameters
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        branch_id = request.args.get('branch_id')
        department_id = request.args.get('department_id')
        status = request.args.get('status')
        search = request.args.get('search')

        # Build query
        query = db.query(Employee).filter_by(company_id=g.company_id)

        # Filter by branch
        if branch_id:
            query = query.filter_by(branch_id=branch_id)

        # Filter by department
        if department_id:
            query = query.filter_by(department_id=department_id)

        # Filter by status
        if status:
            query = query.filter_by(status=status)

        # Search by name or employee number
        if search:
            query = query.filter(
                (Employee.full_name.ilike(f'%{search}%')) |
                (Employee.employee_no.ilike(f'%{search}%'))
            )

        # Get total count
        total = query.count()

        # Paginate
        employees = query.order_by(Employee.full_name).offset((page - 1) * per_page).limit(per_page).all()

        # Format result with relationships
        result = []
        for emp in employees:
            emp_dict = emp.to_dict()
            result.append(emp_dict)

        return success_response({
            'employees': result,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })

    except Exception as e:
        logger.error(f"Error listing employees: {str(e)}")
        return error_response(f"Failed to list employees: {str(e)}", 500)
    finally:
        db.close()


@employee_bp.route('/', methods=['POST'])
@require_auth
@load_company_context
def create_employee():
    """Create new employee"""
    db = get_db()

    try:
        data = request.get_json()

        # Validate required fields
        if not data.get('employee_no'):
            return error_response("Employee number is required", 400)
        if not data.get('full_name'):
            return error_response("Full name is required", 400)

        employee_no = data.get('employee_no')
        full_name = data.get('full_name')

        # Check employee limit
        can_add, message = check_employee_limit()
        if not can_add:
            return error_response(message, 400)

        # Validate branch if provided
        branch_id = data.get('branch_id')
        if branch_id:
            branch = db.query(Branch).filter_by(
                id=branch_id,
                company_id=g.company_id
            ).first()
            if not branch:
                return error_response("Branch not found", 404)

        # Check if employee_no already exists in this company+branch
        existing = db.query(Employee).filter_by(
            company_id=g.company_id,
            branch_id=branch_id,
            employee_no=employee_no
        ).first()

        if existing:
            branch_info = f" in branch {existing.branch.name}" if existing.branch else ""
            return error_response(
                f"Employee number {employee_no} already exists{branch_info}",
                400
            )

        # Validate department if provided
        department_id = data.get('department_id')
        if department_id:
            department = db.query(Department).filter_by(
                id=department_id,
                company_id=g.company_id
            ).first()
            if not department:
                return error_response("Department not found", 404)

        # Parse work times
        work_start_time = parse_time(data.get('work_start_time', '09:00:00'))
        work_end_time = parse_time(data.get('work_end_time', '18:00:00'))

        # Create employee
        employee = Employee(
            company_id=g.company_id,
            branch_id=branch_id,
            department_id=department_id,
            employee_no=employee_no,
            full_name=full_name,
            email=data.get('email'),
            phone=data.get('phone'),
            card_no=data.get('card_no'),
            position=data.get('position'),
            hire_date=data.get('hire_date'),
            work_start_time=work_start_time,
            work_end_time=work_end_time,
            lunch_break_duration=data.get('lunch_break_duration', 60),
            status=data.get('status', 'active'),
            salary_type=data.get('salary_type', 'monthly'),
            salary=data.get('salary')
        )

        db.add(employee)
        db.commit()
        db.refresh(employee)

        logger.info(f"Employee created: {employee.full_name} (ID: {employee.id})")

        result = employee.to_dict()

        return success_response(result, "Employee created successfully", 201)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating employee: {str(e)}")
        return error_response(f"Failed to create employee: {str(e)}", 500)
    finally:
        db.close()


@employee_bp.route('/<employee_id>', methods=['GET'])
@require_auth
@load_company_context
def get_employee(employee_id):
    """Get employee details"""
    db = get_db()

    try:
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        result = employee.to_dict()

        return success_response(result)

    except Exception as e:
        logger.error(f"Error getting employee: {str(e)}")
        return error_response(f"Failed to get employee: {str(e)}", 500)
    finally:
        db.close()


@employee_bp.route('/<employee_id>', methods=['PUT'])
@require_auth
@load_company_context
def update_employee(employee_id):
    """Update employee"""
    db = get_db()

    try:
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        data = request.get_json()

        # Update employee_no with validation
        if 'employee_no' in data and data['employee_no'] != employee.employee_no:
            # Check if new employee_no conflicts
            existing = db.query(Employee).filter_by(
                company_id=g.company_id,
                branch_id=data.get('branch_id', employee.branch_id),
                employee_no=data['employee_no']
            ).first()
            if existing and existing.id != employee.id:
                return error_response(f"Employee number {data['employee_no']} already exists", 400)
            employee.employee_no = data['employee_no']

        # Update branch with validation
        if 'branch_id' in data:
            if data['branch_id']:
                branch = db.query(Branch).filter_by(
                    id=data['branch_id'],
                    company_id=g.company_id
                ).first()
                if not branch:
                    return error_response("Branch not found", 404)
            employee.branch_id = data['branch_id']

        # Update department with validation
        if 'department_id' in data:
            if data['department_id']:
                department = db.query(Department).filter_by(
                    id=data['department_id'],
                    company_id=g.company_id
                ).first()
                if not department:
                    return error_response("Department not found", 404)
            employee.department_id = data['department_id']

        # Update other fields
        if 'full_name' in data:
            employee.full_name = data['full_name']

        if 'email' in data:
            employee.email = data['email']

        if 'phone' in data:
            employee.phone = data['phone']

        if 'card_no' in data:
            employee.card_no = data['card_no']

        if 'position' in data:
            employee.position = data['position']

        if 'hire_date' in data:
            employee.hire_date = data['hire_date']

        if 'status' in data:
            employee.status = data['status']

        if 'work_start_time' in data:
            work_start = parse_time(data['work_start_time'])
            if work_start:
                employee.work_start_time = work_start

        if 'work_end_time' in data:
            work_end = parse_time(data['work_end_time'])
            if work_end:
                employee.work_end_time = work_end

        if 'lunch_break_duration' in data:
            employee.lunch_break_duration = data['lunch_break_duration']

        if 'salary_type' in data:
            employee.salary_type = data['salary_type']

        if 'salary' in data:
            employee.salary = data['salary']

        db.commit()
        db.refresh(employee)

        logger.info(f"Employee updated: {employee.full_name} (ID: {employee.id})")

        result = employee.to_dict()

        return success_response(result, "Employee updated successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating employee: {str(e)}")
        return error_response(f"Failed to update employee: {str(e)}", 500)
    finally:
        db.close()


@employee_bp.route('/<employee_id>', methods=['DELETE'])
@require_auth
@load_company_context
def delete_employee(employee_id):
    """Delete employee"""
    db = get_db()

    try:
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        employee_name = employee.full_name

        # ✅ Import Penalty model
        from database import Penalty, AttendanceLog

        # ✅ Step 1: Delete related penalties first (to avoid foreign key constraint)
        penalties = db.query(Penalty).filter_by(employee_id=employee_id).all()
        penalty_count = len(penalties)
        for penalty in penalties:
            db.delete(penalty)

        # Commit penalties deletion first
        db.commit()
        logger.info(f"✅ Deleted {penalty_count} penalties for employee {employee_name}")

        # ✅ Step 2: Delete attendance logs (now penalties are gone, no FK constraint)
        attendance_logs = db.query(AttendanceLog).filter_by(employee_id=employee_id).all()
        log_count = len(attendance_logs)
        for log in attendance_logs:
            db.delete(log)

        # Commit attendance logs deletion
        db.commit()
        logger.info(f"✅ Deleted {log_count} attendance logs for employee {employee_name}")

        # ✅ Step 3: Now delete employee (everything is clean)
        db.delete(employee)
        db.commit()

        logger.info(f"Employee deleted: {employee_name} (ID: {employee_id})")

        return success_response(None, f"Employee '{employee_name}' deleted successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting employee: {str(e)}", exc_info=True)
        return error_response(f"Failed to delete employee: {str(e)}", 500)
    finally:
        db.close()


@employee_bp.route('/<employee_id>/photo', methods=['POST'])
@require_auth
@load_company_context
def upload_photo(employee_id):
    """Upload employee photo"""
    db = get_db()

    try:
        if 'photo' not in request.files:
            return error_response("No photo file provided", 400)

        file = request.files['photo']

        if file.filename == '':
            return error_response("No file selected", 400)

        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        # Save file
        filename = save_uploaded_file(file, Config.PHOTO_FOLDER, Config.ALLOWED_EXTENSIONS)

        if not filename:
            return error_response("Invalid file type. Allowed: png, jpg, jpeg, gif", 400)

        # Delete old photo if exists
        if employee.photo_url:
            old_file_path = os.path.join(Config.PHOTO_FOLDER, employee.photo_url)
            if os.path.exists(old_file_path):
                try:
                    os.remove(old_file_path)
                except Exception as e:
                    logger.warning(f"Failed to delete old photo: {str(e)}")

        employee.photo_url = filename

        db.commit()

        photo_url = get_file_url(filename, 'photos')

        logger.info(f"Photo uploaded for employee: {employee.full_name}")

        return success_response({
            'photo_url': photo_url,
            'filename': filename
        }, "Photo uploaded successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error uploading photo: {str(e)}")
        return error_response(f"Failed to upload photo: {str(e)}", 500)
    finally:
        db.close()


@employee_bp.route('/bulk-import', methods=['POST'])
@require_auth
@load_company_context
def bulk_import_employees():
    """Bulk import employees from CSV/Excel"""
    db = get_db()

    try:
        # This can be implemented later for bulk employee import
        return error_response("Bulk import not yet implemented", 501)

    except Exception as e:
        logger.error(f"Error bulk importing employees: {str(e)}")
        return error_response(f"Failed to bulk import: {str(e)}", 500)
    finally:
        db.close()