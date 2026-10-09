from datetime import datetime
from uuid import UUID
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from app.modules.workspace.schemas import Input, Category, UserSummary, Page
from app.modules.assets.schemas import VersionInput

SourceType = Literal["USER_UPLOAD", "PUBLIC_URL", "INTERNAL_ASSET", "DEMO_ASSET"]
TargetType = Literal["DIRECTION_REVISION", "ASSET", "ASSET_VERSION"]

class AttributeValue(Input):
    text: str = Field(min_length=1, max_length=2000)
    hex: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")

class AttributeInput(Input):
    attributeType: Category
    value: AttributeValue

class AttributePatch(Input):
    value: AttributeValue | None = None
    reviewStatus: Literal["CONFIRMED", "CORRECTED", "REJECTED"] | None = None

    @model_validator(mode="after")
    def non_null(self):
        for key in self.model_fields_set:
            if getattr(self, key) is None:
                raise ValueError(key + " cannot be null")
        return self

class LinkInput(Input):
    targetType: TargetType
    targetId: UUID
    relationshipType: str | None = Field(default=None, max_length=80)
    usageNote: str | None = None

class ReferenceInput(Input):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    sourceType: SourceType
    sourceUrl: str | None = None
    storageObjectId: UUID | None = None
    usageRightsNote: str | None = None
    tags: list[str] = Field(default_factory=list)
    attributes: list[AttributeInput] = Field(default_factory=list)
    links: list[LinkInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_source(self):
        if self.sourceType in ("USER_UPLOAD", "INTERNAL_ASSET") and not self.storageObjectId:
            raise ValueError("This reference requires storageObjectId")
        if self.sourceType == "PUBLIC_URL":
            if not self.sourceUrl or not self.sourceUrl.startswith(("http://", "https://")):
                raise ValueError("PUBLIC_URL requires an HTTP(S) URL")
        if self.sourceType == "DEMO_ASSET" and not self.storageObjectId:
            if not self.sourceUrl or not self.sourceUrl.startswith("/demo-assets/"):
                raise ValueError("DEMO_ASSET requires a demo URL or storageObjectId")
        if self.sourceUrl:
            VersionInput.valid_url(self.sourceUrl)
        return self

class ReferencePatch(Input):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    usageRightsNote: str | None = None
    tags: list[str] | None = None
    archived: bool | None = Field(default=None, strict=True)
    expectedUpdatedAt: datetime

    @model_validator(mode="after")
    def non_null(self):
        for key in ("title", "tags", "archived"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(key + " cannot be null")
        return self

class StorageDTO(BaseModel):
    id: UUID
    originalFilename: str | None
    mimeType: str
    byteSize: int
    width: int | None
    height: int | None
    checksum: str | None
    url: str
    urlExpiresAt: datetime | None
    createdAt: datetime

class AttributeDTO(BaseModel):
    id: UUID
    referenceId: UUID
    attributeType: Category
    value: AttributeValue
    origin: Literal["USER_PROVIDED", "AI_INFERRED", "SYSTEM_DERIVED"]
    reviewStatus: Literal["UNREVIEWED", "CONFIRMED", "CORRECTED", "REJECTED"]
    createdBy: UserSummary | None
    createdAt: datetime
    updatedAt: datetime

class LinkDTO(BaseModel):
    targetType: TargetType
    targetId: UUID
    relationshipType: str | None
    usageNote: str | None

class ReferenceDTO(BaseModel):
    id: UUID
    projectId: UUID
    title: str
    description: str | None
    sourceType: SourceType
    sourceUrl: str | None
    usageRightsNote: str | None
    tags: list[str]
    fileUrl: str | None
    thumbnailUrl: str | None
    attributes: list[AttributeDTO]
    links: list[LinkDTO]
    addedBy: UserSummary
    createdAt: datetime
    updatedAt: datetime
    archivedAt: datetime | None

class ReferenceResponse(BaseModel):
    data: ReferenceDTO
    meta: dict | None = None
class AttributeResponse(BaseModel):
    data: AttributeDTO
class StorageResponse(BaseModel):
    data: StorageDTO
class ReferenceList(BaseModel):
    data: list[ReferenceDTO]
    page: Page
