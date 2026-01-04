from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, Date, DateTime, Time, Text, ForeignKey, \
    Index
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, scoped_session
from datetime import datetime, timedelta
import datetime as dt
import pytz
import os
import uuid

# Database URL
DATABASE_URL = os.getenv(
    'DATABASE_URL',
    'postgresql://attendance_user:attendance_pass_2025@localhost:5432/attendance_system_db'
)

# Create engine
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)

# Create session
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

Base = declarative_base()


def get_tashkent_time():
    """Get current time in Tashkent timezone"""
    tashkent_tz = pytz.timezone('Asia/Tashkent')
    return datetime.now(tashkent_tz)


def get_db():
    """Get database session"""
    return SessionLocal()


# ========================================
# PostgreSQL ENUM Types
# ========================================

# Create PostgreSQL ENUM types
status_enum = ENUM('active', 'inactive', 'suspended', name='statusenum', create_type=False)
penalty_type_enum = ENUM('late', 'early_leave', 'absence', 'manual', name='penaltytypeenum', create_type=False)
bonus_type_enum = ENUM('perfect_attendance', 'early_arrival', 'overtime', 'manual', name='bonustypeenum',
                       create_type=False)
salary_type_enum = ENUM('monthly', 'daily', name='salarytypeenum', create_type=False)


# ========================================
# MODELS
# ========================================

class SuperAdmin(Base):
    """Super Admin model - can manage all companies"""
    __tablename__ = 'super_admins'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)

    def to_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'full_name': self.full_name,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Company(Base):
    """Company model - multi-tenant support"""
    __tablename__ = 'companies'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_name = Column(String(255), nullable=False)
    subdomain = Column(String(100), unique=True, nullable=False)
    logo_url = Column(String(500))
    max_employees = Column(Integer, default=100)
    status = Column(status_enum, default='active')
    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)
    updated_at = Column(DateTime(timezone=True), default=get_tashkent_time, onupdate=get_tashkent_time)

    # Relationships
    admins = relationship("CompanyAdmin", back_populates="company", cascade="all, delete-orphan")
    settings = relationship("CompanySettings", back_populates="company", uselist=False, cascade="all, delete-orphan")
    branches = relationship("Branch", back_populates="company", cascade="all, delete-orphan")
    departments = relationship("Department", back_populates="company", cascade="all, delete-orphan")
    employees = relationship("Employee", back_populates="company", cascade="all, delete-orphan")
    attendance_logs = relationship("AttendanceLog", back_populates="company")
    penalties = relationship("Penalty", back_populates="company")
    bonuses = relationship("Bonus", back_populates="company")

    def to_dict(self):
        return {
            'id': self.id,
            'company_name': self.company_name,
            'subdomain': self.subdomain,
            'logo_url': self.logo_url,
            'max_employees': self.max_employees,
            'status': self.status,
            'employee_count': len(self.employees) if self.employees else 0,
            'branch_count': len(self.branches) if self.branches else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class CompanyAdmin(Base):
    """Company Admin model - one admin per company"""
    __tablename__ = 'company_admins'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey('companies.id'), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)

    # Relationships
    company = relationship("Company", back_populates="admins")

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'email': self.email,
            'full_name': self.full_name,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class CompanySettings(Base):
    """Company Settings model - work hours, penalties, etc"""
    __tablename__ = 'company_settings'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey('companies.id'), unique=True, nullable=False)

    # Work hours
    work_start_time = Column(String(8), default='09:00')
    work_end_time = Column(String(8), default='18:00')
    lunch_break_minutes = Column(Integer, default=60)
    late_threshold_minutes = Column(Integer, default=10)
    overtime_threshold_minutes = Column(Integer, default=30)

    # Legacy fields (keep for backward compatibility)
    default_work_start = Column(Time, default=dt.time(9, 0))
    default_work_end = Column(Time, default=dt.time(18, 0))
    grace_period_minutes = Column(Integer, default=15)

    # Penalty settings
    auto_penalty_enabled = Column(Boolean, default=False)
    late_penalty_per_minute = Column(Float, default=1000.0)
    absence_penalty_amount = Column(Float, default=50000.0)
    penalty_per_minute = Column(Float, default=0.0)  # Legacy field

    currency = Column(String(10), default='UZS')

    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)
    updated_at = Column(DateTime(timezone=True), default=get_tashkent_time, onupdate=get_tashkent_time)

    # Relationships
    company = relationship("Company", back_populates="settings")

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'work_start_time': self.work_start_time,
            'work_end_time': self.work_end_time,
            'lunch_break_minutes': self.lunch_break_minutes,
            'late_threshold_minutes': self.late_threshold_minutes,
            'overtime_threshold_minutes': self.overtime_threshold_minutes,
            'auto_penalty_enabled': self.auto_penalty_enabled,
            'late_penalty_per_minute': self.late_penalty_per_minute,
            'absence_penalty_amount': self.absence_penalty_amount,
            'default_work_start': str(self.default_work_start) if self.default_work_start else None,
            'default_work_end': str(self.default_work_end) if self.default_work_end else None,
            'grace_period_minutes': self.grace_period_minutes,
            'penalty_per_minute': self.penalty_per_minute,
            'currency': self.currency,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class Branch(Base):
    """Branch (Filial) model - each company can have multiple branches"""
    __tablename__ = 'branches'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey('companies.id'), nullable=False)
    name = Column(String(255), nullable=False)
    code = Column(String(50))
    address = Column(Text)
    phone = Column(String(20))
    manager_name = Column(String(255))
    status = Column(status_enum, default='active')
    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)
    updated_at = Column(DateTime(timezone=True), default=get_tashkent_time, onupdate=get_tashkent_time)

    # Indexes
    __table_args__ = (
        Index('idx_branch_company', 'company_id'),
        Index('idx_branch_status', 'status'),
    )

    # Relationships
    company = relationship("Company", back_populates="branches")
    employees = relationship("Employee", back_populates="branch", cascade="all, delete-orphan")
    attendance_logs = relationship("AttendanceLog", back_populates="branch")

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'name': self.name,
            'code': self.code,
            'address': self.address,
            'phone': self.phone,
            'manager_name': self.manager_name,
            'status': self.status,
            'employee_count': len(self.employees) if self.employees else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class Department(Base):
    """Department model"""
    __tablename__ = 'departments'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey('companies.id'), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)
    updated_at = Column(DateTime(timezone=True), default=get_tashkent_time, onupdate=get_tashkent_time)

    # Indexes
    __table_args__ = (
        Index('idx_department_company', 'company_id'),
    )

    # Relationships
    company = relationship("Company", back_populates="departments")
    employees = relationship("Employee", back_populates="department")

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'name': self.name,
            'description': self.description,
            'employee_count': len(self.employees) if self.employees else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class Employee(Base):
    """Employee model - belongs to company and branch"""
    __tablename__ = 'employees'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey('companies.id'), nullable=False)
    branch_id = Column(String(36), ForeignKey('branches.id'), nullable=True)
    department_id = Column(String(36), ForeignKey('departments.id'), nullable=True)

    employee_no = Column(String(50), nullable=False)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255))
    phone = Column(String(20))
    photo_url = Column(String(500))
    card_no = Column(String(50))

    position = Column(String(255))
    hire_date = Column(Date)

    # Ish vaqti - DEFAULT vaqtlar (agar schedule bo'lmasa ishlatiladi)
    work_start_time = Column(Time, default=dt.time(9, 0))
    work_end_time = Column(Time, default=dt.time(18, 0))
    lunch_break_duration = Column(Integer, default=60)

    # YANGI - Oylik/maosh tizimi
    salary = Column(Float)  # Oylik yoki kunlik maosh
    salary_type = Column(salary_type_enum, default='monthly')  # 'monthly' yoki 'daily'

    status = Column(status_enum, default='active')

    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)
    updated_at = Column(DateTime(timezone=True), default=get_tashkent_time, onupdate=get_tashkent_time)

    # employee_no unique faqat company_id + branch_id scope'ida
    __table_args__ = (
        Index('idx_employee_company_branch_no', 'company_id', 'branch_id', 'employee_no', unique=True),
        Index('idx_employee_company', 'company_id'),
        Index('idx_employee_branch', 'branch_id'),
        Index('idx_employee_department', 'department_id'),
        Index('idx_employee_status', 'status'),
    )

    # Relationships
    company = relationship("Company", back_populates="employees")
    branch = relationship("Branch", back_populates="employees")
    department = relationship("Department", back_populates="employees")
    attendance_logs = relationship("AttendanceLog", back_populates="employee", cascade="all, delete-orphan")
    penalties = relationship("Penalty", back_populates="employee", cascade="all, delete-orphan")
    bonuses = relationship("Bonus", back_populates="employee", cascade="all, delete-orphan")
    schedules = relationship("EmployeeSchedule", back_populates="employee", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'branch_id': self.branch_id,
            'branch_name': self.branch.name if self.branch else None,
            'department_id': self.department_id,
            'department_name': self.department.name if self.department else None,
            'employee_no': self.employee_no,
            'full_name': self.full_name,
            'email': self.email,
            'phone': self.phone,
            'photo_url': self.photo_url,
            'card_no': self.card_no,
            'position': self.position,
            'hire_date': self.hire_date.isoformat() if self.hire_date else None,
            'work_start_time': str(self.work_start_time) if self.work_start_time else None,
            'work_end_time': str(self.work_end_time) if self.work_end_time else None,
            'lunch_break_duration': self.lunch_break_duration,
            'salary': self.salary,
            'salary_type': self.salary_type,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class EmployeeSchedule(Base):
    """Xodimning haftalik ish vaqti jadvali - har bir kun uchun alohida"""
    __tablename__ = 'employee_schedules'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id = Column(String(36), ForeignKey('employees.id'), nullable=False)

    # Hafta kuni (1=Dushanba, 2=Seshanba, ..., 7=Yakshanba)
    day_of_week = Column(Integer, nullable=False)  # 1-7

    # Ish vaqti
    work_start_time = Column(Time)
    work_end_time = Column(Time)

    # Dam olish kuni
    is_day_off = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)
    updated_at = Column(DateTime(timezone=True), default=get_tashkent_time, onupdate=get_tashkent_time)

    # Unique constraint: bir xodim uchun bir kun faqat bir marta
    __table_args__ = (
        Index('idx_schedule_employee_day', 'employee_id', 'day_of_week', unique=True),
    )

    # Relationships
    employee = relationship("Employee", back_populates="schedules")

    def to_dict(self):
        return {
            'id': self.id,
            'employee_id': self.employee_id,
            'day_of_week': self.day_of_week,
            'work_start_time': str(self.work_start_time) if self.work_start_time else None,
            'work_end_time': str(self.work_end_time) if self.work_end_time else None,
            'is_day_off': self.is_day_off,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class AttendanceLog(Base):
    """Attendance Log model - daily check-in/check-out records"""
    __tablename__ = 'attendance_logs'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey('companies.id'), nullable=False)
    branch_id = Column(String(36), ForeignKey('branches.id'), nullable=True)
    employee_id = Column(String(36), ForeignKey('employees.id'), nullable=False)
    employee_no = Column(String(50), nullable=False)

    date = Column(Date, nullable=False)
    check_in_time = Column(DateTime(timezone=True))
    check_out_time = Column(DateTime(timezone=True))

    late_minutes = Column(Integer, default=0)
    early_leave_minutes = Column(Integer, default=0)
    total_work_minutes = Column(Integer, default=0)
    overtime_minutes = Column(Integer, default=0)

    device_name = Column(String(255))
    ip_address = Column(String(50))
    verify_mode = Column(String(50))

    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)
    updated_at = Column(DateTime(timezone=True), default=get_tashkent_time, onupdate=get_tashkent_time)

    # Indexes for performance
    __table_args__ = (
        Index('idx_attendance_employee_date', 'employee_id', 'date', unique=True),
        Index('idx_attendance_company_date', 'company_id', 'date'),
        Index('idx_attendance_branch_date', 'branch_id', 'date'),
        Index('idx_attendance_date', 'date'),
    )

    # Relationships
    company = relationship("Company", back_populates="attendance_logs")
    branch = relationship("Branch", back_populates="attendance_logs")
    employee = relationship("Employee", back_populates="attendance_logs")

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'branch_id': self.branch_id,
            'branch_name': self.branch.name if self.branch else None,
            'employee_id': self.employee_id,
            'employee_no': self.employee_no,
            'employee_name': self.employee.full_name if self.employee else None,
            'department_name': self.employee.department.name if self.employee and self.employee.department else None,
            'date': self.date.isoformat() if self.date else None,
            'check_in_time': self.check_in_time.isoformat() if self.check_in_time else None,
            'check_out_time': self.check_out_time.isoformat() if self.check_out_time else None,
            'late_minutes': self.late_minutes,
            'early_leave_minutes': self.early_leave_minutes,
            'total_work_minutes': self.total_work_minutes,
            'overtime_minutes': self.overtime_minutes,
            'work_hours': round(self.total_work_minutes / 60, 2) if self.total_work_minutes else 0,
            'device_name': self.device_name,
            'ip_address': self.ip_address,
            'verify_mode': self.verify_mode,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class Penalty(Base):
    """Penalty model - late/absence penalties"""
    __tablename__ = 'penalties'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey('companies.id'), nullable=False)
    employee_id = Column(String(36), ForeignKey('employees.id'), nullable=False)
    attendance_log_id = Column(String(36), ForeignKey('attendance_logs.id'), nullable=True)

    penalty_type = Column(penalty_type_enum, nullable=False)
    date = Column(Date, nullable=False)
    late_minutes = Column(Integer, default=0)
    amount = Column(Float, nullable=False)
    reason = Column(Text)

    # Jarima bekor qilish (waive)
    is_waived = Column(Boolean, default=False)
    waived_by = Column(String(36))  # Admin user ID
    waived_at = Column(DateTime(timezone=True))
    waive_reason = Column(Text)

    # YANGI - Sababli kechikish (excused late)
    is_excused = Column(Boolean, default=False)  # Agar sababli bo'lsa jarima hisoblanmaydi
    excuse_reason = Column(Text)  # Sababi
    excused_by = Column(String(36))  # Kim sababli deb belgiladi
    excused_at = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)

    # Indexes
    __table_args__ = (
        Index('idx_penalty_employee_date', 'employee_id', 'date'),
        Index('idx_penalty_company_date', 'company_id', 'date'),
        Index('idx_penalty_waived', 'is_waived'),
        Index('idx_penalty_excused', 'is_excused'),
    )

    # Relationships
    company = relationship("Company", back_populates="penalties")
    employee = relationship("Employee", back_populates="penalties")

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'employee_id': self.employee_id,
            'employee_no': self.employee.employee_no if self.employee else None,
            'employee_name': self.employee.full_name if self.employee else None,
            'attendance_log_id': self.attendance_log_id,
            'penalty_type': self.penalty_type,
            'date': self.date.isoformat() if self.date else None,
            'late_minutes': self.late_minutes,
            'amount': self.amount,
            'reason': self.reason,
            'is_waived': self.is_waived,
            'waived_by': self.waived_by,
            'waived_at': self.waived_at.isoformat() if self.waived_at else None,
            'waive_reason': self.waive_reason,
            'is_excused': self.is_excused,
            'excuse_reason': self.excuse_reason,
            'excused_by': self.excused_by,
            'excused_at': self.excused_at.isoformat() if self.excused_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Bonus(Base):
    """Bonus model - xodim bonuslari"""
    __tablename__ = 'bonuses'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String(36), ForeignKey('companies.id'), nullable=False)
    employee_id = Column(String(36), ForeignKey('employees.id'), nullable=False)

    bonus_type = Column(bonus_type_enum, nullable=False)  # 'perfect_attendance', 'early_arrival', 'overtime', 'manual'
    amount = Column(Float, nullable=False)
    reason = Column(Text)
    date = Column(Date, nullable=False)

    # Kim berdi
    given_by = Column(String(36))  # Admin user ID
    given_at = Column(DateTime(timezone=True), default=get_tashkent_time)

    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)

    # Indexes
    __table_args__ = (
        Index('idx_bonus_employee_date', 'employee_id', 'date'),
        Index('idx_bonus_company_date', 'company_id', 'date'),
        Index('idx_bonus_type', 'bonus_type'),
    )

    # Relationships
    company = relationship("Company", back_populates="bonuses")
    employee = relationship("Employee", back_populates="bonuses")

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'employee_id': self.employee_id,
            'employee_no': self.employee.employee_no if self.employee else None,
            'employee_name': self.employee.full_name if self.employee else None,
            'bonus_type': self.bonus_type,
            'amount': self.amount,
            'reason': self.reason,
            'date': self.date.isoformat() if self.date else None,
            'given_by': self.given_by,
            'given_at': self.given_at.isoformat() if self.given_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


def create_enum_types():
    """Create PostgreSQL enum types if they don't exist"""
    from sqlalchemy import text

    with engine.connect() as conn:
        # Create statusenum
        conn.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'statusenum') THEN
                    CREATE TYPE statusenum AS ENUM ('active', 'inactive', 'suspended');
                END IF;
            END
            $$;
        """))

        # Create penaltytypeenum
        conn.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'penaltytypeenum') THEN
                    CREATE TYPE penaltytypeenum AS ENUM ('late', 'early_leave', 'absence', 'manual');
                END IF;
            END
            $$;
        """))

        # Create bonustypeenum - YANGI
        conn.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'bonustypeenum') THEN
                    CREATE TYPE bonustypeenum AS ENUM ('perfect_attendance', 'early_arrival', 'overtime', 'manual');
                END IF;
            END
            $$;
        """))

        # Create salarytypeenum - YANGI
        conn.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'salarytypeenum') THEN
                    CREATE TYPE salarytypeenum AS ENUM ('monthly', 'daily');
                END IF;
            END
            $$;
        """))

        conn.commit()

    print("✅ Enum types created successfully!")


def init_db():
    """Initialize database - create enum types and all tables"""
    print("Initializing database...")

    # Create enum types first
    create_enum_types()

    # Create all tables
    Base.metadata.create_all(bind=engine)

    print("✅ Database initialized successfully!")