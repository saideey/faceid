from datetime import datetime, time
import pytz
import os
from werkzeug.utils import secure_filename
import uuid


def get_tashkent_time():
    """Get current time in Asia/Tashkent timezone"""
    tz = pytz.timezone('Asia/Tashkent')
    return datetime.now(tz)


def format_datetime(dt, format_str='%Y-%m-%d %H:%M:%S'):
    """Format datetime to string"""
    if not dt:
        return None

    if isinstance(dt, str):
        return dt

    return dt.strftime(format_str)


def parse_datetime(dt_str, timezone='Asia/Tashkent'):
    """Parse datetime string to timezone-aware datetime"""
    if not dt_str:
        return None

    try:
        # Handle ISO format with timezone
        if 'T' in dt_str:
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')

        # Convert to target timezone
        tz = pytz.timezone(timezone)
        if dt.tzinfo is None:
            dt = tz.localize(dt)
        else:
            dt = dt.astimezone(tz)

        return dt
    except (ValueError, AttributeError):
        return None


def parse_date(date_str):
    """Parse date string to date object"""
    if not date_str:
        return None

    try:
        if isinstance(date_str, str):
            return datetime.strptime(date_str, '%Y-%m-%d').date()
        return date_str
    except ValueError:
        return None


def parse_time(time_str):
    """Parse time string to time object"""
    if not time_str:
        return None

    try:
        if isinstance(time_str, str):
            return datetime.strptime(time_str, '%H:%M:%S').time()
        return time_str
    except ValueError:
        return None


def save_uploaded_file(file, folder, allowed_extensions=None):
    """Save uploaded file and return the file path"""
    if not file or file.filename == '':
        return None

    # Validate extension if provided
    if allowed_extensions:
        if '.' not in file.filename:
            return None
        ext = file.filename.rsplit('.', 1)[1].lower()
        if ext not in allowed_extensions:
            return None

    # Generate unique filename
    filename = secure_filename(file.filename)
    name, ext = os.path.splitext(filename)
    unique_filename = f"{name}_{uuid.uuid4().hex[:8]}{ext}"

    # Ensure folder exists
    os.makedirs(folder, exist_ok=True)

    # Save file
    file_path = os.path.join(folder, unique_filename)
    file.save(file_path)

    # Return relative path
    return unique_filename


def delete_file(file_path):
    """Delete a file if it exists"""
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            return True
    except Exception:
        pass
    return False


def get_file_url(filename, folder_type='logos'):
    """Generate file URL"""
    from config.settings import Config
    if not filename:
        return None
    return f"{Config.BASE_URL}/uploads/{folder_type}/{filename}"


def calculate_time_difference_minutes(start_time, end_time):
    """Calculate difference between two times in minutes"""
    if not start_time or not end_time:
        return 0

    # Convert to datetime for calculation
    base_date = datetime(2000, 1, 1)

    if isinstance(start_time, time):
        start_dt = datetime.combine(base_date, start_time)
    else:
        start_dt = start_time

    if isinstance(end_time, time):
        end_dt = datetime.combine(base_date, end_time)
    else:
        end_dt = end_time

    # Handle case where end time is on next day
    if end_dt < start_dt:
        end_dt = datetime.combine(base_date.replace(day=2), end_dt.time() if isinstance(end_time, time) else end_time)

    diff = end_dt - start_dt
    return int(diff.total_seconds() / 60)


def success_response(data=None, message=None, status_code=200):
    """Generate success response"""
    response = {'success': True}
    if message:
        response['message'] = message
    if data is not None:
        response['data'] = data
    return response, status_code


def error_response(message, status_code=400, errors=None):
    """Generate error response"""
    response = {
        'success': False,
        'error': message
    }
    if errors:
        response['errors'] = errors
    return response, status_code