from sqlalchemy import Column, String, Integer, Numeric, DateTime, Time, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from datetime import datetime
from database import Base, get_tashkent_time


class CompanySettings(Base):
    __tablename__ = 'company_settings'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey('companies.id', ondelete='CASCADE'), unique=True, nullable=False)
    default_work_start = Column(Time, default=datetime.strptime('09:00:00', '%H:%M:%S').time())
    default_work_end = Column(Time, default=datetime.strptime('18:00:00', '%H:%M:%S').time())
    penalty_per_minute = Column(Numeric(10, 2), default=0.00)
    grace_period_minutes = Column(Integer, default=15)
    timezone = Column(String(100), default='Asia/Tashkent')
    currency = Column(String(10), default='UZS')
    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)
    updated_at = Column(DateTime(timezone=True), default=get_tashkent_time, onupdate=get_tashkent_time)

    # Relationships
    company = relationship("Company", back_populates="settings")

    def to_dict(self):
        return {
            'id': str(self.id),
            'company_id': str(self.company_id),
            'default_work_start': self.default_work_start.strftime('%H:%M:%S') if self.default_work_start else None,
            'default_work_end': self.default_work_end.strftime('%H:%M:%S') if self.default_work_end else None,
            'penalty_per_minute': float(self.penalty_per_minute) if self.penalty_per_minute else 0,
            'grace_period_minutes': self.grace_period_minutes,
            'timezone': self.timezone,
            'currency': self.currency,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def __repr__(self):
        return f"<CompanySettings for {self.company_id}>"