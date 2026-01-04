from flask import Blueprint, request, jsonify, g
from database import get_db, Company, CompanySettings
from utils.decorators import company_admin_required
from utils.helpers import success_response, error_response, save_uploaded_file, get_file_url
from utils.validators import validate_time_format, validate_required_fields
from config.settings import Config
import os

company_bp = Blueprint('company', __name__)


@company_bp.route('/profile', methods=['GET'])
@company_admin_required
def get_profile():
    """Get company profile"""
    try:
        db = get_db()

        company = db.query(Company).filter_by(id=g.company_id).first()

        if not company:
            db.close()
            return error_response("Company not found", 404)

        result = company.to_dict()

        # Include employee count
        from database import Employee
        from sqlalchemy import func
        employee_count = db.query(func.count(Employee.id)).filter_by(company_id=g.company_id).scalar()
        result['employee_count'] = employee_count

        db.close()

        return success_response(result)

    except Exception as e:
        return error_response(f"Failed to get profile: {str(e)}", 500)


@company_bp.route('/profile', methods=['PUT'])
@company_admin_required
def update_profile():
    """Update company profile"""
    try:
        data = request.get_json()

        db = get_db()

        company = db.query(Company).filter_by(id=g.company_id).first()

        if not company:
            db.close()
            return error_response("Company not found", 404)

        # Update fields
        if 'company_name' in data:
            company.company_name = data['company_name']

        from database import get_tashkent_time
        company.updated_at = get_tashkent_time()

        db.commit()
        db.refresh(company)

        result = company.to_dict()
        db.close()

        return success_response(result, "Profile updated successfully")

    except Exception as e:
        return error_response(f"Failed to update profile: {str(e)}", 500)


@company_bp.route('/logo', methods=['POST'])
@company_admin_required
def upload_logo():
    """Upload company logo"""
    try:
        if 'logo' not in request.files:
            return error_response("No logo file provided", 400)

        file = request.files['logo']

        if file.filename == '':
            return error_response("No file selected", 400)

        # Save file
        filename = save_uploaded_file(file, Config.LOGO_FOLDER, Config.ALLOWED_EXTENSIONS)

        if not filename:
            return error_response("Invalid file type. Allowed: png, jpg, jpeg, gif", 400)

        # Update company logo_url
        db = get_db()

        company = db.query(Company).filter_by(id=g.company_id).first()

        if not company:
            db.close()
            return error_response("Company not found", 404)

        # Delete old logo if exists
        if company.logo_url:
            old_file_path = os.path.join(Config.LOGO_FOLDER, company.logo_url)
            if os.path.exists(old_file_path):
                os.remove(old_file_path)

        company.logo_url = filename

        from database import get_tashkent_time
        company.updated_at = get_tashkent_time()

        db.commit()

        logo_url = get_file_url(filename, 'logos')

        db.close()

        return success_response({
            'logo_url': logo_url,
            'filename': filename
        }, "Logo uploaded successfully")

    except Exception as e:
        return error_response(f"Failed to upload logo: {str(e)}", 500)


@company_bp.route('/settings', methods=['GET'])
@company_admin_required
def get_settings():
    """Get company settings"""
    try:
        db = get_db()

        settings = db.query(CompanySettings).filter_by(company_id=g.company_id).first()

        if not settings:
            # Create default settings
            settings = CompanySettings(company_id=g.company_id)
            db.add(settings)
            db.commit()
            db.refresh(settings)

        result = settings.to_dict()
        db.close()

        return success_response(result)

    except Exception as e:
        return error_response(f"Failed to get settings: {str(e)}", 500)


@company_bp.route('/settings', methods=['PUT'])
@company_admin_required
def update_settings():
    """Update company settings"""
    try:
        data = request.get_json()

        db = get_db()

        settings = db.query(CompanySettings).filter_by(company_id=g.company_id).first()

        if not settings:
            settings = CompanySettings(company_id=g.company_id)
            db.add(settings)
            db.flush()

        # Update fields
        if 'default_work_start' in data:
            from utils.helpers import parse_time
            work_start = parse_time(data['default_work_start'])
            if work_start:
                settings.default_work_start = work_start

        if 'default_work_end' in data:
            from utils.helpers import parse_time
            work_end = parse_time(data['default_work_end'])
            if work_end:
                settings.default_work_end = work_end

        if 'penalty_per_minute' in data:
            settings.penalty_per_minute = data['penalty_per_minute']

        if 'grace_period_minutes' in data:
            settings.grace_period_minutes = data['grace_period_minutes']

        if 'currency' in data:
            settings.currency = data['currency']

        from database import get_tashkent_time
        settings.updated_at = get_tashkent_time()

        db.commit()
        db.refresh(settings)

        result = settings.to_dict()
        db.close()

        return success_response(result, "Settings updated successfully")

    except Exception as e:
        return error_response(f"Failed to update settings: {str(e)}", 500)