from flask import Blueprint, request, g, send_from_directory
from database import get_db, CompanySettings, Company
from utils.decorators import company_admin_required
from utils.helpers import success_response, error_response
from werkzeug.utils import secure_filename
import os
import uuid
import logging

settings_bp = Blueprint('settings', __name__)
logger = logging.getLogger(__name__)

UPLOAD_FOLDER = '/app/static/uploads/logos'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'svg'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@settings_bp.route('/', methods=['GET'])
@company_admin_required
def get_settings():
    """Get company settings"""
    try:
        db = get_db()

        company = db.query(Company).filter_by(id=g.company_id).first()
        if not company:
            db.close()
            return error_response("Company not found", 404)

        settings = db.query(CompanySettings).filter_by(company_id=g.company_id).first()

        if not settings:
            # Create default settings
            settings = CompanySettings(
                company_id=g.company_id,
                late_threshold_minutes=10,
                overtime_threshold_minutes=30,
                auto_penalty_enabled=False,
                late_penalty_per_minute=1000,
                absence_penalty_amount=50000
            )
            db.add(settings)
            db.commit()
            db.refresh(settings)

        result = {
            # Company info (safe access with getattr)
            'company_id': company.id,
            'company_name': company.company_name,
            'email': getattr(company, 'email', None),
            'phone': getattr(company, 'phone', None),
            'address': getattr(company, 'address', None),
            'website': getattr(company, 'website', None),
            'logo_url': getattr(company, 'logo_url', None),
            'max_employees': company.max_employees,

            # Thresholds
            'late_threshold_minutes': settings.late_threshold_minutes,
            'overtime_threshold_minutes': settings.overtime_threshold_minutes,

            # Penalties
            'auto_penalty_enabled': settings.auto_penalty_enabled,
            'late_penalty_per_minute': settings.late_penalty_per_minute,
            'absence_penalty_amount': settings.absence_penalty_amount
        }

        logger.info(f"📋 Settings loaded for company {g.company_id}")
        logger.info(f"   Company: {company.company_name}")
        logger.info(f"   Logo: {company.logo_url}")

        db.close()
        return success_response(result)

    except Exception as e:
        logger.error(f"Error getting settings: {e}", exc_info=True)
        return error_response(str(e), 500)


@settings_bp.route('/', methods=['PUT'])
@company_admin_required
def update_settings():
    """Update company settings"""
    try:
        data = request.get_json()
        db = get_db()

        # Update company info
        company = db.query(Company).filter_by(id=g.company_id).first()
        if not company:
            db.close()
            return error_response("Company not found", 404)

        if 'company_name' in data:
            company.company_name = data['company_name']
        if 'email' in data and hasattr(company, 'email'):
            company.email = data['email']
        if 'phone' in data and hasattr(company, 'phone'):
            company.phone = data['phone']
        if 'address' in data and hasattr(company, 'address'):
            company.address = data['address']
        if 'website' in data and hasattr(company, 'website'):
            company.website = data['website']

        # Update settings
        settings = db.query(CompanySettings).filter_by(company_id=g.company_id).first()
        if not settings:
            settings = CompanySettings(company_id=g.company_id)
            db.add(settings)

        if 'late_threshold_minutes' in data:
            settings.late_threshold_minutes = int(data['late_threshold_minutes'])
        if 'overtime_threshold_minutes' in data:
            settings.overtime_threshold_minutes = int(data['overtime_threshold_minutes'])
        if 'auto_penalty_enabled' in data:
            settings.auto_penalty_enabled = data['auto_penalty_enabled']
        if 'late_penalty_per_minute' in data:
            settings.late_penalty_per_minute = float(data['late_penalty_per_minute'])
        if 'absence_penalty_amount' in data:
            settings.absence_penalty_amount = float(data['absence_penalty_amount'])

        db.commit()

        logger.info(f"Settings updated for company {g.company_id}")

        db.close()
        return success_response({'message': 'Settings updated successfully'})

    except Exception as e:
        logger.error(f"Error updating settings: {e}", exc_info=True)
        return error_response(str(e), 500)


@settings_bp.route('/logo', methods=['POST'])
@company_admin_required
def upload_logo():
    """Upload company logo"""
    try:
        if 'logo' not in request.files:
            return error_response("No logo file provided", 400)

        file = request.files['logo']

        if file.filename == '':
            return error_response("No file selected", 400)

        if not allowed_file(file.filename):
            return error_response("Invalid file type. Allowed: png, jpg, jpeg, gif, svg", 400)

        # Create upload folder if not exists
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)

        # Generate unique filename
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{g.company_id}_{uuid.uuid4().hex[:8]}.{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)

        # Save file
        file.save(filepath)

        # Update company logo_url
        db = get_db()
        company = db.query(Company).filter_by(id=g.company_id).first()

        if company:
            # Delete old logo if exists
            if company.logo_url:
                old_path = company.logo_url.replace('/static/', '/app/static/')
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except:
                        pass

            company.logo_url = f"/static/uploads/logos/{filename}"
            db.commit()

            logger.info(f"Logo uploaded for company {g.company_id}: {filename}")

            result = {'logo_url': company.logo_url}
            db.close()
            return success_response(result, "Logo uploaded successfully")

        db.close()
        return error_response("Company not found", 404)

    except Exception as e:
        logger.error(f"Error uploading logo: {e}", exc_info=True)
        return error_response(str(e), 500)


@settings_bp.route('/logo', methods=['DELETE'])
@company_admin_required
def delete_logo():
    """Delete company logo"""
    try:
        db = get_db()
        company = db.query(Company).filter_by(id=g.company_id).first()

        if not company:
            db.close()
            return error_response("Company not found", 404)

        if company.logo_url:
            # Delete file
            filepath = company.logo_url.replace('/static/', '/app/static/')
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception as e:
                    logger.warning(f"Could not delete logo file: {e}")

            company.logo_url = None
            db.commit()

            logger.info(f"Logo deleted for company {g.company_id}")

        db.close()
        return success_response({'message': 'Logo deleted successfully'})

    except Exception as e:
        logger.error(f"Error deleting logo: {e}", exc_info=True)
        return error_response(str(e), 500)