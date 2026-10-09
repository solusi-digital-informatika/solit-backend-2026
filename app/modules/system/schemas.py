from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from app.modules.workspace.schemas import Input, ProjectDTO, DirectionDTO, RevisionDTO
from app.modules.assets.schemas import AssetStatus, DecisionDTO
from app.modules.assessments.schemas import AssessmentStatus

class ResetInput(Input):
    confirm: bool = Field(strict=True)

    @field_validator("confirm")
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError("confirm must be true")
        return value

class ExportInput(Input):
    format: Literal["JSON", "MARKDOWN", "CSV"]

class ActiveDirection(BaseModel):
    direction: DirectionDTO
    revision: RevisionDTO

class AssetCounts(BaseModel):
    total: int
    byStatus: dict[AssetStatus, int]

class LatestAssessment(BaseModel):
    id: UUID
    status: AssessmentStatus
    startedAt: datetime
    isSimulated: bool

class SummaryDTO(BaseModel):
    project: ProjectDTO
    activeDirection: ActiveDirection | None
    assetCounts: AssetCounts
    unresolvedRecommendationCount: int
    staleCollectionItemCount: int
    latestAssessment: LatestAssessment | None
    recentDecisions: list[DecisionDTO]

class SummaryResponse(BaseModel):
    data: SummaryDTO

class AiSettingsDTO(BaseModel):
    mode: Literal["mock", "openai"]
    providerConfigured: bool
    modelName: str | None
    imageAnalysisAvailable: bool
    maxAssetsPerAssessment: int
    monthlyBudgetLimitUsd: float | None
    monthToDateCostUsd: float | None

class AiSettingsResponse(BaseModel):
    data: AiSettingsDTO

class AiTestDTO(BaseModel):
    ok: bool
    latencyMs: int | None
    errorCode: str | None
    message: str

class AiTestResponse(BaseModel):
    data: AiTestDTO

class ExportWarning(BaseModel):
    code: Literal["LOCAL_ONLY_FILE_PATHS", "METADATA_ONLY_VERSIONS", "OTHER"]
    message: str

class ExportDTO(BaseModel):
    id: UUID
    format: Literal["JSON", "MARKDOWN", "CSV"]
    filename: str
    contentType: str
    content: str
    warnings: list[ExportWarning]
    createdAt: datetime

class ExportResponse(BaseModel):
    data: ExportDTO
