from sqlalchemy import Column, String, Integer, DateTime, Date, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from database import Base, get_tashkent_time


class AttendanceLog(Base):
    __tablename__ = 'attendance_logs'
    __table_args__ = (
        Index('ix_attendance_employee_date', 'employee_id', 'date'),
        Index('ix_attendance_company_date', 'company_id', 'date'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey('employees.id', ondelete='CASCADE'), nullable=False)
    employee_no = Column(String(100), nullable=False)
    device_name = Column(String(255))
    ip_address = Column(String(50))
    event_type = Column(String(50))
    verify_mode = Column(String(50))
    attendance_status = Column(String(50))
    check_in_time = Column(DateTime(timezone=True))
    check_out_time = Column(DateTime(timezone=True))
    late_minutes = Column(Integer, default=0)
    early_leave_minutes = Column(Integer, default=0)
    total_work_minutes = Column(Integer, default=0)
    overtime_minutes = Column(Integer, default=0)
    date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)

    # Relationships
    employee = relationship("Employee", back_populates="attendance_logs")
    penalties = relationship("Penalty", back_populates="attendance_log")

    def to_dict(self):
        return {
            'id': str(self.id),
            'company_id': str(self.company_id),
            'employee_id': str(self.employee_id),
            'employee_no': self.employee_no,
            'device_name': self.device_name,
            'ip_address': self.ip_address,
            'event_type': self.event_type,
            'verify_mode': self.verify_mode,
            'attendance_status': self.attendance_status,
            'check_in_time': self.check_in_time.isoformat() if self.check_in_time else None,
            'check_out_time': self.check_out_time.isoformat() if self.check_out_time else None,
            'late_minutes': self.late_minutes,
            'early_leave_minutes': self.early_leave_minutes,
            'total_work_minutes': self.total_work_minutes,
            'overtime_minutes': self.overtime_minutes,
            'date': self.date.isoformat() if self.date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    def __repr__(self):
        return f"<AttendanceLog {self.employee_no} - {self.date}>"