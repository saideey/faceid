from .auth import auth_bp
from .superadmin import superadmin_bp
from .company import company_bp
from .department import department_bp
from .employee import employee_bp
from .terminal import terminal_bp
from .attendance import attendance_bp
from .reports import reports_bp

__all__ = [
    'auth_bp',
    'superadmin_bp',
    'company_bp',
    'department_bp',
    'employee_bp',
    'terminal_bp',
    'attendance_bp',
    'reports_bp'
]