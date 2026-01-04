from flask import Blueprint, request, jsonify, g
from database import get_db, CompanyAdmin, Company, CompanySettings
from services.auth_service import hash_password, verify_password, generate_jwt_token
from utils.helpers import success_response, error_response
import logging

auth_bp = Blueprint('auth', __name__)
logger = logging.getLogger(__name__)


@auth_bp.route('/login', methods=['POST'])
def login():
    """Company admin login"""
    db = get_db()

    try:
        data = request.get_json()

        # Validate required fields
        if not data.get('email') or not data.get('password'):
            return error_response("Email and password are required", 400)

        email = data.get('email')
        password = data.get('password')

        # Find company admin by email
        company_admin = db.query(CompanyAdmin).filter_by(email=email).first()

        if not company_admin:
            return error_response("Invalid email or password", 401)

        # Verify password
        if not verify_password(password, company_admin.password_hash):
            return error_response("Invalid email or password", 401)

        # Check if company is active
        company = db.query(Company).filter_by(id=company_admin.company_id).first()

        if not company:
            return error_response("Company not found", 404)

        # YANGILANDI - to'g'ridan-to'g'ri string
        company_status = company.status if isinstance(company.status, str) else str(company.status)

        if company_status != 'active':
            return error_response("Company account is not active", 403)

        # Generate JWT token - role field'siz
        token = generate_jwt_token(
            user_id=company_admin.id,
            user_type='company_admin',
            company_id=company_admin.company_id
        )

        logger.info(f"Company admin login successful: {email}")

        return success_response({
            'token': token,
            'user': company_admin.to_dict(),
            'company': company.to_dict()
        }, "Login successful")

    except Exception as e:
        logger.error(f"Login error: {str(e)}", exc_info=True)
        return error_response(f"Login failed: {str(e)}", 500)
    finally:
        db.close()


@auth_bp.route('/register', methods=['POST'])
def register():
    """Register new company with admin"""
    db = get_db()

    try:
        data = request.get_json()

        # Validate required fields
        required = ['email', 'password', 'full_name', 'company_name', 'subdomain']
        missing = [f for f in required if not data.get(f)]

        if missing:
            return error_response(f"Missing required fields: {', '.join(missing)}", 400)

        email = data.get('email')
        password = data.get('password')
        full_name = data.get('full_name')
        company_name = data.get('company_name')
        subdomain = data.get('subdomain')

        # Check if email already exists
        existing_admin = db.query(CompanyAdmin).filter_by(email=email).first()
        if existing_admin:
            return error_response("Email already registered", 400)

        # Check if subdomain already exists
        existing_company = db.query(Company).filter_by(subdomain=subdomain).first()
        if existing_company:
            return error_response("Subdomain already exists", 400)

        # Create company
        company = Company(
            company_name=company_name,
            subdomain=subdomain,
            max_employees=data.get('max_employees', 100),
            status='active'
        )
        db.add(company)
        db.flush()

        # Create company settings
        settings = CompanySettings(
            company_id=company.id
        )
        db.add(settings)

        # Create company admin
        company_admin = CompanyAdmin(
            company_id=company.id,
            email=email,
            password_hash=hash_password(password),
            full_name=full_name
        )
        db.add(company_admin)

        db.commit()
        db.refresh(company_admin)
        db.refresh(company)

        # Generate JWT token
        token = generate_jwt_token(
            user_id=company_admin.id,
            user_type='company_admin',
            company_id=company_admin.company_id
        )

        logger.info(f"Company registered: {company_name} ({email})")

        return success_response({
            'token': token,
            'user': company_admin.to_dict(),
            'company': company.to_dict()
        }, "Registration successful", 201)

    except Exception as e:
        db.rollback()
        logger.error(f"Registration error: {str(e)}", exc_info=True)
        return error_response(f"Registration failed: {str(e)}", 500)
    finally:
        db.close()


@auth_bp.route('/change-password', methods=['POST'])
def change_password():
    """Change password (requires authentication)"""
    from middleware.auth_middleware import require_company_admin

    @require_company_admin
    def _change_password():
        db = get_db()

        try:
            data = request.get_json()

            if not data.get('current_password') or not data.get('new_password'):
                return error_response("Current password and new password are required", 400)

            current_password = data.get('current_password')
            new_password = data.get('new_password')

            # Get current user
            company_admin = db.query(CompanyAdmin).filter_by(id=g.user_id).first()

            if not company_admin:
                return error_response("User not found", 404)

            # Verify current password
            if not verify_password(current_password, company_admin.password_hash):
                return error_response("Current password is incorrect", 400)

            # Update password
            company_admin.password_hash = hash_password(new_password)
            db.commit()

            logger.info(f"Password changed: {company_admin.email}")

            return success_response(None, "Password changed successfully")

        except Exception as e:
            db.rollback()
            logger.error(f"Change password error: {str(e)}", exc_info=True)
            return error_response(f"Failed to change password: {str(e)}", 500)
        finally:
            db.close()

    return _change_password()