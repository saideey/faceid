from flask import Flask, jsonify, render_template, send_from_directory
from flask_cors import CORS
import logging
from logging.handlers import RotatingFileHandler
import os

from config.settings import Config
from database import init_db

# Import blueprints
from routes.auth import auth_bp
from routes.superadmin import superadmin_bp
from routes.company import company_bp
from routes.department import department_bp
from routes.employee import employee_bp
from routes.terminal import terminal_bp
from routes.attendance import attendance_bp
from routes.reports import reports_bp
from routes.branch import branch_bp
from routes.employee_schedule import schedule_bp
from routes.penalty import penalty_bp
from routes.bonus import bonus_bp
from routes.salary import salary_bp
from routes.settings import settings_bp
from routes.export import export_bp


def create_initial_superadmin(app):
    """Create initial super admin from environment variables"""
    from database import get_db, SuperAdmin
    from services.auth_service import hash_password

    email = os.getenv('SUPER_ADMIN_EMAIL', 'admin@davomat.uz')
    password = os.getenv('SUPER_ADMIN_PASSWORD', 'admin123')

    db = get_db()
    try:
        existing = db.query(SuperAdmin).filter_by(email=email).first()
        if existing:
            app.logger.info(f"Super admin {email} already exists")
            return

        hashed_password = hash_password(password)
        super_admin = SuperAdmin(
            email=email,
            password_hash=hashed_password,
            full_name="System Administrator"
        )

        db.add(super_admin)
        db.commit()
        app.logger.info(f"Super admin {email} created successfully")
    except Exception as e:
        app.logger.error(f"Failed to create super admin: {str(e)}")
        db.rollback()
    finally:
        db.close()


def setup_logging(app):
    """Setup application logging"""
    if not app.debug:
        log_folder = getattr(Config, 'LOG_FOLDER', 'logs')
        if not os.path.exists(log_folder):
            os.makedirs(log_folder)

        file_handler = RotatingFileHandler(
            os.path.join(log_folder, 'app.log'),
            maxBytes=10240000,
            backupCount=10
        )

        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))

        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.INFO)
        app.logger.info('Davomat Tizimi startup')
    else:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s'
        ))
        app.logger.addHandler(console_handler)
        app.logger.setLevel(logging.DEBUG)


def create_app():
    """Create and configure Flask application"""
    app = Flask(__name__)
    app.config.from_object(Config)

    CORS(app, resources={
        r"/api/*": {
            "origins": "*",
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"]
        },
        '/api/export/*': {'origins': '*'},
    })

    setup_logging(app)

    with app.app_context():
        try:
            init_db()
            app.logger.info("Database initialized successfully")
            create_initial_superadmin(app)
        except Exception as e:
            app.logger.error(f"Database initialization failed: {str(e)}")

    # Register API blueprints
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(superadmin_bp, url_prefix='/api/superadmin')
    app.register_blueprint(company_bp, url_prefix='/api/company')
    app.register_blueprint(department_bp, url_prefix='/api/departments')
    app.register_blueprint(employee_bp, url_prefix='/api/employees')
    app.register_blueprint(terminal_bp, url_prefix='/api/terminal')
    app.register_blueprint(attendance_bp, url_prefix='/api/attendance')
    app.register_blueprint(reports_bp, url_prefix='/api/reports')
    app.register_blueprint(branch_bp, url_prefix='/api/branches')
    app.register_blueprint(schedule_bp, url_prefix='/api/employees')
    app.register_blueprint(penalty_bp, url_prefix='/api/penalties')
    app.register_blueprint(bonus_bp, url_prefix='/api/bonuses')
    app.register_blueprint(salary_bp, url_prefix='/api/salary')
    app.register_blueprint(settings_bp, url_prefix='/api/settings')
    app.register_blueprint(export_bp, url_prefix='/api/export')

    # Frontend Routes
    @app.route('/')
    def index():
        return render_template('login.html')

    @app.route('/login')
    def login_page():
        return render_template('login.html')

    @app.route('/dashboard')
    def dashboard_page():
        return render_template('dashboard.html')

    @app.route('/employees')
    def employees_page():
        return render_template('employees.html')

    @app.route('/schedule')
    def schedule_page():
        return render_template('schedule.html')

    @app.route('/salary')
    def salary_page():
        return render_template('salary.html')

    @app.route('/penalties')
    def penalties_page():
        return render_template('penalties.html')

    @app.route('/bonuses')
    def bonuses_page():
        return render_template('bonuses.html')

    @app.route('/attendance')
    def attendance_page():
        return render_template('attendance.html')

    @app.route('/reports')
    def reports_page():
        return render_template('reports.html')

    @app.route('/settings')
    def settings_page():
        return render_template('settings.html')

    @app.route('/branches')
    def branches_page():
        return render_template('branches.html')

    @app.route('/departments')
    def departments_page():
        return render_template('departments.html')

    @app.route('/test')
    def test_page():
        return render_template('test.html')

    @app.route('/api-debug')
    def api_debug_page():
        return render_template('api_debug.html')

    @app.route('/static/<path:filename>')
    def static_files(filename):
        return send_from_directory('static', filename)

    @app.route('/health', methods=['GET'])
    def health_check():
        return jsonify({'status': 'healthy', 'service': 'Davomat Tizimi', 'version': '1.0.0'}), 200

    @app.after_request
    def after_request(response):
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
        return response

    return app


# ========================================================
# GUNICORN UCHUN ASOSIY QISM:
# 'app' obyekti global darajada yaratilishi shart.
# ========================================================
app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV', 'development') == 'development'

    print(f"🚀 Server: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=debug)