from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    name: str = Field(max_length=100)
    email: EmailStr
    fitness_level: str = Field(
        default="intermediate", pattern=r"^(beginner|intermediate|advanced)$"
    )
    goals: str | None = None


class UserCreate(UserBase):
    pass


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None
    fitness_level: str | None = Field(
        default=None, pattern=r"^(beginner|intermediate|advanced)$"
    )
    goals: str | None = None


class UserRead(UserBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
