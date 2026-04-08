"""
Work Schedule Overrides & Special Day Off Routes
================================================
Admin kompaniyasidagi xodimlarga:
  1. Ish vaqtini vaqt oralig'ida o'zgartirish (WorkTimeOverride)
     - Misol: Ramazon oyida barcha 17:00 da ketadi
     - Misol: Fevralda 9:30 da kelish mumkin
  2. Maxsus dam olish / erta ketish / kech kelish kunlarini belgilash (SpecialDayOff)
     - Misol: To'y kuni - xodimga dam olish
     - Misol: Milliy bayram - barcha erta ketadi

Qo'llanish doirasi (scope):
  - employee_id  => faqat shu xodim
  - department_id => shu bo'lim
  - branch_id    => shu filial
  - barchasi null => kompaniyaning barcha xodimlar
"""

from flask import Blueprint, request, g
from sqlalchemy import and_, or_
from datetime import date, datetime

from database import (
    get_db, WorkTimeOverride, SpecialDayOff,
    Employee, Department, Branch
)
from utils.decorators import company_admin_required
from utils.helpers import success_response, error_response

overrides_bp = Blueprint('overrides', __name__)


# ============================================================
# YORDAMCHI FUNKSIYALAR
# ============================================================

def parse_date_str(s):
    """YYYY-MM-DD formatidagi satrni date ga o'tkazish"""
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except Exception:
        return None


def parse_time_str(s):
    """HH:MM yoki HH:MM:SS formatdagi satrni time ga o'tkazish"""
    if not s:
        return None
    try:
        parts = s.split(':')
        return datetime.strptime(f"{parts[0]}:{parts[1]}", '%H:%M').time()
    except Exception:
        return None


def validate_scope(data, company_id, db):
    """
    Qo'llanish doirasini tekshirish va ID larning kompaniyaga tegishliligini tasdiqlash.
    Returns: (employee_id, department_id, branch_id, error_message)
    """
    employee_id = data.get('employee_id')
    department_id = data.get('department_id')
    branch_id = data.get('branch_id')

    if employee_id:
        emp = db.query(Employee).filter_by(id=employee_id, company_id=company_id).first()
        if not emp:
            return None, None, None, "Employee not found in your company"

    if department_id:
        dept = db.query(Department).filter_by(id=department_id, company_id=company_id).first()
        if not dept:
            return None, None, None, "Department not found in your company"

    if branch_id:
        branch = db.query(Branch).filter_by(id=branch_id, company_id=company_id).first()
        if not branch:
            return None, None, None, "Branch not found in your company"

    return employee_id, department_id, branch_id, None


# ============================================================
# WORK TIME OVERRIDES - Ish vaqtini o'zgartirish
# ============================================================

@overrides_bp.route('/work-time', methods=['GET'])
@company_admin_required
def list_work_time_overrides():
    """
    Kompaniyaning barcha ish vaqti o'zgartirishlarini ko'rish.

    Query params:
      - employee_id, department_id, branch_id (filter)
      - active_only=true (faqat aktiv)
      - date (berilgan sana uchun aktiv bo'lganlar)
    """
    db = get_db()
    try:
        query = db.query(WorkTimeOverride).filter_by(company_id=g.company_id)

        employee_id = request.args.get('employee_id')
        department_id = request.args.get('department_id')
        branch_id = request.args.get('branch_id')
        active_only = request.args.get('active_only', 'false').lower() == 'true'
        filter_date_str = request.args.get('date')

        if employee_id:
            query = query.filter(WorkTimeOverride.employee_id == employee_id)
        if department_id:
            query = query.filter(WorkTimeOverride.department_id == department_id)
        if branch_id:
            query = query.filter(WorkTimeOverride.branch_id == branch_id)
        if active_only:
            query = query.filter(WorkTimeOverride.is_active == True)
        if filter_date_str:
            filter_date = parse_date_str(filter_date_str)
            if filter_date:
                query = query.filter(
                    WorkTimeOverride.start_date <= filter_date,
                    WorkTimeOverride.end_date >= filter_date
                )

        overrides = query.order_by(WorkTimeOverride.start_date.desc()).all()
        return success_response([o.to_dict() for o in overrides])
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        db.close()


@overrides_bp.route('/work-time', methods=['POST'])
@company_admin_required
def create_work_time_override():
    """
    Yangi ish vaqti o'zgartirishini yaratish.

    Body:
    {
        "title": "Ramazon oyi",
        "start_date": "2025-03-01",
        "end_date": "2025-03-31",
        "work_start_time": "09:30",   // ixtiyoriy
        "work_end_time": "17:00",     // ixtiyoriy
        "reason": "Ramazon oyi munosabati bilan",
        "employee_id": "...",         // ixtiyoriy - faqat shu xodim
        "department_id": "...",       // ixtiyoriy - shu bo'lim
        "branch_id": "..."            // ixtiyoriy - shu filial
        // barchasi null = butun kompaniya
    }
    """
    db = get_db()
    try:
        data = request.get_json()
        if not data:
            return error_response("Request body required", 400)

        # Majburiy maydonlar
        title = data.get('title', '').strip()
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')

        if not title:
            return error_response("title is required", 400)
        if not start_date_str or not end_date_str:
            return error_response("start_date and end_date are required", 400)

        start_date = parse_date_str(start_date_str)
        end_date = parse_date_str(end_date_str)

        if not start_date or not end_date:
            return error_response("Invalid date format. Use YYYY-MM-DD", 400)
        if start_date > end_date:
            return error_response("start_date must be before or equal to end_date", 400)

        # Kamida bittasi bo'lishi kerak
        work_start = parse_time_str(data.get('work_start_time'))
        work_end = parse_time_str(data.get('work_end_time'))

        if not work_start and not work_end:
            return error_response(
                "At least one of work_start_time or work_end_time is required", 400
            )

        # Scope tekshirish
        employee_id, department_id, branch_id, scope_err = validate_scope(
            data, g.company_id, db
        )
        if scope_err:
            return error_response(scope_err, 404)

        override = WorkTimeOverride(
            company_id=g.company_id,
            employee_id=employee_id,
            department_id=department_id,
            branch_id=branch_id,
            start_date=start_date,
            end_date=end_date,
            work_start_time=work_start,
            work_end_time=work_end,
            title=title,
            reason=data.get('reason'),
            is_active=True,
            created_by=g.user_id
        )

        db.add(override)
        db.commit()
        db.refresh(override)

        return success_response(override.to_dict(), message="Work time override created successfully", status_code=201)

    except Exception as e:
        db.rollback()
        return error_response(str(e), 500)
    finally:
        db.close()


@overrides_bp.route('/work-time/<override_id>', methods=['GET'])
@company_admin_required
def get_work_time_override(override_id):
    """Bitta ish vaqti o'zgartirishini ko'rish"""
    db = get_db()
    try:
        override = db.query(WorkTimeOverride).filter_by(
            id=override_id, company_id=g.company_id
        ).first()
        if not override:
            return error_response("Override not found", 404)
        return success_response(override.to_dict())
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        db.close()


@overrides_bp.route('/work-time/<override_id>', methods=['PUT'])
@company_admin_required
def update_work_time_override(override_id):
    """Ish vaqti o'zgartirishini yangilash"""
    db = get_db()
    try:
        override = db.query(WorkTimeOverride).filter_by(
            id=override_id, company_id=g.company_id
        ).first()
        if not override:
            return error_response("Override not found", 404)

        data = request.get_json()
        if not data:
            return error_response("Request body required", 400)

        # Mavjud qiymatlarni yangilash
        if 'title' in data:
            override.title = data['title'].strip()
        if 'reason' in data:
            override.reason = data['reason']
        if 'start_date' in data:
            d = parse_date_str(data['start_date'])
            if not d:
                return error_response("Invalid start_date format", 400)
            override.start_date = d
        if 'end_date' in data:
            d = parse_date_str(data['end_date'])
            if not d:
                return error_response("Invalid end_date format", 400)
            override.end_date = d
        if 'work_start_time' in data:
            override.work_start_time = parse_time_str(data['work_start_time'])
        if 'work_end_time' in data:
            override.work_end_time = parse_time_str(data['work_end_time'])
        if 'is_active' in data:
            override.is_active = bool(data['is_active'])

        if override.start_date > override.end_date:
            return error_response("start_date must be before or equal to end_date", 400)

        db.commit()
        db.refresh(override)
        return success_response(override.to_dict(), message="Updated successfully")

    except Exception as e:
        db.rollback()
        return error_response(str(e), 500)
    finally:
        db.close()


@overrides_bp.route('/work-time/<override_id>', methods=['DELETE'])
@company_admin_required
def delete_work_time_override(override_id):
    """Ish vaqti o'zgartirishini o'chirish"""
    db = get_db()
    try:
        override = db.query(WorkTimeOverride).filter_by(
            id=override_id, company_id=g.company_id
        ).first()
        if not override:
            return error_response("Override not found", 404)

        db.delete(override)
        db.commit()
        return success_response({'id': override_id}, message="Deleted successfully")

    except Exception as e:
        db.rollback()
        return error_response(str(e), 500)
    finally:
        db.close()


@overrides_bp.route('/work-time/<override_id>/toggle', methods=['POST'])
@company_admin_required
def toggle_work_time_override(override_id):
    """Ish vaqti o'zgartirishini yoqish/o'chirish"""
    db = get_db()
    try:
        override = db.query(WorkTimeOverride).filter_by(
            id=override_id, company_id=g.company_id
        ).first()
        if not override:
            return error_response("Override not found", 404)

        override.is_active = not override.is_active
        db.commit()
        db.refresh(override)
        status_text = "activated" if override.is_active else "deactivated"
        return success_response(override.to_dict(), message=f"Override {status_text}")

    except Exception as e:
        db.rollback()
        return error_response(str(e), 500)
    finally:
        db.close()


# ============================================================
# SPECIAL DAY OFFS - Maxsus dam olish / erta ketish
# ============================================================

@overrides_bp.route('/special-days', methods=['GET'])
@company_admin_required
def list_special_day_offs():
    """
    Maxsus kunlarni ko'rish.

    Query params:
      - employee_id, department_id, branch_id
      - event_type: day_off | early_leave | late_start
      - active_only=true
      - start_date, end_date (sana oralig'i)
    """
    db = get_db()
    try:
        query = db.query(SpecialDayOff).filter_by(company_id=g.company_id)

        employee_id = request.args.get('employee_id')
        department_id = request.args.get('department_id')
        branch_id = request.args.get('branch_id')
        event_type = request.args.get('event_type')
        active_only = request.args.get('active_only', 'false').lower() == 'true'
        start_str = request.args.get('start_date')
        end_str = request.args.get('end_date')

        if employee_id:
            query = query.filter(SpecialDayOff.employee_id == employee_id)
        if department_id:
            query = query.filter(SpecialDayOff.department_id == department_id)
        if branch_id:
            query = query.filter(SpecialDayOff.branch_id == branch_id)
        if event_type:
            query = query.filter(SpecialDayOff.event_type == event_type)
        if active_only:
            query = query.filter(SpecialDayOff.is_active == True)
        if start_str:
            sd = parse_date_str(start_str)
            if sd:
                query = query.filter(SpecialDayOff.end_date >= sd)
        if end_str:
            ed = parse_date_str(end_str)
            if ed:
                query = query.filter(SpecialDayOff.start_date <= ed)

        events = query.order_by(SpecialDayOff.start_date.desc()).all()
        return success_response([e.to_dict() for e in events])

    except Exception as e:
        return error_response(str(e), 500)
    finally:
        db.close()


@overrides_bp.route('/special-days', methods=['POST'])
@company_admin_required
def create_special_day_off():
    """
    Yangi maxsus kun yaratish.

    Body:
    {
        "title": "Abdullayevning to'yi",
        "event_type": "day_off",         // day_off | early_leave | late_start
        "start_date": "2025-05-10",
        "end_date": "2025-05-10",        // bir kun uchun start == end
        "override_end_time": "15:00",    // early_leave uchun: bu vaqtdan keyin ketish OK
        "override_start_time": "10:00",  // late_start uchun: bu vaqtgacha kelish OK
        "reason": "Xodimning to'yi",
        "employee_id": "...",            // faqat shu xodim (ixtiyoriy)
        "department_id": "...",          // shu bo'lim (ixtiyoriy)
        "branch_id": "..."               // shu filial (ixtiyoriy)
    }

    event_type qiymatlari:
      - day_off     : Bu kun ish kuni emas, jarima yo'q
      - early_leave : Erta ketishga ruxsat (override_end_time dan keyin ketish jarima emas)
      - late_start  : Kech kelishga ruxsat (override_start_time gacha kelish jarima emas)
    """
    db = get_db()
    try:
        data = request.get_json()
        if not data:
            return error_response("Request body required", 400)

        title = data.get('title', '').strip()
        event_type = data.get('event_type', '').strip()
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')

        if not title:
            return error_response("title is required", 400)
        if event_type not in ('day_off', 'early_leave', 'late_start'):
            return error_response(
                "event_type must be one of: day_off, early_leave, late_start", 400
            )
        if not start_date_str or not end_date_str:
            return error_response("start_date and end_date are required", 400)

        start_date = parse_date_str(start_date_str)
        end_date = parse_date_str(end_date_str)

        if not start_date or not end_date:
            return error_response("Invalid date format. Use YYYY-MM-DD", 400)
        if start_date > end_date:
            return error_response("start_date must be before or equal to end_date", 400)

        # Vaqtlarni parse qilish (event_type ga bog'liq)
        override_start_time = parse_time_str(data.get('override_start_time'))
        override_end_time = parse_time_str(data.get('override_end_time'))

        # Scope tekshirish
        employee_id, department_id, branch_id, scope_err = validate_scope(
            data, g.company_id, db
        )
        if scope_err:
            return error_response(scope_err, 404)

        event = SpecialDayOff(
            company_id=g.company_id,
            employee_id=employee_id,
            department_id=department_id,
            branch_id=branch_id,
            start_date=start_date,
            end_date=end_date,
            event_type=event_type,
            override_start_time=override_start_time,
            override_end_time=override_end_time,
            title=title,
            reason=data.get('reason'),
            is_active=True,
            created_by=g.user_id
        )

        db.add(event)
        db.commit()
        db.refresh(event)

        return success_response(event.to_dict(), message="Special day off created successfully", status_code=201)

    except Exception as e:
        db.rollback()
        return error_response(str(e), 500)
    finally:
        db.close()


@overrides_bp.route('/special-days/<event_id>', methods=['GET'])
@company_admin_required
def get_special_day_off(event_id):
    """Bitta maxsus kunni ko'rish"""
    db = get_db()
    try:
        event = db.query(SpecialDayOff).filter_by(
            id=event_id, company_id=g.company_id
        ).first()
        if not event:
            return error_response("Special day off not found", 404)
        return success_response(event.to_dict())
    except Exception as e:
        return error_response(str(e), 500)
    finally:
        db.close()


@overrides_bp.route('/special-days/<event_id>', methods=['PUT'])
@company_admin_required
def update_special_day_off(event_id):
    """Maxsus kunni yangilash"""
    db = get_db()
    try:
        event = db.query(SpecialDayOff).filter_by(
            id=event_id, company_id=g.company_id
        ).first()
        if not event:
            return error_response("Special day off not found", 404)

        data = request.get_json()
        if not data:
            return error_response("Request body required", 400)

        if 'title' in data:
            event.title = data['title'].strip()
        if 'reason' in data:
            event.reason = data['reason']
        if 'event_type' in data:
            if data['event_type'] not in ('day_off', 'early_leave', 'late_start'):
                return error_response("Invalid event_type", 400)
            event.event_type = data['event_type']
        if 'start_date' in data:
            d = parse_date_str(data['start_date'])
            if not d:
                return error_response("Invalid start_date format", 400)
            event.start_date = d
        if 'end_date' in data:
            d = parse_date_str(data['end_date'])
            if not d:
                return error_response("Invalid end_date format", 400)
            event.end_date = d
        if 'override_start_time' in data:
            event.override_start_time = parse_time_str(data['override_start_time'])
        if 'override_end_time' in data:
            event.override_end_time = parse_time_str(data['override_end_time'])
        if 'is_active' in data:
            event.is_active = bool(data['is_active'])

        if event.start_date > event.end_date:
            return error_response("start_date must be before or equal to end_date", 400)

        db.commit()
        db.refresh(event)
        return success_response(event.to_dict(), message="Updated successfully")

    except Exception as e:
        db.rollback()
        return error_response(str(e), 500)
    finally:
        db.close()


@overrides_bp.route('/special-days/<event_id>', methods=['DELETE'])
@company_admin_required
def delete_special_day_off(event_id):
    """Maxsus kunni o'chirish"""
    db = get_db()
    try:
        event = db.query(SpecialDayOff).filter_by(
            id=event_id, company_id=g.company_id
        ).first()
        if not event:
            return error_response("Special day off not found", 404)

        db.delete(event)
        db.commit()
        return success_response({'id': event_id}, message="Deleted successfully")

    except Exception as e:
        db.rollback()
        return error_response(str(e), 500)
    finally:
        db.close()


@overrides_bp.route('/special-days/<event_id>/toggle', methods=['POST'])
@company_admin_required
def toggle_special_day_off(event_id):
    """Maxsus kunni yoqish/o'chirish"""
    db = get_db()
    try:
        event = db.query(SpecialDayOff).filter_by(
            id=event_id, company_id=g.company_id
        ).first()
        if not event:
            return error_response("Special day off not found", 404)

        event.is_active = not event.is_active
        db.commit()
        db.refresh(event)
        status_text = "activated" if event.is_active else "deactivated"
        return success_response(event.to_dict(), message=f"Special day off {status_text}")

    except Exception as e:
        db.rollback()
        return error_response(str(e), 500)
    finally:
        db.close()


# ============================================================
# BULK OPERATIONS - Ko'p xodimga bir vaqtda belgilash
# ============================================================

@overrides_bp.route('/special-days/bulk', methods=['POST'])
@company_admin_required
def bulk_create_special_day_off():
    """
    Bir nechta xodimga bir vaqtda maxsus kun belgilash.

    Body:
    {
        "employee_ids": ["id1", "id2", "id3"],  // bo'sh = scope bo'yicha
        "title": "Haftalik dam olish",
        "event_type": "day_off",
        "start_date": "2025-05-10",
        "end_date": "2025-05-12",
        "reason": "..."
    }
    """
    db = get_db()
    try:
        data = request.get_json()
        if not data:
            return error_response("Request body required", 400)

        employee_ids = data.get('employee_ids', [])
        title = data.get('title', '').strip()
        event_type = data.get('event_type', '').strip()
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')

        if not title:
            return error_response("title is required", 400)
        if event_type not in ('day_off', 'early_leave', 'late_start'):
            return error_response("Invalid event_type", 400)
        if not start_date_str or not end_date_str:
            return error_response("start_date and end_date are required", 400)

        start_date = parse_date_str(start_date_str)
        end_date = parse_date_str(end_date_str)
        if not start_date or not end_date:
            return error_response("Invalid date format. Use YYYY-MM-DD", 400)
        if start_date > end_date:
            return error_response("start_date must be before end_date", 400)

        override_start_time = parse_time_str(data.get('override_start_time'))
        override_end_time = parse_time_str(data.get('override_end_time'))

        created = []

        if employee_ids:
            # Faqat ko'rsatilgan xodimlar uchun
            for emp_id in employee_ids:
                emp = db.query(Employee).filter_by(
                    id=emp_id, company_id=g.company_id
                ).first()
                if not emp:
                    continue

                event = SpecialDayOff(
                    company_id=g.company_id,
                    employee_id=emp_id,
                    start_date=start_date,
                    end_date=end_date,
                    event_type=event_type,
                    override_start_time=override_start_time,
                    override_end_time=override_end_time,
                    title=title,
                    reason=data.get('reason'),
                    is_active=True,
                    created_by=g.user_id
                )
                db.add(event)
                created.append(emp_id)
        else:
            # Kompaniya darajasida (barcha uchun bitta yozuv)
            employee_id, department_id, branch_id, scope_err = validate_scope(
                data, g.company_id, db
            )
            if scope_err:
                return error_response(scope_err, 404)

            event = SpecialDayOff(
                company_id=g.company_id,
                employee_id=employee_id,
                department_id=department_id,
                branch_id=branch_id,
                start_date=start_date,
                end_date=end_date,
                event_type=event_type,
                override_start_time=override_start_time,
                override_end_time=override_end_time,
                title=title,
                reason=data.get('reason'),
                is_active=True,
                created_by=g.user_id
            )
            db.add(event)
            created.append('company-wide')

        db.commit()

        return success_response(
            {'created_count': len(created), 'scope': created},
            message=f"{len(created)} special day off(s) created",
            status_code=201
        )

    except Exception as e:
        db.rollback()
        return error_response(str(e), 500)
    finally:
        db.close()


# ============================================================
# PREVIEW - Berilgan sana uchun xodimga qo'llaniladigan qoidalar
# ============================================================

@overrides_bp.route('/preview', methods=['GET'])
@company_admin_required
def preview_employee_schedule():
    """
    Berilgan sana va xodim uchun qo'llaniladigan ish vaqtini ko'rish.
    Bu endpoint admin uchun: o'zgartirishlar to'g'ri ishlayaptimi tekshirish.

    Query params:
      - employee_id (majburiy)
      - date (majburiy, YYYY-MM-DD)
    """
    db = get_db()
    try:
        employee_id = request.args.get('employee_id')
        date_str = request.args.get('date')

        if not employee_id or not date_str:
            return error_response("employee_id and date are required", 400)

        check_date = parse_date_str(date_str)
        if not check_date:
            return error_response("Invalid date format. Use YYYY-MM-DD", 400)

        employee = db.query(Employee).filter_by(
            id=employee_id, company_id=g.company_id
        ).first()
        if not employee:
            return error_response("Employee not found", 404)

        from services.attendance_service import (
            get_employee_work_time_for_date,
            get_active_overrides_for_employee,
            get_active_special_day_off_for_employee
        )

        work_start, work_end, is_day_off, special_event = get_employee_work_time_for_date(
            employee, check_date, db
        )
        active_override = get_active_overrides_for_employee(employee, check_date, db)

        result = {
            'employee_id': employee.id,
            'employee_name': employee.full_name,
            'date': date_str,
            'day_name': ['Dushanba', 'Seshanba', 'Chorshanba', 'Payshanba',
                         'Juma', 'Shanba', 'Yakshanba'][check_date.isoweekday() - 1],
            'is_day_off': is_day_off,
            'effective_work_start': str(work_start) if work_start else None,
            'effective_work_end': str(work_end) if work_end else None,
            'default_work_start': str(employee.work_start_time) if employee.work_start_time else None,
            'default_work_end': str(employee.work_end_time) if employee.work_end_time else None,
            'active_work_time_override': active_override.to_dict() if active_override else None,
            'active_special_event': special_event.to_dict() if special_event else None,
            'rules_applied': []
        }

        # Qo'llangan qoidalarni tushuntirish
        if is_day_off:
            if special_event and special_event.event_type == 'day_off':
                result['rules_applied'].append(
                    f"✅ Maxsus dam olish kuni: '{special_event.title}'"
                )
            else:
                result['rules_applied'].append("📅 Haftalik jadval bo'yicha dam olish kuni")
        else:
            if special_event and special_event.event_type == 'early_leave':
                result['rules_applied'].append(
                    f"⏰ Erta ketishga ruxsat: '{special_event.title}' "
                    f"({special_event.override_end_time} dan keyin ketish OK)"
                )
            if special_event and special_event.event_type == 'late_start':
                result['rules_applied'].append(
                    f"⏰ Kech kelishga ruxsat: '{special_event.title}' "
                    f"({special_event.override_start_time} gacha kelish OK)"
                )
            if active_override:
                parts = []
                if active_override.work_start_time:
                    parts.append(f"kelish: {active_override.work_start_time}")
                if active_override.work_end_time:
                    parts.append(f"ketish: {active_override.work_end_time}")
                result['rules_applied'].append(
                    f"🔄 Ish vaqti o'zgartirilgan: '{active_override.title}' ({', '.join(parts)})"
                )
            if not result['rules_applied']:
                result['rules_applied'].append("📋 Standart jadval qo'llanilmoqda")

        return success_response(result)

    except Exception as e:
        return error_response(str(e), 500)
    finally:
        db.close()


# ============================================================
# RETROAKTIV QO'LLASH — O'tgan kunlarga override ni tatbiq etish
# ============================================================

@overrides_bp.route('/work-time/<override_id>/apply-retroactive', methods=['POST'])
@company_admin_required
def apply_retroactive(override_id):
    """
    Override ni o'tgan kunlarga retroaktiv qo'llash:
    1. Override sana oralig'idagi barcha AttendanceLog larni topadi
    2. Har bir log uchun yangi work_start/end vaqtga nisbatan
       late_minutes va early_leave_minutes ni qayta hisoblaydi
    3. Mavjud 'late' Penalty larni yangi minutga yangilaydi (summa ham)
    4. Agar kechikish yo'qolsa — penaltini o'chiradi
    5. Agar yangi kechikish paydo bo'lsa — yangi penalty yaratadi

    Response:
    {
        "processed_logs": 15,
        "updated_penalties": 8,
        "deleted_penalties": 3,
        "created_penalties": 2,
        "skipped": 1,
        "details": [...]
    }
    """
    from decimal import Decimal
    from datetime import datetime, timedelta
    import pytz

    db = get_db()
    try:
        # Override ni olish
        override = db.query(WorkTimeOverride).filter_by(
            id=override_id, company_id=g.company_id
        ).first()
        if not override:
            return error_response("Override not found", 404)

        if not override.is_active:
            return error_response("Override is not active. Activate it first.", 400)

        # Company settings (jarima hisoblash uchun)
        from database import CompanySettings, AttendanceLog, Employee, Penalty
        settings = db.query(CompanySettings).filter_by(
            company_id=g.company_id
        ).first()
        if not settings:
            return error_response("Company settings not found", 400)

        grace_period = settings.late_threshold_minutes or 10
        penalty_per_minute = float(settings.penalty_per_minute or settings.late_penalty_per_minute or 0)

        tashkent_tz = pytz.timezone('Asia/Tashkent')

        # Override qo'llaniladigan xodimlarni aniqlash
        from sqlalchemy import and_, or_
        emp_query = db.query(Employee).filter_by(
            company_id=g.company_id,
            status='active'
        )
        if override.employee_id:
            emp_query = emp_query.filter(Employee.id == override.employee_id)
        elif override.department_id:
            emp_query = emp_query.filter(Employee.department_id == override.department_id)
        elif override.branch_id:
            emp_query = emp_query.filter(Employee.branch_id == override.branch_id)
        # else: butun kompaniya — barcha xodimlar

        employees_to_process = emp_query.all()

        stats = {
            'processed_logs': 0,
            'updated_penalties': 0,
            'deleted_penalties': 0,
            'created_penalties': 0,
            'skipped': 0,
            'details': []
        }

        for employee in employees_to_process:
            # Shu xodimning override sana oralig'idagi attendance log larini olish
            logs = db.query(AttendanceLog).filter(
                and_(
                    AttendanceLog.employee_id == employee.id,
                    AttendanceLog.date >= override.start_date,
                    AttendanceLog.date <= override.end_date
                )
            ).all()

            for log in logs:
                if not log.check_in_time:
                    stats['skipped'] += 1
                    continue

                stats['processed_logs'] += 1

                # Override bo'yicha yangi work vaqtlarni hisoblash
                # Avval bazaviy vaqtni olish (schedule yoki employee default)
                from services.attendance_service import (
                    get_employee_work_time_for_date,
                    parse_time_field
                )
                work_start_time, work_end_time, is_day_off, special_event = \
                    get_employee_work_time_for_date(employee, log.date, db)

                if is_day_off:
                    stats['skipped'] += 1
                    continue

                work_start = parse_time_field(work_start_time)
                work_end = parse_time_field(work_end_time)

                detail = {
                    'employee': employee.full_name,
                    'date': log.date.isoformat(),
                    'check_in': log.check_in_time.strftime('%H:%M') if log.check_in_time else None,
                    'check_out': log.check_out_time.strftime('%H:%M') if log.check_out_time else None,
                    'old_late_minutes': log.late_minutes,
                    'old_early_leave_minutes': log.early_leave_minutes,
                }

                # ── KECHIKISHNI QAYTA HISOBLASH ──────────────────────
                new_late_minutes = 0
                if work_start and log.check_in_time:
                    scheduled_start = tashkent_tz.localize(
                        datetime.combine(log.date, work_start)
                    )
                    # check_in_time timezone aware qilish
                    check_in = log.check_in_time
                    if check_in.tzinfo is None:
                        check_in = tashkent_tz.localize(check_in)
                    else:
                        check_in = check_in.astimezone(tashkent_tz)

                    time_diff = (check_in - scheduled_start).total_seconds() / 60
                    if time_diff > grace_period:
                        new_late_minutes = int(time_diff - grace_period)

                # ── ERTA KETISHNI QAYTA HISOBLASH ─────────────────────
                new_early_leave_minutes = 0
                if work_end and log.check_out_time:
                    scheduled_end = tashkent_tz.localize(
                        datetime.combine(log.date, work_end)
                    )
                    check_out = log.check_out_time
                    if check_out.tzinfo is None:
                        check_out = tashkent_tz.localize(check_out)
                    else:
                        check_out = check_out.astimezone(tashkent_tz)

                    time_diff_out = (check_out - scheduled_end).total_seconds() / 60
                    if time_diff_out < 0:
                        new_early_leave_minutes = int(abs(time_diff_out))

                # AttendanceLog ni yangilash
                log.late_minutes = new_late_minutes
                log.early_leave_minutes = new_early_leave_minutes

                detail['new_late_minutes'] = new_late_minutes
                detail['new_early_leave_minutes'] = new_early_leave_minutes

                # ── KECHIKISH PENALTY YANGILASH ───────────────────────
                existing_late_penalty = db.query(Penalty).filter_by(
                    employee_id=employee.id,
                    attendance_log_id=log.id,
                    penalty_type='late'
                ).first()

                if existing_late_penalty and not existing_late_penalty.is_waived:
                    if new_late_minutes <= 0:
                        # Kechikish yo'qoldi — penalty o'chirish
                        db.delete(existing_late_penalty)
                        stats['deleted_penalties'] += 1
                        detail['penalty_action'] = 'deleted (no more lateness)'
                    else:
                        # Minutlarni va summani yangilash
                        new_amount = Decimal(str(new_late_minutes)) * Decimal(str(penalty_per_minute))
                        existing_late_penalty.late_minutes = new_late_minutes
                        existing_late_penalty.amount = float(new_amount)
                        existing_late_penalty.reason = f"Late by {new_late_minutes} minutes (retroactively recalculated)"
                        stats['updated_penalties'] += 1
                        detail['penalty_action'] = f'updated: {log.late_minutes}min → {new_late_minutes}min'
                elif not existing_late_penalty and new_late_minutes > 0 and penalty_per_minute > 0:
                    # Yangi penalty yaratish
                    import uuid
                    new_amount = Decimal(str(new_late_minutes)) * Decimal(str(penalty_per_minute))
                    new_penalty = Penalty(
                        id=str(uuid.uuid4()),
                        company_id=g.company_id,
                        employee_id=employee.id,
                        attendance_log_id=log.id,
                        penalty_type='late',
                        amount=float(new_amount),
                        late_minutes=new_late_minutes,
                        reason=f"Late by {new_late_minutes} minutes (applied retroactively)",
                        date=log.date
                    )
                    db.add(new_penalty)
                    stats['created_penalties'] += 1
                    detail['penalty_action'] = f'created: {new_late_minutes}min'
                else:
                    detail['penalty_action'] = 'no change needed'

                # ── ERTA KETISH PENALTY YANGILASH ─────────────────────
                existing_early_penalty = db.query(Penalty).filter_by(
                    employee_id=employee.id,
                    attendance_log_id=log.id,
                    penalty_type='early_leave'
                ).first()

                if existing_early_penalty and not existing_early_penalty.is_waived:
                    if new_early_leave_minutes <= 0:
                        db.delete(existing_early_penalty)
                        stats['deleted_penalties'] += 1
                    else:
                        new_amount_el = Decimal(str(new_early_leave_minutes)) * Decimal(str(penalty_per_minute))
                        existing_early_penalty.late_minutes = new_early_leave_minutes
                        existing_early_penalty.amount = float(new_amount_el)
                        existing_early_penalty.reason = f"Left early by {new_early_leave_minutes} minutes (retroactively recalculated)"
                        stats['updated_penalties'] += 1

                stats['details'].append(detail)

        db.commit()

        return success_response(
            stats,
            message=f"Retroaktiv qo'llash muvaffaqiyatli: {stats['processed_logs']} kun qayta hisoblandi"
        )

    except Exception as e:
        db.rollback()
        import traceback
        return error_response(f"Xato: {str(e)}\n{traceback.format_exc()}", 500)
    finally:
        db.close()
