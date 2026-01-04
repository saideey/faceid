import re
from datetime import datetime, time, date


def validate_email(email):
    """Validate email format"""
    if not email:
        return False

    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def validate_time_format(time_str):
    """Validate time format HH:MM:SS"""
    if not time_str:
        return False

    try:
        datetime.strptime(time_str, '%H:%M:%S')
        return True
    except ValueError:
        return False


def validate_date_format(date_str):
    """Validate date format YYYY-MM-DD"""
    if not date_str:
        return False

    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False


def validate_date_range(start_date, end_date):
    """Validate that start_date is before or equal to end_date"""
    try:
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()

        return start_date <= end_date
    except (ValueError, AttributeError):
        return False


def validate_password(password):
    """Validate password strength (min 6 characters)"""
    if not password:
        return False
    return len(password) >= 6


def validate_phone(phone):
    """Validate phone number format"""
    if not phone:
        return True  # Phone is optional

    # Remove spaces and dashes
    phone_clean = re.sub(r'[\s\-\(\)]', '', phone)

    # Check if it contains only digits and plus sign
    pattern = r'^\+?[0-9]{9,15}$'
    return re.match(pattern, phone_clean) is not None


def validate_required_fields(data, required_fields):
    """Check if all required fields are present in data"""
    missing_fields = []
    for field in required_fields:
        if field not in data or data[field] is None or data[field] == '':
            missing_fields.append(field)

    return missing_fields


def validate_file_extension(filename, allowed_extensions):
    """Validate file extension"""
    if not filename:
        return False

    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in allowed_extensions