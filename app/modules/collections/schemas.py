from datetime import datetime
from uuid import UUID
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from app.modules.workspace.schemas import Input, UserSummary, Page
from app.modules.assets.schemas import VersionSummary, AssetType

CollectionStatus = Literal["DRAFT", "IN_REVIEW", "APPROVED", "SUPERSEDED", "ARCHIVED"]
RevisionStatus = Literal["DRAFT", "IN_REVIEW", "APPROVED", "REJECTED", "SUPERSEDED"]
CollectionType = Literal["DELIVERABLE", "REVIEW_ROUND", "PRESENTATION", "OTHER"]

class PinInput(Input):
    assetVersionId: UUID
    role: str | None = None
    note: str | None = None

class ItemInput(PinInput):
    position: int | None = Field(default=None, gt=0, strict=True)

class CollectionInput(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    collectionType: CollectionType
    items: list[PinInput] = Field(default_factory=list)

class CollectionPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    archived: bool | None = Field(default=None, strict=True)
    expectedUpdatedAt: datetime

    @model_validator(mode="after")
    def non_null(self):
        for key in ("name", "archived"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(key + " cannot be null")
        return self

class RevisionInput(Input):
    basedOnRevisionId: UUID | None = None
    changeSummary: str = Field(min_length=3, max_length=1000)

class ItemPatch(Input):
    assetVersionId: UUID | None = None
    position: int | None = Field(default=None, gt=0, strict=True)
    role: str | None = None
    note: str | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def non_null(self):
        for key in ("assetVersionId", "position"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(key + " cannot be null")
        return self

class ApproveInput(Input):
    comment: str | None = None

class RejectInput(Input):
    decision: Literal["CHANGES_REQUESTED", "REJECTED"]
    comment: str = Field(min_length=3, max_length=2000)

class VersionRef(VersionSummary):
    assetTitle: str
    assetType: AssetType

class ItemDTO(BaseModel):
    id: UUID
    collectionRevisionId: UUID
    position: int
    role: str | None
    note: str | None
    pinnedVersion: VersionRef
    latestVersion: VersionRef
    isStale: bool
    newerVersionCount: int

class ApprovalDTO(BaseModel):
    id: UUID
    collectionRevisionId: UUID
    decision: Literal["APPROVED", "CHANGES_REQUESTED", "REJECTED"]
    comment: str | None
    reviewedBy: UserSummary
    reviewedAt: datetime

class RevisionSummary(BaseModel):
    id: UUID
    collectionId: UUID
    revisionNumber: int
    status: RevisionStatus
    changeSummary: str | None
    itemCount: int
    staleItemCount: int
    createdBy: UserSummary
    createdAt: datetime
    submittedAt: datetime | None
    approvedAt: datetime | None

class RevisionDTO(RevisionSummary):
    collectionName: str
    items: list[ItemDTO]
    approvals: list[ApprovalDTO]

class CollectionDTO(BaseModel):
    id: UUID
    projectId: UUID
    name: str
    description: str | None
    collectionType: CollectionType
    status: CollectionStatus
    latestRevision: RevisionSummary
    approvedRevisionId: UUID | None
    createdBy: UserSummary
    createdAt: datetime
    updatedAt: datetime
    archivedAt: datetime | None

class CollectionDetail(CollectionDTO):
    revisions: list[RevisionSummary]

class CollectionResponse(BaseModel):
    data: CollectionDTO
class DetailResponse(BaseModel):
    data: CollectionDetail
class RevisionResponse(BaseModel):
    data: RevisionDTO
class ItemResponse(BaseModel):
    data: ItemDTO
class CollectionList(BaseModel):
    data: list[CollectionDTO]
    page: Page
class RevisionList(BaseModel):
    data: list[RevisionSummary]
    page: Page
