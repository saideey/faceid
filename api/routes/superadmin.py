from flask import Blueprint, request, jsonify, g
from database import get_db, SuperAdmin, Company, CompanyAdmin, CompanySettings, Branch
from services.auth_service import hash_password, verify_password, generate_jwt_token
from middleware.auth_middleware import require_super_admin
from utils.helpers import success_response, error_response
from sqlalchemy import func
import datetime as dt
import logging

superadmin_bp = Blueprint('superadmin', __name__)
logger = logging.getLogger(__name__)


@superadmin_bp.route('/login', methods=['POST'])
def login():
    """Super admin login"""
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ['email', 'password']
        missing_fields = [field for field in required_fields if not data.get(field)]

        if missing_fields:
            return error_response(f"Missing required fields: {', '.join(missing_fields)}", 400)

        email = data.get('email')
        password = data.get('password')

        db = get_db()

        # Find super admin
        super_admin = db.query(SuperAdmin).filter_by(email=email).first()

        if not super_admin:
            db.close()
            return error_response("Invalid email or password", 401)

        # Verify password
        if not verify_password(password, super_admin.password_hash):
            db.close()
            return error_response("Invalid email or password", 401)

        # Generate JWT token
        token = generate_jwt_token(
            user_id=super_admin.id,
            user_type='superadmin'
        )

        result = {
            'token': token,
            'user': super_admin.to_dict()
        }

        db.close()

        return success_response(result, "Login successful")

    except Exception as e:
        logger.error(f"Login error: {str(e)}", exc_info=True)
        return error_response(f"Login failed: {str(e)}", 500)


@superadmin_bp.route('/companies', methods=['POST'])
@require_super_admin
def create_company():
    """Create new company with admin"""
    db = get_db()

    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ['company_name', 'subdomain', 'admin_email', 'admin_password', 'admin_full_name']
        missing_fields = [field for field in required_fields if not data.get(field)]

        if missing_fields:
            return error_response(f"Missing required fields: {', '.join(missing_fields)}", 400)

        # Check if subdomain already exists
        existing_company = db.query(Company).filter_by(subdomain=data['subdomain']).first()
        if existing_company:
            return error_response(f"Subdomain '{data['subdomain']}' already exists", 400)

        # Check if admin email already exists
        existing_admin = db.query(CompanyAdmin).filter_by(email=data['admin_email']).first()
        if existing_admin:
            return error_response(f"Admin email '{data['admin_email']}' already exists", 400)

        # Create company
        company = Company(
            company_name=data['company_name'],
            subdomain=data['subdomain'],
            max_employees=data.get('max_employees', 100),
            status='active'
        )

        db.add(company)
        db.flush()  # Get company ID

        # Create company admin
        company_admin = CompanyAdmin(
            company_id=company.id,
            email=data['admin_email'],
            password_hash=hash_password(data['admin_password']),
            full_name=data['admin_full_name']
        )

        db.add(company_admin)

        # Create default company settings
        company_settings = CompanySettings(
            company_id=company.id,
            default_work_start=dt.time(9, 0),
            default_work_end=dt.time(18, 0),
            grace_period_minutes=15,
            penalty_per_minute=0.0,
            currency='UZS'
        )

        db.add(company_settings)

        # Create default branch
        default_branch = Branch(
            company_id=company.id,
            name='Asosiy filial',
            code='MAIN',
            status='active'
        )

        db.add(default_branch)

        db.commit()
        db.refresh(company)

        logger.info(f"Company created: {company.company_name} (ID: {company.id})")

        result = company.to_dict()
        result['admin'] = {
            'email': company_admin.email,
            'full_name': company_admin.full_name
        }
        result['default_branch'] = {
            'id': default_branch.id,
            'name': default_branch.name,
            'code': default_branch.code
        }

        return success_response(result, "Company created successfully", 201)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating company: {str(e)}", exc_info=True)
        return error_response(f"Failed to create company: {str(e)}", 500)
    finally:
        db.close()


@superadmin_bp.route('/companies', methods=['GET'])
@require_super_admin
def list_companies():
    """List all companies"""
    db = get_db()

    try:
        # Get query parameters
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))
        status = request.args.get('status')
        search = request.args.get('search')

        # Build query
        query = db.query(Company)

        if status:
            query = query.filter_by(status=status)

        if search:
            query = query.filter(Company.company_name.ilike(f'%{search}%'))

        # Get total count
        total = query.count()

        # Paginate
        companies = query.order_by(Company.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

        # Get employee counts for each company
        from database import Employee
        result = []
        for company in companies:
            company_dict = company.to_dict()
            employee_count = db.query(func.count(Employee.id)).filter_by(company_id=company.id).scalar()
            company_dict['employee_count'] = employee_count
            result.append(company_dict)

        return success_response({
            'companies': result,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })

    except Exception as e:
        logger.error(f"Error listing companies: {str(e)}", exc_info=True)
        return error_response(f"Failed to list companies: {str(e)}", 500)
    finally:
        db.close()


@superadmin_bp.route('/companies/<company_id>', methods=['GET'])
@require_super_admin
def get_company(company_id):
    """Get company details"""
    db = get_db()

    try:
        company = db.query(Company).filter_by(id=company_id).first()

        if not company:
            return error_response("Company not found", 404)

        # Get statistics
        from database import Employee
        employee_count = db.query(func.count(Employee.id)).filter_by(company_id=company_id).scalar()
        active_employees = db.query(func.count(Employee.id)).filter_by(company_id=company_id, status='active').scalar()

        result = company.to_dict()
        result['employee_count'] = employee_count
        result['active_employees'] = active_employees

        # Get company settings
        if company.settings:
            result['settings'] = company.settings.to_dict()

        return success_response(result)

    except Exception as e:
        logger.error(f"Error getting company: {str(e)}", exc_info=True)
        return error_response(f"Failed to get company: {str(e)}", 500)
    finally:
        db.close()


@superadmin_bp.route('/companies/<company_id>', methods=['PUT'])
@require_super_admin
def update_company(company_id):
    """Update company details"""
    db = get_db()

    try:
        data = request.get_json()

        company = db.query(Company).filter_by(id=company_id).first()

        if not company:
            return error_response("Company not found", 404)

        # Update fields
        if 'company_name' in data:
            company.company_name = data['company_name']

        if 'subdomain' in data:
            # Check if subdomain is unique
            existing = db.query(Company).filter(
                Company.subdomain == data['subdomain'],
                Company.id != company_id
            ).first()
            if existing:
                return error_response("Subdomain already exists", 400)
            company.subdomain = data['subdomain']

        if 'status' in data:
            company.status = data['status']

        if 'max_employees' in data:
            company.max_employees = data['max_employees']

        db.commit()
        db.refresh(company)

        result = company.to_dict()

        logger.info(f"Company updated: {company.company_name} (ID: {company.id})")

        return success_response(result, "Company updated successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating company: {str(e)}", exc_info=True)
        return error_response(f"Failed to update company: {str(e)}", 500)
    finally:
        db.close()


@superadmin_bp.route('/companies/<company_id>', methods=['DELETE'])
@require_super_admin
def delete_company(company_id):
    """Delete company"""
    db = get_db()

    try:
        company = db.query(Company).filter_by(id=company_id).first()

        if not company:
            return error_response("Company not found", 404)

        company_name = company.company_name

        # Delete company (cascade will delete all related data)
        db.delete(company)
        db.commit()

        logger.info(f"Company deleted: {company_name} (ID: {company_id})")

        return success_response(None, f"Company '{company_name}' deleted successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting company: {str(e)}", exc_info=True)
        return error_response(f"Failed to delete company: {str(e)}", 500)
    finally:
        db.close()


@superadmin_bp.route('/create-superadmin', methods=['POST'])
def create_superadmin():
    """Create initial super admin (should be protected in production)"""
    db = get_db()

    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ['email', 'password', 'full_name']
        missing_fields = [field for field in required_fields if not data.get(field)]

        if missing_fields:
            return error_response(f"Missing required fields: {', '.join(missing_fields)}", 400)

        email = data.get('email')
        password = data.get('password')
        full_name = data.get('full_name')

        # Check if email exists
        existing = db.query(SuperAdmin).filter_by(email=email).first()
        if existing:
            return error_response("Email already registered", 400)

        # Create super admin
        super_admin = SuperAdmin(
            email=email,
            password_hash=hash_password(password),
            full_name=full_name
        )

        db.add(super_admin)
        db.commit()
        db.refresh(super_admin)

        result = super_admin.to_dict()

        logger.info(f"Super admin created: {email}")

        return success_response(result, "Super admin created successfully", 201)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating super admin: {str(e)}", exc_info=True)
        return error_response(f"Failed to create super admin: {str(e)}", 500)
    finally:
        db.close()