from flask import request, jsonify, g
from functools import wraps
import jwt
import os
from database import get_db, CompanyAdmin, SuperAdmin

JWT_SECRET = os.getenv('JWT_SECRET', 'your-secret-key-change-in-production')


def verify_token(token):
    """
    Verify JWT token and return payload
    Returns: (payload, error_message)
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload, None
    except jwt.ExpiredSignatureError:
        return None, 'Token has expired'
    except jwt.InvalidTokenError:
        return None, 'Invalid token'
    except Exception as e:
        return None, f'Token verification failed: {str(e)}'


def require_auth(f):
    """
    Decorator to require authentication (both super admin and company admin)
    Sets g.user_id, g.user_type, and g.company_id
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')

        if not auth_header:
            return jsonify({
                'success': False,
                'error': 'No authorization token provided'
            }), 401

        try:
            # Extract token (format: "Bearer <token>")
            parts = auth_header.split()
            if len(parts) != 2 or parts[0].lower() != 'bearer':
                return jsonify({
                    'success': False,
                    'error': 'Invalid authorization header format'
                }), 401

            token = parts[1]

            # Verify token
            payload, error = verify_token(token)
            if error:
                return jsonify({
                    'success': False,
                    'error': error
                }), 401

            # Set user info in g object
            g.user_id = payload.get('user_id')
            g.user_type = payload.get('user_type')
            g.company_id = payload.get('company_id')

            if not g.user_id or not g.user_type:
                return jsonify({
                    'success': False,
                    'error': 'Invalid token payload'
                }), 401

            return f(*args, **kwargs)

        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Authentication failed: {str(e)}'
            }), 401

    return decorated_function


def require_super_admin(f):
    """
    Decorator to require super admin authentication
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')

        if not auth_header:
            return jsonify({
                'success': False,
                'error': 'No authorization token provided'
            }), 401

        try:
            # Extract token
            parts = auth_header.split()
            if len(parts) != 2 or parts[0].lower() != 'bearer':
                return jsonify({
                    'success': False,
                    'error': 'Invalid authorization header format'
                }), 401

            token = parts[1]

            # Verify token
            payload, error = verify_token(token)
            if error:
                return jsonify({
                    'success': False,
                    'error': error
                }), 401

            # Check if super admin
            if payload.get('user_type') != 'superadmin':
                return jsonify({
                    'success': False,
                    'error': 'Super admin access required'
                }), 403

            # Set user info in g object
            g.user_id = payload.get('user_id')
            g.user_type = payload.get('user_type')

            return f(*args, **kwargs)

        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Authentication failed: {str(e)}'
            }), 401

    return decorated_function


def require_company_admin(f):
    """
    Decorator to require company admin authentication
    Sets g.user_id, g.user_type, and g.company_id
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')

        if not auth_header:
            return jsonify({
                'success': False,
                'error': 'No authorization token provided'
            }), 401

        try:
            # Extract token
            parts = auth_header.split()
            if len(parts) != 2 or parts[0].lower() != 'bearer':
                return jsonify({
                    'success': False,
                    'error': 'Invalid authorization header format'
                }), 401

            token = parts[1]

            # Verify token
            payload, error = verify_token(token)
            if error:
                return jsonify({
                    'success': False,
                    'error': error
                }), 401

            # Check if company admin
            if payload.get('user_type') != 'company_admin':
                return jsonify({
                    'success': False,
                    'error': 'Company admin access required'
                }), 403

            # Set user info in g object
            g.user_id = payload.get('user_id')
            g.user_type = payload.get('user_type')
            g.company_id = payload.get('company_id')

            if not g.company_id:
                return jsonify({
                    'success': False,
                    'error': 'Company ID not found in token'
                }), 401

            return f(*args, **kwargs)

        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Authentication failed: {str(e)}'
            }), 401

    return decorated_function


def optional_auth(f):
    """
    Decorator for optional authentication
    Sets g.user_id, g.user_type, and g.company_id if token is present
    Does not fail if token is missing
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')

        # Set defaults
        g.user_id = None
        g.user_type = None
        g.company_id = None

        if not auth_header:
            return f(*args, **kwargs)

        try:
            # Extract token
            parts = auth_header.split()
            if len(parts) == 2 and parts[0].lower() == 'bearer':
                token = parts[1]

                # Verify token
                payload, error = verify_token(token)
                if not error:
                    # Set user info in g object
                    g.user_id = payload.get('user_id')
                    g.user_type = payload.get('user_type')
                    g.company_id = payload.get('company_id')

        except:
            # Ignore token errors for optional auth
            pass

        return f(*args, **kwargs)

    return decorated_function


# Backward compatibility aliases
company_admin_required = require_company_admin
super_admin_required = require_super_admin