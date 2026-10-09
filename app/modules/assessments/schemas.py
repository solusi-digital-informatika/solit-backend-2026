from datetime import datetime
from uuid import UUID
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from app.modules.workspace.schemas import Input, UserSummary, Page
from app.modules.collections.schemas import VersionRef
from app.modules.assets.schemas import DecisionDTO

Recommendation = Literal["REUSE_CANDIDATE", "ADAPT_CANDIDATE", "REVIEW_REQUIRED", "RECREATE_CANDIDATE"]
ResolutionStatus = Literal["UNRESOLVED", "ACCEPTED", "OVERRIDDEN", "DISMISSED", "DEFERRED"]
AssessmentStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]

class AssessmentInput(Input):
    oldDirectionRevisionId: UUID | None = None
    newDirectionRevisionId: UUID
    briefRevisionId: UUID | None = None
    assetVersionIds: list[UUID] | None = None
    mode: Literal["RULES_ONLY", "AI_ASSISTED", "HYBRID"]
    includeImages: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def unique_scope(self):
        if self.assetVersionIds is not None and len(set(self.assetVersionIds)) != len(self.assetVersionIds):
            raise ValueError("assetVersionIds must be unique")
        return self

class ResolveInput(Input):
    resolution: Literal["ACCEPT", "OVERRIDE", "DEFER", "DISMISS"]
    overrideRecommendation: Recommendation | None = None
    selectedAction: str | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def rationale_required(self):
        if self.resolution == "OVERRIDE" and self.overrideRecommendation is None:
            raise ValueError("overrideRecommendation is required for OVERRIDE")
        if self.resolution != "OVERRIDE" and self.overrideRecommendation is not None:
            raise ValueError("overrideRecommendation is only allowed for OVERRIDE")
        if self.resolution in ("OVERRIDE", "DISMISS") and (not self.rationale or len(self.rationale) < 5):
            raise ValueError("A rationale of at least 5 characters is required")
        return self

class Evidence(BaseModel):
    source: Literal["BRIEF", "DIRECTION", "ASSET_METADATA", "IMAGE_OBSERVATION", "USER_NOTE", "RULE"]
    statement: str = Field(min_length=1)
    sourceId: UUID | None
    ruleId: str | None

class ProviderItem(BaseModel):
    assetVersionId: UUID
    recommendation: Recommendation
    rationale: str = Field(min_length=1)
    supportingEvidence: list[Evidence]
    conflictingEvidence: list[Evidence]
    missingInformation: list[str]
    suggestedActions: list[str]
    uncertaintyLevel: Literal["LOW", "MEDIUM", "HIGH"]
    requiresHumanReview: bool
    analysisSource: Literal["RULES", "AI", "HYBRID"]
    priorityReason: str = Field(min_length=1)

class ItemDTO(BaseModel):
    id: UUID
    assessmentId: UUID
    assetVersion: VersionRef
    recommendation: Recommendation
    rationale: str
    supportingEvidence: list[Evidence]
    conflictingEvidence: list[Evidence]
    missingInformation: list[str]
    suggestedActions: list[str]
    uncertaintyLevel: Literal["LOW", "MEDIUM", "HIGH"]
    requiresHumanReview: bool
    analysisSource: Literal["RULES", "AI", "HYBRID"]
    priority: int
    priorityReason: str
    resolutionStatus: ResolutionStatus
    effectiveRecommendation: Recommendation
    latestDecision: DecisionDTO | None
    createdAt: datetime
    updatedAt: datetime

class DiffChange(BaseModel):
    category: str
    field: str
    from_: str | None = Field(alias="from")
    to: str | None
    kind: Literal["ADDED", "REMOVED", "CHANGED"]

class DirectionDiffDTO(BaseModel):
    fromRevisionId: UUID | None
    toRevisionId: UUID
    summary: str
    changes: list[DiffChange]

class Counts(BaseModel):
    total: int
    byRecommendation: dict[Recommendation, int]
    unresolved: int

class AssessmentDTO(BaseModel):
    id: UUID
    projectId: UUID
    oldDirectionRevisionId: UUID | None
    newDirectionRevisionId: UUID
    briefRevisionId: UUID | None
    mode: Literal["RULES_ONLY", "AI_ASSISTED", "HYBRID"]
    status: AssessmentStatus
    providerName: str | None
    modelName: str | None
    promptTemplateVersion: str | None
    outputSchemaVersion: str
    rulesVersion: str
    isSimulated: bool
    imagesSent: int
    directionDiff: DirectionDiffDTO
    summary: str | None
    errorCode: str | None
    errorSummary: str | None
    counts: Counts
    items: list[ItemDTO] | None
    startedBy: UserSummary
    startedAt: datetime
    completedAt: datetime | None

class AssessmentResponse(BaseModel):
    data: AssessmentDTO
class AssessmentList(BaseModel):
    data: list[AssessmentDTO]
    page: Page
class ResolveResult(BaseModel):
    decision: DecisionDTO
    item: ItemDTO
class ResolveResponse(BaseModel):
    data: ResolveResult
class DecisionList(BaseModel):
    data: list[DecisionDTO]
    page: Page
