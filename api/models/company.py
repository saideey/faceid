from sqlalchemy import Column, String, Integer, DateTime, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
import enum
from database import Base, get_tashkent_time


class CompanyStatus(enum.Enum):
    active = 'active'
    suspended = 'suspended'
    inactive = 'inactive'


class Company(Base):
    __tablename__ = 'companies'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name = Column(String(255), nullable=False)
    subdomain = Column(String(100), unique=True, index=True)
    logo_url = Column(String(500))
    status = Column(SQLEnum(CompanyStatus), default=CompanyStatus.active, nullable=False)
    max_employees = Column(Integer, default=100)
    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)
    updated_at = Column(DateTime(timezone=True), default=get_tashkent_time, onupdate=get_tashkent_time)

    # Relationships
    admins = relationship("CompanyAdmin", back_populates="company", cascade="all, delete-orphan")
    departments = relationship("Department", back_populates="company", cascade="all, delete-orphan")
    employees = relationship("Employee", back_populates="company", cascade="all, delete-orphan")
    settings = relationship("CompanySettings", back_populates="company", uselist=False, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': str(self.id),
            'company_name': self.company_name,
            'subdomain': self.subdomain,
            'logo_url': self.logo_url,
            'status': self.status.value if self.status else None,
            'max_employees': self.max_employees,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def __repr__(self):
        return f"<Company {self.company_name}>"