from flask import Blueprint, request, jsonify, g
from database import get_db, Branch, Company, Employee
from middleware.auth_middleware import require_auth
from utils.helpers import success_response, error_response
import logging

branch_bp = Blueprint('branch', __name__)
logger = logging.getLogger(__name__)


@branch_bp.route('/', methods=['GET'])
@require_auth
def list_branches():
    """Get all branches - Super Admin sees all, Company Admin sees only their branches"""
    db = get_db()

    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))

        # Build query based on user type
        if g.user_type == 'superadmin':
            # Super admin can see all branches
            company_id = request.args.get('company_id')
            if company_id:
                query = db.query(Branch).filter_by(company_id=company_id)
            else:
                query = db.query(Branch)
        elif g.user_type == 'company_admin':
            # Company admin sees only their branches
            query = db.query(Branch).filter_by(company_id=g.company_id)
        else:
            return error_response("Unauthorized", 403)

        # Filter by status if provided
        status = request.args.get('status')
        if status:
            query = query.filter_by(status=status)

        # Search by name
        search = request.args.get('search')
        if search:
            query = query.filter(Branch.name.ilike(f'%{search}%'))

        # Order by name
        query = query.order_by(Branch.name)

        # Pagination
        total = query.count()
        branches = query.offset((page - 1) * per_page).limit(per_page).all()

        return success_response({
            'branches': [branch.to_dict() for branch in branches],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })

    except Exception as e:
        logger.error(f"Error listing branches: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@branch_bp.route('/<branch_id>', methods=['GET'])
@require_auth
def get_branch(branch_id):
    """Get branch by ID"""
    db = get_db()

    try:
        branch = db.query(Branch).filter_by(id=branch_id).first()

        if not branch:
            return error_response("Branch not found", 404)

        # Check access
        if g.user_type == 'company_admin' and branch.company_id != g.company_id:
            return error_response("Access denied", 403)

        return success_response(branch.to_dict())

    except Exception as e:
        logger.error(f"Error getting branch: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@branch_bp.route('/', methods=['POST'])
@require_auth
def create_branch():
    """Create new branch - Super Admin or Company Admin"""
    db = get_db()

    try:
        data = request.get_json()

        # Validate required fields
        if not data.get('name'):
            return error_response("Branch name is required", 400)

        # Determine company_id based on user type
        if g.user_type == 'superadmin':
            # Super Admin must provide company_id
            company_id = data.get('company_id')
            if not company_id:
                return error_response("company_id is required for super admin", 400)
        elif g.user_type == 'company_admin':
            # Company Admin uses their company_id
            company_id = g.company_id
        else:
            return error_response("Unauthorized", 403)

        # Verify company exists
        company = db.query(Company).filter_by(id=company_id).first()
        if not company:
            return error_response("Company not found", 404)

        # Check if branch code already exists (if provided)
        if data.get('code'):
            existing = db.query(Branch).filter_by(
                company_id=company_id,
                code=data['code']
            ).first()
            if existing:
                return error_response(f"Branch code '{data['code']}' already exists", 400)

        # Create branch
        branch = Branch(
            company_id=company_id,
            name=data['name'],
            code=data.get('code'),
            address=data.get('address'),
            phone=data.get('phone'),
            manager_name=data.get('manager_name'),
            status='active'
        )

        db.add(branch)
        db.commit()
        db.refresh(branch)

        logger.info(f"Branch created: {branch.name} (ID: {branch.id}) for company {company_id}")

        return success_response(branch.to_dict(), "Branch created successfully", 201)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating branch: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@branch_bp.route('/<branch_id>', methods=['PUT'])
@require_auth
def update_branch(branch_id):
    """Update branch"""
    db = get_db()

    try:
        branch = db.query(Branch).filter_by(id=branch_id).first()

        if not branch:
            return error_response("Branch not found", 404)

        # Check access
        if g.user_type == 'company_admin' and branch.company_id != g.company_id:
            return error_response("Access denied", 403)

        data = request.get_json()

        # Check if new code conflicts with existing
        if data.get('code') and data['code'] != branch.code:
            existing = db.query(Branch).filter_by(
                company_id=branch.company_id,
                code=data['code']
            ).first()
            if existing:
                return error_response(f"Branch code '{data['code']}' already exists", 400)

        # Update fields
        if 'name' in data:
            branch.name = data['name']
        if 'code' in data:
            branch.code = data['code']
        if 'address' in data:
            branch.address = data['address']
        if 'phone' in data:
            branch.phone = data['phone']
        if 'manager_name' in data:
            branch.manager_name = data['manager_name']
        if 'status' in data:
            branch.status = data['status']

        db.commit()
        db.refresh(branch)

        logger.info(f"Branch updated: {branch.name} (ID: {branch.id})")

        return success_response(branch.to_dict(), "Branch updated successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating branch: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@branch_bp.route('/<branch_id>', methods=['DELETE'])
@require_auth
def delete_branch(branch_id):
    """Delete branch"""
    db = get_db()

    try:
        branch = db.query(Branch).filter_by(id=branch_id).first()

        if not branch:
            return error_response("Branch not found", 404)

        # Check access
        if g.user_type == 'company_admin' and branch.company_id != g.company_id:
            return error_response("Access denied", 403)

        # Check if branch has employees
        employee_count = db.query(Employee).filter_by(branch_id=branch_id).count()
        if employee_count > 0:
            return error_response(
                f"Cannot delete branch with {employee_count} employees. "
                "Please reassign or delete employees first.",
                400
            )

        branch_name = branch.name
        db.delete(branch)
        db.commit()

        logger.info(f"Branch deleted: {branch_name} (ID: {branch_id})")

        return success_response(None, "Branch deleted successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting branch: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@branch_bp.route('/<branch_id>/employees', methods=['GET'])
@require_auth
def get_branch_employees(branch_id):
    """Get all employees in a branch"""
    db = get_db()

    try:
        branch = db.query(Branch).filter_by(id=branch_id).first()

        if not branch:
            return error_response("Branch not found", 404)

        # Check access
        if g.user_type == 'company_admin' and branch.company_id != g.company_id:
            return error_response("Access denied", 403)

        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))

        query = db.query(Employee).filter_by(
            branch_id=branch_id,
            status='active'
        )

        total = query.count()
        employees = query.offset((page - 1) * per_page).limit(per_page).all()

        return success_response({
            'branch': branch.to_dict(),
            'employees': [emp.to_dict() for emp in employees],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })

    except Exception as e:
        logger.error(f"Error getting branch employees: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()