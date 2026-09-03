from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class CurrentUser(BaseModel):
    id: str
    email: str
    full_name: str
    is_admin: bool

    model_config = {"from_attributes": True}
