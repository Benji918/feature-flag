"""Pydantic shapes for project endpoints.

The raw machine key appears in exactly one shape: ProjectCreated, returned
once by POST /projects. Every other shape is key-free by construction --
there is no field for it to leak through.
"""

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    name: str
    repo_url: str | None = None


class ProjectCreated(BaseModel):
    id: int
    name: str
    repo_url: str | None
    api_key: str


class ProjectSummary(BaseModel):
    id: int
    name: str
    repo_url: str | None
    created_at: str


class FlagOut(BaseModel):
    key: str
    description: str
    enabled: bool
    rollout_percentage: int
    default_value: bool
    created_at: str
    definition_updated_at: str


class ProjectDetail(BaseModel):
    id: int
    name: str
    repo_url: str | None
    created_at: str
    flags: list[FlagOut]
