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
