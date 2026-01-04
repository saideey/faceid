from sqlalchemy import Column, String, Integer, DateTime, Time, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
import enum
from datetime import datetime
from database import Base, get_tashkent_time


class EmployeeStatus(enum.Enum):
    active = 'active'
    inactive = 'inactive'
    suspended = 'suspended'


class Employee(Base):
    __tablename__ = 'employees'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey('companies.id', ondelete='CASCADE'), nullable=False, index=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey('departments.id', ondelete='SET NULL'), nullable=True)
    employee_no = Column(String(100), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    card_no = Column(String(100))
    position = Column(String(255))
    phone = Column(String(50))
    email = Column(String(255))
    photo_url = Column(String(500))
    user_type = Column(String(50), default='normal')
    status = Column(SQLEnum(EmployeeStatus), default=EmployeeStatus.active, nullable=False)
    work_start_time = Column(Time, default=datetime.strptime('09:00:00', '%H:%M:%S').time())
    work_end_time = Column(Time, default=datetime.strptime('18:00:00', '%H:%M:%S').time())
    lunch_break_duration = Column(Integer, default=60)  # minutes
    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)
    updated_at = Column(DateTime(timezone=True), default=get_tashkent_time, onupdate=get_tashkent_time)

    # Relationships
    company = relationship("Company", back_populates="employees")
    department = relationship("Department", back_populates="employees")
    attendance_logs = relationship("AttendanceLog", back_populates="employee", cascade="all, delete-orphan")
    penalties = relationship("Penalty", back_populates="employee", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': str(self.id),
            'company_id': str(self.company_id),
            'department_id': str(self.department_id) if self.department_id else None,
            'employee_no': self.employee_no,
            'full_name': self.full_name,
            'card_no': self.card_no,
            'position': self.position,
            'phone': self.phone,
            'email': self.email,
            'photo_url': self.photo_url,
            'user_type': self.user_type,
            'status': self.status.value if self.status else None,
            'work_start_time': self.work_start_time.strftime('%H:%M:%S') if self.work_start_time else None,
            'work_end_time': self.work_end_time.strftime('%H:%M:%S') if self.work_end_time else None,
            'lunch_break_duration': self.lunch_break_duration,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def __repr__(self):
        return f"<Employee {self.employee_no} - {self.full_name}>"