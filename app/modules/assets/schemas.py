from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
from app.modules.workspace.schemas import Input, UserSummary, Page

AssetType = Literal["HERO_IMAGE", "PRODUCT_IMAGE", "BACKGROUND", "TEXTURE", "CHARACTER", "CONCEPT_ART", "LAYOUT", "OTHER"]
AssetStatus = Literal["DRAFT", "IN_PROGRESS", "NEEDS_REVIEW", "APPROVED", "REJECTED", "ARCHIVED"]
VersionStatus = Literal["DRAFT", "CANDIDATE", "NEEDS_REVIEW", "APPROVED", "REJECTED", "SUPERSEDED"]

class RecordedAttributes(Input):
    palette: list[str] = Field(default_factory=list)
    lighting: list[str] = Field(default_factory=list)
    composition: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    typography: list[str] = Field(default_factory=list)
    subject: list[str] = Field(default_factory=list)

    @field_validator("*", mode="after")
    @classmethod
    def normalize(cls, value):
        return list(dict.fromkeys(x.strip().lower() for x in value if x.strip()))

class ReferenceUsage(Input):
    referenceId: UUID
    usageNote: str | None = None

class VersionInput(Input):
    derivedFromVersionId: UUID | None = None
    directionRevisionId: UUID | None = None
    briefRevisionId: UUID | None = None
    storageObjectId: UUID | None = None
    thumbnailStorageObjectId: UUID | None = None
    externalFileUrl: str | None = None
    metadataOnly: bool = Field(default=False, strict=True)
    mimeType: str | None = None
    width: int | None = Field(default=None, gt=0, strict=True)
    height: int | None = Field(default=None, gt=0, strict=True)
    prompt: str | None = None
    negativePrompt: str | None = None
    providerName: str | None = None
    modelName: str | None = None
    modelVersion: str | None = None
    generationSettings: dict[str, Any] = Field(default_factory=dict)
    recordedAttributes: RecordedAttributes = Field(default_factory=RecordedAttributes)
    revisionRationale: str = Field(min_length=3)
    references: list[ReferenceUsage] = Field(default_factory=list)
    status: Literal["DRAFT", "CANDIDATE", "NEEDS_REVIEW"] = "DRAFT"

    @field_validator("externalFileUrl")
    @classmethod
    def valid_url(cls, value):
        if value is None:
            return value
        parsed = urlsplit(value)
        if any(ord(char) < 32 for char in value) or "\\" in value:
            raise ValueError("Invalid external image URL")
        if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
            return value
        if value.startswith("/demo-assets/") and not parsed.scheme and not parsed.netloc and ".." not in parsed.path.split("/"):
            return value
        raise ValueError("Use an http(s) URL or /demo-assets/ path")

    @model_validator(mode="after")
    def image_required(self):
        if not self.storageObjectId and not self.externalFileUrl and not self.metadataOnly:
            raise ValueError("A file, URL, or metadataOnly: true is required")
        return self

class AssetInput(Input):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    assetType: AssetType
    tags: list[str] = Field(default_factory=list)
    ownerUserId: UUID | None = None
    initialVersion: VersionInput | None = None

class AssetPatch(Input):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    assetType: AssetType | None = None
    status: AssetStatus | None = None
    tags: list[str] | None = None
    ownerUserId: UUID | None = None
    expectedUpdatedAt: datetime

    @model_validator(mode="after")
    def non_nullable(self):
        for name in ("title", "assetType", "status", "tags"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(name + " cannot be null")
        return self

class VersionPatch(Input):
    status: Literal["DRAFT", "CANDIDATE", "NEEDS_REVIEW", "SUPERSEDED"]
    reason: str = Field(min_length=3, max_length=500)

class DecisionInput(Input):
    decisionType: Literal["APPROVE_VERSION", "REJECT_VERSION", "REQUEST_REVISION"]
    rationale: str = Field(min_length=3, max_length=2000)

class VersionSummary(BaseModel):
    id: UUID
    assetId: UUID
    versionNumber: int
    status: VersionStatus
    thumbnailUrl: str | None
    createdAt: datetime

class DecisionDTO(BaseModel):
    id: UUID
    projectId: UUID
    assessmentItemId: UUID | None
    assetId: UUID | None
    assetVersionId: UUID | None
    directionRevisionId: UUID | None
    collectionRevisionId: UUID | None
    decisionType: str
    selectedAction: str | None
    rationale: str
    previousRecommendation: str | None
    newRecommendation: str | None
    supersedesDecisionId: UUID | None
    supersededByDecisionId: UUID | None
    createdBy: UserSummary
    createdAt: datetime

class AssetDTO(BaseModel):
    id: UUID
    projectId: UUID
    title: str
    description: str | None
    assetType: AssetType
    status: AssetStatus
    tags: list[str]
    owner: UserSummary | None
    versionCount: int
    latestVersion: VersionSummary | None
    createdBy: UserSummary
    createdAt: datetime
    updatedAt: datetime
    archivedAt: datetime | None

class AssetDetail(AssetDTO):
    versions: list[VersionSummary]
    referenceLinks: list[dict]
    recentDecisions: list[DecisionDTO]

class Lineage(BaseModel):
    derivedFrom: VersionSummary | None
    derivedVersions: list[VersionSummary]

class VersionDTO(BaseModel):
    id: UUID
    assetId: UUID
    assetTitle: str
    versionNumber: int
    derivedFromVersionId: UUID | None
    directionRevisionId: UUID | None
    briefRevisionId: UUID | None
    fileUrl: str | None
    thumbnailUrl: str | None
    externalFileUrl: str | None
    isMetadataOnly: bool
    mimeType: str | None
    width: int | None
    height: int | None
    contentChecksum: str | None
    prompt: str | None
    negativePrompt: str | None
    providerName: str | None
    modelName: str | None
    modelVersion: str | None
    generationSettings: dict[str, Any]
    recordedAttributes: RecordedAttributes
    revisionRationale: str
    status: VersionStatus
    references: list[ReferenceUsage]
    isLatest: bool
    pinnedIn: list[dict]
    lineage: Lineage
    createdBy: UserSummary
    createdAt: datetime

class AssetResponse(BaseModel):
    data: AssetDTO
class AssetDetailResponse(BaseModel):
    data: AssetDetail
class VersionResponse(BaseModel):
    data: VersionDTO
class AssetList(BaseModel):
    data: list[AssetDTO]
    page: Page
class VersionList(BaseModel):
    data: list[VersionSummary]
    page: Page
class VersionDecisionResult(BaseModel):
    decision: DecisionDTO
    assetVersion: VersionDTO
class VersionDecisionResponse(BaseModel):
    data: VersionDecisionResult
