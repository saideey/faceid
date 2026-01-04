from flask import Blueprint, request, jsonify, g
from database import get_db, Penalty, Employee, AttendanceLog, CompanySettings, EmployeeSchedule
from middleware.auth_middleware import require_auth, require_super_admin
from middleware.company_middleware import load_company_context
from utils.helpers import success_response, error_response
from datetime import datetime, date
import pytz
import logging

penalty_bp = Blueprint('penalty', __name__)
logger = logging.getLogger(__name__)


@penalty_bp.route('/', methods=['GET'])
@require_auth
@load_company_context
def list_penalties():
    """
    Jarimalarni ko'rish (filter bilan)

    Query params:
    - employee_id: Xodim ID
    - start_date: Boshlanish sanasi (YYYY-MM-DD)
    - end_date: Tugash sanasi (YYYY-MM-DD)
    - is_waived: Bekor qilingan/qilinmagan (true/false)
    - is_excused: Sababli/sababsiz (true/false)
    - page, per_page
    """
    db = get_db()

    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))

        # Base query
        query = db.query(Penalty).filter_by(company_id=g.company_id)

        # Filters
        employee_id = request.args.get('employee_id')
        if employee_id:
            query = query.filter_by(employee_id=employee_id)

        start_date = request.args.get('start_date')
        if start_date:
            query = query.filter(Penalty.date >= start_date)

        end_date = request.args.get('end_date')
        if end_date:
            query = query.filter(Penalty.date <= end_date)

        is_waived = request.args.get('is_waived')
        if is_waived is not None:
            query = query.filter_by(is_waived=is_waived.lower() == 'true')

        is_excused = request.args.get('is_excused')
        if is_excused is not None:
            query = query.filter_by(is_excused=is_excused.lower() == 'true')

        # Order by date desc
        query = query.order_by(Penalty.date.desc())

        # Pagination
        total = query.count()
        penalties = query.offset((page - 1) * per_page).limit(per_page).all()

        return success_response({
            'penalties': [p.to_dict() for p in penalties],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })

    except Exception as e:
        logger.error(f"Error listing penalties: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@penalty_bp.route('/<penalty_id>', methods=['GET'])
@require_auth
@load_company_context
def get_penalty(penalty_id):
    """Bitta jarimani ko'rish"""
    db = get_db()

    try:
        penalty = db.query(Penalty).filter_by(
            id=penalty_id,
            company_id=g.company_id
        ).first()

        if not penalty:
            return error_response("Penalty not found", 404)

        return success_response(penalty.to_dict())

    except Exception as e:
        logger.error(f"Error getting penalty: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@penalty_bp.route('/<penalty_id>/waive', methods=['POST'])
@require_auth
@load_company_context
def waive_penalty(penalty_id):
    """
    Bitta jarimani bekor qilish

    Body: {
        "reason": "Sababi"
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        penalty = db.query(Penalty).filter_by(
            id=penalty_id,
            company_id=g.company_id
        ).first()

        if not penalty:
            return error_response("Penalty not found", 404)

        if penalty.is_waived:
            return error_response("Penalty already waived", 400)

        # Waive penalty
        penalty.is_waived = True
        penalty.waived_by = g.user_id
        penalty.waived_at = datetime.now(pytz.timezone('Asia/Tashkent'))
        penalty.waive_reason = data.get('reason')

        db.commit()
        db.refresh(penalty)

        logger.info(f"Penalty waived: {penalty_id} by {g.user_id}")

        return success_response(penalty.to_dict(), "Penalty waived successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error waiving penalty: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@penalty_bp.route('/<penalty_id>/restore', methods=['POST'])
@require_auth
@load_company_context
def restore_penalty(penalty_id):
    """Bekor qilingan jarimani qayta tiklash"""
    db = get_db()

    try:
        penalty = db.query(Penalty).filter_by(
            id=penalty_id,
            company_id=g.company_id
        ).first()

        if not penalty:
            return error_response("Penalty not found", 404)

        if not penalty.is_waived:
            return error_response("Penalty is not waived", 400)

        # Restore penalty
        penalty.is_waived = False
        penalty.waived_by = None
        penalty.waived_at = None
        penalty.waive_reason = None

        db.commit()
        db.refresh(penalty)

        logger.info(f"Penalty restored: {penalty_id}")

        return success_response(penalty.to_dict(), "Penalty restored successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error restoring penalty: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@penalty_bp.route('/<penalty_id>/excuse', methods=['POST'])
@require_auth
@load_company_context
def excuse_penalty(penalty_id):
    """
    Jarimani sababli deb belgilash (jarima hisoblanmaydi)

    Body: {
        "reason": "Sababli kechikish - oilaviy vaziyat"
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        if not data.get('reason'):
            return error_response("Excuse reason is required", 400)

        penalty = db.query(Penalty).filter_by(
            id=penalty_id,
            company_id=g.company_id
        ).first()

        if not penalty:
            return error_response("Penalty not found", 404)

        if penalty.is_excused:
            return error_response("Penalty already excused", 400)

        # Excuse penalty
        penalty.is_excused = True
        penalty.excuse_reason = data.get('reason')
        penalty.excused_by = g.user_id
        penalty.excused_at = datetime.now(pytz.timezone('Asia/Tashkent'))

        db.commit()
        db.refresh(penalty)

        logger.info(f"Penalty excused: {penalty_id} by {g.user_id}, reason: {data.get('reason')}")

        return success_response(penalty.to_dict(), "Penalty excused successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error excusing penalty: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@penalty_bp.route('/<penalty_id>/unexcuse', methods=['POST'])
@require_auth
@load_company_context
def unexcuse_penalty(penalty_id):
    """Sababli belgisini olib tashlash"""
    db = get_db()

    try:
        penalty = db.query(Penalty).filter_by(
            id=penalty_id,
            company_id=g.company_id
        ).first()

        if not penalty:
            return error_response("Penalty not found", 404)

        if not penalty.is_excused:
            return error_response("Penalty is not excused", 400)

        # Remove excuse
        penalty.is_excused = False
        penalty.excuse_reason = None
        penalty.excused_by = None
        penalty.excused_at = None

        db.commit()
        db.refresh(penalty)

        logger.info(f"Penalty unexcused: {penalty_id}")

        return success_response(penalty.to_dict(), "Excuse removed successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error unexcusing penalty: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@penalty_bp.route('/bulk-excuse', methods=['POST'])
@require_auth
@load_company_context
def bulk_excuse_penalties():
    """
    Bir nechta xodim uchun ma'lum sanada jarimalarni sababli qilish

    Body: {
        "date": "2025-01-15",
        "employee_ids": ["id1", "id2", "id3"],
        "reason": "Yomon ob-havo sharoiti"
    }

    yoki

    Body: {
        "penalty_ids": ["penalty_id1", "penalty_id2"],
        "reason": "Sababi"
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        if not data.get('reason'):
            return error_response("Reason is required", 400)

        reason = data.get('reason')
        excused_penalties = []

        # Option 1: By date and employee IDs
        if data.get('date') and data.get('employee_ids'):
            penalty_date = datetime.strptime(data.get('date'), '%Y-%m-%d').date()
            employee_ids = data.get('employee_ids')

            if not isinstance(employee_ids, list):
                return error_response("employee_ids must be an array", 400)

            # Find penalties for these employees on this date
            penalties = db.query(Penalty).filter(
                Penalty.company_id == g.company_id,
                Penalty.employee_id.in_(employee_ids),
                Penalty.date == penalty_date,
                Penalty.is_excused == False
            ).all()

        # Option 2: By penalty IDs
        elif data.get('penalty_ids'):
            penalty_ids = data.get('penalty_ids')

            if not isinstance(penalty_ids, list):
                return error_response("penalty_ids must be an array", 400)

            penalties = db.query(Penalty).filter(
                Penalty.company_id == g.company_id,
                Penalty.id.in_(penalty_ids),
                Penalty.is_excused == False
            ).all()

        else:
            return error_response("Either (date + employee_ids) or penalty_ids is required", 400)

        if not penalties:
            return error_response("No penalties found to excuse", 404)

        # Excuse all penalties
        for penalty in penalties:
            penalty.is_excused = True
            penalty.excuse_reason = reason
            penalty.excused_by = g.user_id
            penalty.excused_at = datetime.now(pytz.timezone('Asia/Tashkent'))
            excused_penalties.append(penalty)

        db.commit()

        for penalty in excused_penalties:
            db.refresh(penalty)

        logger.info(f"Bulk excuse: {len(excused_penalties)} penalties excused by {g.user_id}")

        return success_response({
            'excused_count': len(excused_penalties),
            'penalties': [p.to_dict() for p in excused_penalties]
        }, f"{len(excused_penalties)} penalties excused successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error bulk excusing penalties: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@penalty_bp.route('/bulk-waive', methods=['POST'])
@require_auth
@load_company_context
def bulk_waive_penalties():
    """
    Bir nechta jarimani bekor qilish

    Body: {
        "penalty_ids": ["id1", "id2", "id3"],
        "reason": "Sababi"
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        penalty_ids = data.get('penalty_ids')
        if not penalty_ids or not isinstance(penalty_ids, list):
            return error_response("penalty_ids array is required", 400)

        reason = data.get('reason')

        # Find penalties
        penalties = db.query(Penalty).filter(
            Penalty.company_id == g.company_id,
            Penalty.id.in_(penalty_ids),
            Penalty.is_waived == False
        ).all()

        if not penalties:
            return error_response("No penalties found to waive", 404)

        waived_penalties = []

        # Waive all penalties
        for penalty in penalties:
            penalty.is_waived = True
            penalty.waived_by = g.user_id
            penalty.waived_at = datetime.now(pytz.timezone('Asia/Tashkent'))
            penalty.waive_reason = reason
            waived_penalties.append(penalty)

        db.commit()

        for penalty in waived_penalties:
            db.refresh(penalty)

        logger.info(f"Bulk waive: {len(waived_penalties)} penalties waived by {g.user_id}")

        return success_response({
            'waived_count': len(waived_penalties),
            'penalties': [p.to_dict() for p in waived_penalties]
        }, f"{len(waived_penalties)} penalties waived successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error bulk waiving penalties: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@penalty_bp.route('/employee/<employee_id>/summary', methods=['GET'])
@require_auth
@load_company_context
def get_employee_penalty_summary(employee_id):
    """
    Xodimning jarima xulosasi

    Query params:
    - start_date, end_date

    Response: {
        "total_penalties": 10,
        "total_amount": 500000,
        "waived_count": 2,
        "waived_amount": 100000,
        "excused_count": 1,
        "excused_amount": 50000,
        "active_penalties": 7,
        "active_amount": 350000
    }
    """
    db = get_db()

    try:
        # Verify employee
        employee = db.query(Employee).filter_by(
            id=employee_id,
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        # Base query
        query = db.query(Penalty).filter_by(
            company_id=g.company_id,
            employee_id=employee_id
        )

        # Date filters
        start_date = request.args.get('start_date')
        if start_date:
            query = query.filter(Penalty.date >= start_date)

        end_date = request.args.get('end_date')
        if end_date:
            query = query.filter(Penalty.date <= end_date)

        all_penalties = query.all()

        # Calculate summary
        total_penalties = len(all_penalties)
        total_amount = sum(p.amount for p in all_penalties)

        waived_penalties = [p for p in all_penalties if p.is_waived]
        waived_count = len(waived_penalties)
        waived_amount = sum(p.amount for p in waived_penalties)

        excused_penalties = [p for p in all_penalties if p.is_excused]
        excused_count = len(excused_penalties)
        excused_amount = sum(p.amount for p in excused_penalties)

        active_penalties = [p for p in all_penalties if not p.is_waived and not p.is_excused]
        active_count = len(active_penalties)
        active_amount = sum(p.amount for p in active_penalties)

        return success_response({
            'employee': {
                'id': employee.id,
                'full_name': employee.full_name,
                'employee_no': employee.employee_no
            },
            'total_penalties': total_penalties,
            'total_amount': total_amount,
            'waived_count': waived_count,
            'waived_amount': waived_amount,
            'excused_count': excused_count,
            'excused_amount': excused_amount,
            'active_penalties': active_count,
            'active_amount': active_amount
        })

    except Exception as e:
        logger.error(f"Error getting penalty summary: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@penalty_bp.route('/create', methods=['POST'])
@require_auth
@load_company_context
def create_manual_penalty():
    """
    Qo'lda jarima yaratish

    Body: {
        "employee_id": "...",
        "penalty_type": "manual",
        "amount": 50000,
        "date": "2025-01-15",
        "reason": "Sababi"
    }
    """
    db = get_db()

    try:
        data = request.get_json()

        # Validate required fields
        required = ['employee_id', 'amount', 'date']
        missing = [f for f in required if not data.get(f)]
        if missing:
            return error_response(f"Missing required fields: {', '.join(missing)}", 400)

        # Verify employee
        employee = db.query(Employee).filter_by(
            id=data['employee_id'],
            company_id=g.company_id
        ).first()

        if not employee:
            return error_response("Employee not found", 404)

        # Parse date
        penalty_date = datetime.strptime(data['date'], '%Y-%m-%d').date()

        # Create penalty
        penalty = Penalty(
            company_id=g.company_id,
            employee_id=data['employee_id'],
            penalty_type='manual',
            date=penalty_date,
            amount=float(data['amount']),
            reason=data.get('reason'),
            late_minutes=0
        )

        db.add(penalty)
        db.commit()
        db.refresh(penalty)

        logger.info(f"Manual penalty created: {penalty.id} for employee {employee.employee_no}")

        return success_response(penalty.to_dict(), "Penalty created successfully", 201)

    except Exception as e:
        db.rollback()
        logger.error(f"Error creating penalty: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()


@penalty_bp.route('/<penalty_id>', methods=['DELETE'])
@require_auth
@load_company_context
def delete_penalty(penalty_id):
    """Jarimani o'chirish (faqat manual penalties)"""
    db = get_db()

    try:
        penalty = db.query(Penalty).filter_by(
            id=penalty_id,
            company_id=g.company_id
        ).first()

        if not penalty:
            return error_response("Penalty not found", 404)

        # Only allow deleting manual penalties
        if penalty.penalty_type != 'manual':
            return error_response("Only manual penalties can be deleted", 400)

        db.delete(penalty)
        db.commit()

        logger.info(f"Penalty deleted: {penalty_id}")

        return success_response(None, "Penalty deleted successfully")

    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting penalty: {str(e)}", exc_info=True)
        return error_response(str(e), 500)
    finally:
        db.close()