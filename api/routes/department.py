from flask import Blueprint, request, jsonify, g
from database import get_db, Department, Employee
from utils.decorators import company_admin_required
from utils.helpers import success_response, error_response
from utils.validators import validate_required_fields
from sqlalchemy import func

department_bp = Blueprint('department', __name__)


@department_bp.route('', methods=['GET'])
@company_admin_required
def list_departments():
    """List all departments"""
    try:
        db = get_db()

        # Get query parameters
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        search = request.args.get('search')

        # Build query
        query = db.query(Department).filter_by(company_id=g.company_id)

        if search:
            query = query.filter(Department.name.ilike(f'%{search}%'))

        # Get total count
        total = query.count()

        # Paginate
        departments = query.order_by(Department.name).offset((page - 1) * per_page).limit(per_page).all()

        # Get employee count for each department
        result = []
        for dept in departments:
            dept_dict = dept.to_dict()
            employee_count = db.query(func.count(Employee.id)).filter_by(department_id=dept.id).scalar()
            dept_dict['employee_count'] = employee_count
            result.append(dept_dict)

        db.close()

        return success_response({
            'departments': result,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })

    except Exception as e:
        return error_response(f"Failed to list departments: {str(e)}", 500)


@department_bp.route('', methods=['POST'])
@company_admin_required
def create_department():
    """Create new department"""
    try:
        data = request.get_json()

        missing_fields = validate_required_fields(data, ['name'])
        if missing_fields:
            return error_response(f"Missing required fields: {', '.join(missing_fields)}", 400)

        name = data.get('name')
        description = data.get('description')

        db = get_db()

        # Check if department name already exists
        existing = db.query(Department).filter_by(
            company_id=g.company_id,
            name=name
        ).first()

        if existing:
            db.close()
            return error_response("Department with this name already exists", 400)

        # Create department
        department = Department(
            company_id=g.company_id,
            name=name,
            description=description
        )

        db.add(department)
        db.commit()
        db.refresh(department)

        result = department.to_dict()
        result['employee_count'] = 0

        db.close()

        return success_response(result, "Department created successfully", 201)

    except Exception as e:
        return error_response(f"Failed to create department: {str(e)}", 500)


@department_bp.route('/<department_id>', methods=['GET'])
@company_admin_required
def get_department(department_id):
    """Get department details"""
    try:
        db = get_db()

        department = db.query(Department).filter_by(
            id=department_id,
            company_id=g.company_id
        ).first()

        if not department:
            db.close()
            return error_response("Department not found", 404)

        result = department.to_dict()

        # Get employee count
        employee_count = db.query(func.count(Employee.id)).filter_by(department_id=department_id).scalar()
        result['employee_count'] = employee_count

        # Get employees
        employees = db.query(Employee).filter_by(department_id=department_id).all()
        result['employees'] = [emp.to_dict() for emp in employees]

        db.close()

        return success_response(result)

    except Exception as e:
        return error_response(f"Failed to get department: {str(e)}", 500)


@department_bp.route('/<department_id>', methods=['PUT'])
@company_admin_required
def update_department(department_id):
    """Update department"""
    try:
        data = request.get_json()

        db = get_db()

        department = db.query(Department).filter_by(
            id=department_id,
            company_id=g.company_id
        ).first()

        if not department:
            db.close()
            return error_response("Department not found", 404)

        # Update fields
        if 'name' in data:
            # Check if new name conflicts
            existing = db.query(Department).filter(
                Department.company_id == g.company_id,
                Department.name == data['name'],
                Department.id != department_id
            ).first()

            if existing:
                db.close()
                return error_response("Department with this name already exists", 400)

            department.name = data['name']

        if 'description' in data:
            department.description = data['description']

        db.commit()
        db.refresh(department)

        result = department.to_dict()
        db.close()

        return success_response(result, "Department updated successfully")

    except Exception as e:
        return error_response(f"Failed to update department: {str(e)}", 500)


@department_bp.route('/<department_id>', methods=['DELETE'])
@company_admin_required
def delete_department(department_id):
    """Delete department"""
    try:
        db = get_db()

        department = db.query(Department).filter_by(
            id=department_id,
            company_id=g.company_id
        ).first()

        if not department:
            db.close()
            return error_response("Department not found", 404)

        # Check if department has employees
        employee_count = db.query(func.count(Employee.id)).filter_by(department_id=department_id).scalar()

        if employee_count > 0:
            db.close()
            return error_response(
                f"Cannot delete department with {employee_count} employees. Please reassign or remove employees first.",
                400)

        department_name = department.name

        db.delete(department)
        db.commit()
        db.close()

        return success_response(None, f"Department '{department_name}' deleted successfully")

    except Exception as e:
        return error_response(f"Failed to delete department: {str(e)}", 500)