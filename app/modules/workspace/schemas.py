from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Category = Literal["PALETTE", "LIGHTING", "COMPOSITION", "MATERIAL", "MOOD", "TYPOGRAPHY", "SUBJECT", "OTHER"]

class Input(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def optional_text(cls, value, info):
        field = cls.model_fields[info.field_name]
        if isinstance(value, str) and not value.strip() and field.default is None:
            return None
        return value

class Requirement(Input):
    id: UUID = Field(default_factory=uuid4)
    category: Category
    label: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=1000)
    hard: bool

class Palette(Input):
    name: str = Field(min_length=1)
    hex: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    role: str | None = None

class Lighting(Input):
    quality: str | None = None
    direction: str | None = None
    temperature: str | None = None
    notes: str | None = None

class Composition(Input):
    framing: str | None = None
    layout: str | None = None
    notes: str | None = None

class Typography(Input):
    families: list[str] = Field(default_factory=list)
    notes: str | None = None

class BriefInput(Input):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1)
    targetAudience: str | None = None
    deliverables: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    forbiddenAttributes: list[Requirement] = Field(default_factory=list)
    acceptanceCriteria: list[str] = Field(default_factory=list)
    sourceText: str | None = None
    changeSummary: str | None = None

class RevisionInput(Input):
    summary: str = Field(min_length=1)
    palette: list[Palette] = Field(default_factory=list)
    lighting: Lighting = Field(default_factory=Lighting)
    composition: Composition = Field(default_factory=Composition)
    materials: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    typography: Typography = Field(default_factory=Typography)
    requiredAttributes: list[Requirement] = Field(default_factory=list)
    forbiddenAttributes: list[Requirement] = Field(default_factory=list)
    stylePrompt: str | None = None
    changeSummary: str | None = None

class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    template: Literal["BLANK", "DEMO_SOLARA"]

class ProjectPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    expectedUpdatedAt: datetime

    @model_validator(mode="after")
    def valid_name(self):
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self

class DirectionInput(Input):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    revision: RevisionInput | None = None
    cloneFromDirectionRevisionId: UUID | None = None

    @model_validator(mode="after")
    def require_revision(self):
        if not self.revision and not self.cloneFromDirectionRevisionId:
            raise ValueError("revision or cloneFromDirectionRevisionId is required")
        return self

class DirectionPatch(ProjectPatch):
    status: Literal["DRAFT", "ARCHIVED"] | None = None

class ActivateInput(Input):
    directionRevisionId: UUID
    reason: str = Field(min_length=3, max_length=1000)

class UserSummary(BaseModel):
    id: UUID
    displayName: str

class ProjectDTO(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None
    status: Literal["ACTIVE", "ARCHIVED"]
    owner: UserSummary
    currentUserRole: Literal["OWNER", "EDITOR", "REVIEWER", "VIEWER"]
    activeDirectionRevisionId: UUID | None
    createdAt: datetime
    updatedAt: datetime
    archivedAt: datetime | None

class BriefDTO(BriefInput):
    id: UUID
    projectId: UUID
    revisionNumber: int
    createdBy: UserSummary
    createdAt: datetime

class RevisionDTO(RevisionInput):
    id: UUID
    directionId: UUID
    revisionNumber: int
    createdBy: UserSummary
    createdAt: datetime

class DirectionDTO(BaseModel):
    id: UUID
    projectId: UUID
    name: str
    description: str | None
    status: Literal["DRAFT", "ACTIVE", "SUPERSEDED", "ARCHIVED"]
    isActive: bool
    latestRevision: RevisionDTO
    revisionCount: int
    createdBy: UserSummary
    createdAt: datetime
    updatedAt: datetime
    archivedAt: datetime | None

class ProjectResponse(BaseModel):
    data: ProjectDTO

class BriefResponse(BaseModel):
    data: BriefDTO

class RevisionResponse(BaseModel):
    data: RevisionDTO

class DirectionResponse(BaseModel):
    data: DirectionDTO

class Page(BaseModel):
    limit: int
    nextCursor: str | None

class ProjectList(BaseModel):
    data: list[ProjectDTO]
    page: Page

class BriefList(BaseModel):
    data: list[BriefDTO]
    page: Page

class DirectionList(BaseModel):
    data: list[DirectionDTO]
    page: Page

class RevisionList(BaseModel):
    data: list[RevisionDTO]
    page: Page
