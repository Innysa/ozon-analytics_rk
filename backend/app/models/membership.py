import enum

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class StoreRole(str, enum.Enum):
    OWNER = "owner"
    MANAGER = "manager"
    VIEWER = "viewer"


class StoreMembership(TimestampMixin, Base):
    """Grants a user a role on a specific store. Platform admins bypass this
    table entirely (see User.is_admin) but can still hold explicit rows if
    desired for clarity in the UI."""

    __tablename__ = "store_memberships"
    __table_args__ = (UniqueConstraint("user_id", "store_id", name="uq_membership_user_store"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[StoreRole] = mapped_column(Enum(StoreRole, name="store_role"), nullable=False)

    user = relationship("User", back_populates="memberships")
    store = relationship("Store", back_populates="memberships")
