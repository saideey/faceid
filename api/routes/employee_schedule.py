from flask import Blueprint, request, jsonify, g
from database import get_db, EmployeeSchedule, Employee
from middleware.auth_middleware import require_auth
from middleware.company_middleware import load_company_context
from utils.helpers import success_response, error_response
import logging
from datetime import time as datetime_time

schedule_bp = Blueprint('schedule', __name__)
logger = logging.getLogger(__name__)


@schedule_bp.route('/<employee_id>/schedule', methods=['GET'])
@require_auth
@load_company_context
def get_employee_schedule(employee_id):
    """
    Xodimning haftalik jadvalini ko'rish

    Response: {
        "employee": {...},
        "schedule": [
            {"day_of_week": 1, "day_name": "Dushanba", "work_start_time": "09:00", "work_end_time": "18:00", "is_day_off": false},
            ...
        ]
    }
    """
    db = get_db()

    try:
        # Verify employee belongs to this company
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        # Get schedule
        schedules = db.query(EmployeeSchedule).filter_by(
            employee_id=employee_id
        ).order_by(EmployeeSchedule.day_of_week).all()

        # Day names in Uzbek
        day_names = {
            1: "Dushanba",
            2: "Seshanba",
            3: "Chorshanba",
            4: "Payshanba",
            5: "Juma",
            6: "Shanba",
            7: "Yakshanba"
        }

        # Format schedule with all 7 days
        schedule_dict = {}
        for schedule in schedules:
            schedule_dict[schedule.day_of_week] = schedule

        # Build full week schedule
        full_schedule = []
        for day in range(1, 8):
            if day in schedule_dict:
                sched = schedule_dict[day]
                full_schedule.append({
                    'day_of_week': day,
                    'day_name': day_names[day],
                    'work_start_time': str(sched.work_start_time) if sched.work_start_time else None,
                    'work_end_time': str(sched.work_end_time) if sched.work_end_time else None,
                    'is_day_off': sched.is_day_off,
                    'id': sched.id
                })
            else:
                # Use employee's default work times if no schedule
                full_schedule.append({
                    'day_of_week': day,
                    'day_name': day_names[day],
                    'work_start_time': str(employee.work_start_time) if employee.work_start_time else "09:00:00",
                    'work_end_time': str(employee.work_end_time) if employee.work_end_time else "18:00:00",
                    'is_day_off': False,
                    'id': None
                })

        return success_response({
            'employee': {
                'id': employee.id,
                'full_name': employee.full_name,
                'employee_no': employee.employee_no
            },
            'schedule': full_schedule
        })

    except Exception as e:
        logger.error(f"Error getting schedule: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@schedule_bp.route('/<employee_id>/schedule', methods=['POST'])
@require_auth
@load_company_context
def set_employee_schedule(employee_id):
    """
    Xodim uchun haftalik jadval o'rnatish

    Body: {
        "schedule": [
            {"day_of_week": 1, "work_start_time": "09:00", "work_end_time": "18:00", "is_day_off": false},
            {"day_of_week": 2, "work_start_time": "09:00", "work_end_time": "18:00", "is_day_off": false},
            {"day_of_week": 6, "is_day_off": true},
            {"day_of_week": 7, "is_day_off": true},
            ...
        ]
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        if not data.get('schedule'):
            return error_response("Schedule array is required", 400)

        # Verify employee belongs to this company
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        schedule_data = data['schedule']

        # Validate schedule data
        if not isinstance(schedule_data, list):
            return error_response("Schedule must be an array", 400)

        # Delete existing schedule
        db.query(EmployeeSchedule).filter_by(employee_id=employee_id).delete()

        # Create new schedule
        created_schedules = []
        for item in schedule_data:
            day_of_week = item.get('day_of_week')

            if not day_of_week or day_of_week < 1 or day_of_week > 7:
                return error_response(f"Invalid day_of_week: {day_of_week}. Must be 1-7", 400)

            is_day_off = item.get('is_day_off', False)

            # Parse time
            work_start_time = None
            work_end_time = None

            if not is_day_off:
                start_str = item.get('work_start_time')
                end_str = item.get('work_end_time')

                if not start_str or not end_str:
                    return error_response(f"work_start_time and work_end_time required for day {day_of_week}", 400)

                try:
                    # Parse HH:MM or HH:MM:SS format
                    start_parts = start_str.split(':')
                    work_start_time = datetime_time(int(start_parts[0]), int(start_parts[1]))

                    end_parts = end_str.split(':')
                    work_end_time = datetime_time(int(end_parts[0]), int(end_parts[1]))
                except Exception as e:
                    return error_response(f"Invalid time format for day {day_of_week}: {str(e)}", 400)

            # Create schedule entry
            schedule = EmployeeSchedule(
                employee_id=employee_id,
                day_of_week=day_of_week,
                work_start_time=work_start_time,
                work_end_time=work_end_time,
                is_day_off=is_day_off
            )

            db.add(schedule)
            created_schedules.append(schedule)

        db.commit()

        # Refresh all schedules
        for schedule in created_schedules:
            db.refresh(schedule)

        logger.info(f"Schedule set for employee: {employee.full_name} ({employee_id})")

        return success_response(
            {'schedules': [s.to_dict() for s in created_schedules]},
            "Schedule saved successfully",
            201
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Error setting schedule: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@schedule_bp.route('/<employee_id>/schedule/<int:day_of_week>', methods=['PUT'])
@require_auth
@load_company_context
def update_day_schedule(employee_id, day_of_week):
    """
    Bitta kunning jadvalini yangilash

    Body: {
        "work_start_time": "09:00",
        "work_end_time": "18:00",
        "is_day_off": false
    }
    """
    db = get_db()

    try:
        # Validate day
        if day_of_week < 1 or day_of_week > 7:
            return error_response("day_of_week must be 1-7", 400)

        # Verify employee belongs to this company
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        data = request.get_json()

        # Find or create schedule for this day
        schedule = db.query(EmployeeSchedule).filter_by(
            employee_id=employee_id,
            day_of_week=day_of_week
        ).first()

        if not schedule:
            schedule = EmployeeSchedule(
                employee_id=employee_id,
                day_of_week=day_of_week
            )
            db.add(schedule)

        # Update fields
        if 'is_day_off' in data:
            schedule.is_day_off = data['is_day_off']

        if not data.get('is_day_off', False):
            # Parse work times
            if 'work_start_time' in data:
                start_str = data['work_start_time']
                start_parts = start_str.split(':')
                schedule.work_start_time = datetime_time(int(start_parts[0]), int(start_parts[1]))

            if 'work_end_time' in data:
                end_str = data['work_end_time']
                end_parts = end_str.split(':')
                schedule.work_end_time = datetime_time(int(end_parts[0]), int(end_parts[1]))
        else:
            # If day off, clear work times
            schedule.work_start_time = None
            schedule.work_end_time = None

        db.commit()
        db.refresh(schedule)

        logger.info(f"Schedule updated for employee {employee_id}, day {day_of_week}")

        return success_response(schedule.to_dict(), "Schedule updated successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating schedule: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@schedule_bp.route('/<employee_id>/schedule/<int:day_of_week>', methods=['DELETE'])
@require_auth
@load_company_context
def delete_day_schedule(employee_id, day_of_week):
    """
    Bitta kunning jadvalini o'chirish (default vaqtga qaytarish)
    """
    db = get_db()

    try:
        # Validate day
        if day_of_week < 1 or day_of_week > 7:
            return error_response("day_of_week must be 1-7", 400)

        # Verify employee belongs to this company
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        # Delete schedule for this day
        deleted = db.query(EmployeeSchedule).filter_by(
            employee_id=employee_id,
            day_of_week=day_of_week
        ).delete()

        db.commit()

        if deleted:
            logger.info(f"Schedule deleted for employee {employee_id}, day {day_of_week}")
            return success_response(None, "Schedule deleted successfully")
        else:
            return error_response("Schedule not found for this day", 404)

    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting schedule: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@schedule_bp.route('/<employee_id>/schedule/bulk', methods=['POST'])
@require_auth
@load_company_context
def set_bulk_schedule(employee_id):
    """
    Bir nechta kun uchun bir xil vaqtni o'rnatish

    Body: {
        "days": [1, 2, 3, 4, 5],  // Dushanba-Juma
        "work_start_time": "09:00",
        "work_end_time": "18:00",
        "is_day_off": false
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        days = data.get('days', [])
        if not days:
            return error_response("Days array is required", 400)

        # Verify employee belongs to this company
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        is_day_off = data.get('is_day_off', False)

        work_start_time = None
        work_end_time = None

        if not is_day_off:
            start_str = data.get('work_start_time')
            end_str = data.get('work_end_time')

            if not start_str or not end_str:
                return error_response("work_start_time and work_end_time required", 400)

            # Parse times
            start_parts = start_str.split(':')
            work_start_time = datetime_time(int(start_parts[0]), int(start_parts[1]))

            end_parts = end_str.split(':')
            work_end_time = datetime_time(int(end_parts[0]), int(end_parts[1]))

        # Update/create schedule for each day
        updated_schedules = []
        for day in days:
            if day < 1 or day > 7:
                continue

            schedule = db.query(EmployeeSchedule).filter_by(
                employee_id=employee_id,
                day_of_week=day
            ).first()

            if not schedule:
                schedule = EmployeeSchedule(
                    employee_id=employee_id,
                    day_of_week=day
                )
                db.add(schedule)

            schedule.work_start_time = work_start_time
            schedule.work_end_time = work_end_time
            schedule.is_day_off = is_day_off

            updated_schedules.append(schedule)

        db.commit()

        for schedule in updated_schedules:
            db.refresh(schedule)

        logger.info(f"Bulk schedule set for employee {employee_id}, days: {days}")

        return success_response(
            {'schedules': [s.to_dict() for s in updated_schedules]},
            "Bulk schedule saved successfully"
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Error setting bulk schedule: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@schedule_bp.route('/<employee_id>/schedule/copy-from/<source_employee_id>', methods=['POST'])
@require_auth
@load_company_context
def copy_schedule_from_employee(employee_id, source_employee_id):
    """
    Boshqa xodimning jadvalini nusxalash
    """
    db = get_db()

    try:
        # Verify both employees belong to this company
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        source_employee = db.query(Employee).filter_by(
            id=source_employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Target employee not found", 404)

        if not source_employee:
            return error_response("Source employee not found", 404)

        # Get source schedule
        source_schedules = db.query(EmployeeSchedule).filter_by(
            employee_id=source_employee_id
        ).all()

        if not source_schedules:
            return error_response("Source employee has no schedule", 404)

        # Delete target's existing schedule
        db.query(EmployeeSchedule).filter_by(employee_id=employee_id).delete()

        # Copy schedules
        new_schedules = []
        for source_sched in source_schedules:
            new_schedule = EmployeeSchedule(
                employee_id=employee_id,
                day_of_week=source_sched.day_of_week,
                work_start_time=source_sched.work_start_time,
                work_end_time=source_sched.work_end_time,
                is_day_off=source_sched.is_day_off
            )
            db.add(new_schedule)
            new_schedules.append(new_schedule)

        db.commit()

        for schedule in new_schedules:
            db.refresh(schedule)

        logger.info(f"Schedule copied from {source_employee_id} to {employee_id}")

        return success_response(
            {'schedules': [s.to_dict() for s in new_schedules]},
            f"Schedule copied from {source_employee.full_name}"
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Error copying schedule: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()