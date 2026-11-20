from pydantic import BaseModel, Field


class MedianRunInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    point_ids: list[int] = Field(min_length=1, max_length=20)


class PointUpdateInput(BaseModel):
    mode: str = Field(pattern='^(regenerate|set)$')
    n: int | None = Field(default=None, ge=1, le=5000)
    values: list[float] | None = Field(default=None, max_length=5000)


class PointOnlineInput(BaseModel):
    online: bool


class HeartbeatInput(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=2, max_length=120)
    version: int = Field(ge=0)
    count: int = Field(ge=0)
