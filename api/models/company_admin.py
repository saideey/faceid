from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
import enum
from database import Base, get_tashkent_time


class CompanyAdminRole(enum.Enum):
    owner = 'owner'
    admin = 'admin'
    manager = 'manager'


class CompanyAdmin(Base):
    __tablename__ = 'company_admins'
    __table_args__ = (
        Index('ix_company_admin_email', 'company_id', 'email', unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey('companies.id', ondelete='CASCADE'), nullable=False)
    email = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255))
    role = Column(SQLEnum(CompanyAdminRole), default=CompanyAdminRole.admin, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_tashkent_time)

    # Relationships
    company = relationship("Company", back_populates="admins")

    def to_dict(self):
        return {
            'id': str(self.id),
            'company_id': str(self.company_id),
            'email': self.email,
            'full_name': self.full_name,
            'role': self.role.value if self.role else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    def __repr__(self):
        return f"<CompanyAdmin {self.email}>"