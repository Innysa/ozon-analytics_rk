from pydantic import BaseModel, EmailStr, Field


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    is_admin: bool
    is_active: bool

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str = Field(min_length=8, max_length=200)
    is_admin: bool = False


class UserUpdate(BaseModel):
    full_name: str | None = None
    is_admin: bool | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)
