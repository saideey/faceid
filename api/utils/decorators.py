from functools import wraps
from flask import request, jsonify, g
from services.auth_service import decode_jwt_token
from database import get_db, SuperAdmin, CompanyAdmin


def auth_required(f):
    """Decorator to require JWT authentication"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = None

        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]

        if not token:
            return jsonify({
                'success': False,
                'error': 'Authentication token is missing'
            }), 401

        # Decode token
        payload = decode_jwt_token(token)
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401

        # Store user info in g object
        g.user_id = payload.get('user_id')
        g.company_id = payload.get('company_id')
        g.role = payload.get('role')
        g.user_type = payload.get('user_type', 'company_admin')

        return f(*args, **kwargs)

    return decorated_function


def superadmin_required(f):
    """Decorator to require super admin authentication"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = None

        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]

        if not token:
            return jsonify({
                'success': False,
                'error': 'Authentication token is missing'
            }), 401

        # Decode token
        payload = decode_jwt_token(token)
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401

        # Check if user is super admin
        if payload.get('user_type') != 'superadmin':
            return jsonify({
                'success': False,
                'error': 'Super admin access required'
            }), 403

        # Verify super admin exists
        db = get_db()
        try:
            super_admin = db.query(SuperAdmin).filter_by(id=payload.get('user_id')).first()
            if not super_admin:
                return jsonify({
                    'success': False,
                    'error': 'Super admin not found'
                }), 403

            g.user_id = payload.get('user_id')
            g.user_type = 'superadmin'

            return f(*args, **kwargs)
        finally:
            db.close()

    return decorated_function


def company_admin_required(f):
    """Decorator to require company admin authentication"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = None

        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]

        if not token:
            return jsonify({
                'success': False,
                'error': 'Authentication token is missing'
            }), 401

        # Decode token
        payload = decode_jwt_token(token)
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401

        # Check if user is company admin
        if payload.get('user_type') != 'company_admin':
            return jsonify({
                'success': False,
                'error': 'Company admin access required'
            }), 403

        # Verify company admin exists
        db = get_db()
        try:
            company_admin = db.query(CompanyAdmin).filter_by(id=payload.get('user_id')).first()
            if not company_admin:
                return jsonify({
                    'success': False,
                    'error': 'Company admin not found'
                }), 403

            g.user_id = payload.get('user_id')
            g.company_id = payload.get('company_id')
            g.role = payload.get('role')
            g.user_type = 'company_admin'

            return f(*args, **kwargs)
        finally:
            db.close()

    return decorated_function