from flask import request, jsonify, g
from database import get_db, Company, CompanyAdmin, CompanySettings
from functools import wraps


def load_company_context(f):
    """
    Decorator to load company context for authenticated requests
    Requires require_auth or require_company_admin to be applied first
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not hasattr(g, 'company_id') or not g.company_id:
            return jsonify({
                'success': False,
                'error': 'Company context not available'
            }), 403

        db = get_db()
        try:
            # Load company
            company = db.query(Company).filter_by(id=g.company_id).first()

            if not company:
                return jsonify({
                    'success': False,
                    'error': 'Company not found'
                }), 404

            # Check if company is active - YANGILANDI
            company_status = company.status if isinstance(company.status, str) else company.status.value

            if company_status != 'active':
                return jsonify({
                    'success': False,
                    'error': 'Company account is not active'
                }), 403

            # Store company in g object
            g.company = company

            return f(*args, **kwargs)

        finally:
            db.close()

    return decorated_function


def verify_company_access(company_id):
    """Verify that the authenticated user has access to the specified company"""
    if not hasattr(g, 'user_type'):
        return False

    # Super admins have access to all companies
    if g.user_type == 'superadmin':
        return True

    # Company admins can only access their own company
    if g.user_type == 'company_admin':
        return str(g.company_id) == str(company_id)

    return False


def check_employee_limit():
    """Check if company has reached employee limit"""
    if not hasattr(g, 'company_id') or not g.company_id:
        return False, "Company context not loaded"

    db = get_db()
    try:
        from database import Employee

        company = db.query(Company).filter_by(id=g.company_id).first()
        if not company:
            return False, "Company not found"

        # Count current employees
        current_count = db.query(Employee).filter_by(
            company_id=g.company_id,
            status='active'
        ).count()

        if current_count >= company.max_employees:
            return False, f"Employee limit reached. Maximum allowed: {company.max_employees}"

        return True, None
    finally:
        db.close()


def get_company_settings(company_id=None):
    """
    Get company settings from g object or database

    Args:
        company_id: Optional company ID. If not provided, uses g.company_id

    Returns:
        CompanySettings object or None
    """
    # If company_id provided, fetch directly from database
    if company_id:
        db = get_db()
        try:
            settings = db.query(CompanySettings).filter_by(company_id=company_id).first()
            return settings
        finally:
            db.close()

    # Otherwise, use g object (for authenticated requests)
    if hasattr(g, 'company_settings'):
        return g.company_settings

    if not hasattr(g, 'company_id') or not g.company_id:
        return None

    db = get_db()
    try:
        settings = db.query(CompanySettings).filter_by(company_id=g.company_id).first()

        if settings:
            g.company_settings = settings

        return settings
    finally:
        db.close()