from sqlalchemy import Column, String, Integer, Text, Numeric, DateTime, Date, ForeignKey, Index, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
import enum
from database import Base, get_tashkent_time


class PenaltyType(enum.Enum):
    late = 'late'
    early_leave = 'early_leave'
    absence = 'absence'
    manual = 'manual'


class Penalty(Base):
    __tablename__ = 'penalties'
    __table_args__ = (
        Index('ix_penalty_employee_date', 'employee_id', 'date'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey('employees.id', ondelete='CASCADE'), nullable=False)
    attendance_log_id = Column(UUID(as_uuid=True), ForeignKey('attendance_logs.id', ondelete='SET NULL'), nullable=True)
    penalty_type = Column(SQLEnum(PenaltyType), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    late_minutes = Column(Integer, default=0)
    reason = Column(Text)
    date = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)

    # Relationships
    employee = relationship("Employee", back_populates="penalties")
    attendance_log = relationship("AttendanceLog", back_populates="penalties")

    def to_dict(self):
        return {
            'id': str(self.id),
            'company_id': str(self.company_id),
            'employee_id': str(self.employee_id),
            'attendance_log_id': str(self.attendance_log_id) if self.attendance_log_id else None,
            'penalty_type': self.penalty_type.value if self.penalty_type else None,
            'amount': float(self.amount) if self.amount else 0,
            'late_minutes': self.late_minutes,
            'reason': self.reason,
            'date': self.date.isoformat() if self.date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    def __repr__(self):
        return f"<Penalty {self.penalty_type.value} - {self.amount}>"