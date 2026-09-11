"""Validated request payloads for the public HTTP API."""

from typing import Optional

from pydantic import BaseModel, Field


class TrackRequest(BaseModel):
    appid: int = Field(gt=0)
    name: Optional[str] = Field(default=None, max_length=300)
    header_image: Optional[str] = Field(default=None, max_length=2048)
    tiny_image: Optional[str] = Field(default=None, max_length=2048)


class UntrackRequest(BaseModel):
    appid: int = Field(gt=0)

class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=128)
    remember: bool = False
