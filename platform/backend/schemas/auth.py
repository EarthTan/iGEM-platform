from __future__ import annotations
from pydantic import BaseModel, EmailStr, field_validator
from typing import Literal
import uuid
from datetime import datetime


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class JobConfig(BaseModel):
    function_type: Literal["antioxidant", "antimicrobial", "antiglycation"]
    tier: Literal["A", "B", "C"]
    top_n: int = 10000
    linkers: list[str] = ["Flex_GGGGSx2"]
    structure_tool: Literal["omegafold", "esmfold"] = "omegafold"
    weights: dict[str, float] | None = None
    thresholds: dict[str, float] | None = None
    round_splits: dict[str, float] | None = None

    @field_validator("top_n")
    @classmethod
    def validate_top_n(cls, v: int) -> int:
        if not (1000 <= v <= 100000):
            raise ValueError("top_n must be between 1000 and 100000")
        return v

    @field_validator("linkers")
    @classmethod
    def validate_linkers(cls, v: list[str]) -> list[str]:
        valid = {
            "Flex_GGGGSx1", "Flex_GGGGSx2", "Flex_GGGGSx3",
            "Rigid_EAAAKx1", "Rigid_EAAAKx2", "Helix_AEAAAKEAAAKA",
            "PAS_linker", "Silk_like_GS", "Gly_rich_GPG", "Pro_rich_PPP",
        }
        for linker in v:
            if linker not in valid:
                raise ValueError(f"Unknown linker: {linker}")
        return v

    @field_validator("weights", "thresholds", "round_splits")
    @classmethod
    def validate_nonnegative(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        for key, val in v.items():
            if val < 0:
                raise ValueError(f"{key} must be non-negative")
        return v
