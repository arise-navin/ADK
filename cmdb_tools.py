from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from servicenow_client import (
    MAX_CI_LIMIT,
    ConfigurationItem,
    ServiceNowClient,
    ServiceNowReference,
    read_servicenow_credentials_from_env,
    validate_ci_limit,
    validate_ci_name,
    validate_sys_id,
)

DEFAULT_STALE_DAYS_THRESHOLD = 90
MAX_STALE_DAYS_THRESHOLD = 3650

AssessmentStatus = Literal["current", "stale", "unknown"]
RiskLevel = Literal["low", "medium", "high", "unknown"]


class CiLookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sys_id: str | None = None
    name: str | None = None
    limit: int = Field(default=1, ge=1, le=MAX_CI_LIMIT)

    @model_validator(mode="after")
    def validate_identifier(self) -> "CiLookupInput":
        if self.sys_id is None and self.name is None:
            raise ValueError("Either sys_id or name is required.")
        if self.sys_id is not None:
            validate_sys_id(self.sys_id)
        if self.name is not None:
            validate_ci_name(self.name)
        validate_ci_limit(self.limit)
        return self


class StalenessInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sys_id: str | None = None
    name: str | None = None
    ci: ConfigurationItem | None = None
    stale_days_threshold: int = Field(default=DEFAULT_STALE_DAYS_THRESHOLD, ge=1, le=MAX_STALE_DAYS_THRESHOLD)

    @model_validator(mode="after")
    def validate_source(self) -> "StalenessInput":
        if self.ci is None and self.sys_id is None and self.name is None:
            raise ValueError("Provide ci, sys_id, or name.")
        if self.sys_id is not None:
            validate_sys_id(self.sys_id)
        if self.name is not None:
            validate_ci_name(self.name)
        return self


class ProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sys_id: str | None = None
    name: str | None = None
    assessment: dict[str, object] | None = None
    recommendation_context: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "ProposalInput":
        if self.sys_id is None and self.name is None:
            raise ValueError("Provide sys_id or name.")
        if self.sys_id is not None:
            validate_sys_id(self.sys_id)
        if self.name is not None:
            validate_ci_name(self.name)
        return self

    @field_validator("recommendation_context")
    @classmethod
    def validate_recommendation_context(cls, recommendation_context: str | None) -> str | None:
        if recommendation_context is None:
            return None
        normalized_context = recommendation_context.strip()
        if len(normalized_context) > 1000:
            raise ValueError("recommendation_context must be 1000 characters or fewer.")
        return normalized_context


def get_ci_details(sys_id: str | None, name: str | None, limit: int) -> dict[str, object]:
    request = CiLookupInput(sys_id=sys_id, name=name, limit=limit)
    client = build_servicenow_client()
    cis = client.get_cmdb_ci(sys_id=request.sys_id, name=request.name, limit=request.limit)
    return {
        "records": [serialize_ci(ci) for ci in cis],
        "record_count": len(cis),
        "multiple_matches": len(cis) > 1,
        "query_was_bounded": True,
        "record_modified": False,
    }


def analyze_ci_staleness(
    sys_id: str | None,
    name: str | None,
    ci: ConfigurationItem | None,
    stale_days_threshold: int,
) -> dict[str, object]:
    request = StalenessInput(
        sys_id=sys_id,
        name=name,
        ci=ci,
        stale_days_threshold=stale_days_threshold,
    )
    selected_ci = request.ci if request.ci is not None else fetch_single_ci(request.sys_id, request.name)
    timestamp_value = field_text(selected_ci.sys_updated_on)
    timestamp = parse_servicenow_datetime(timestamp_value)
    if timestamp is None:
        return build_unknown_staleness_result(selected_ci, request.stale_days_threshold)

    current_time = datetime.now(UTC)
    staleness_days = max((current_time - timestamp).days, 0)
    assessment = "stale" if staleness_days > request.stale_days_threshold else "current"
    risk_level = derive_staleness_risk_level(assessment, staleness_days, request.stale_days_threshold)
    evidence = [
        "sys_updated_on is the selected timestamp because no true discovery or last-seen field is available.",
        f"sys_updated_on age is {staleness_days} days and threshold is {request.stale_days_threshold} days.",
    ]
    return {
        "ci_sys_id": selected_ci.sys_id,
        "ci_name": field_text(selected_ci.name),
        "staleness_days": staleness_days,
        "threshold_days": request.stale_days_threshold,
        "assessment": assessment,
        "risk_level": risk_level,
        "timestamp_field": "sys_updated_on",
        "timestamp_value": timestamp_value,
        "basis": "This assessment measures record update freshness, not discovery freshness.",
        "evidence": evidence,
        "factual_observations": evidence,
        "ai_generated_inferences": build_inferences(assessment),
        "recommended_action": build_recommended_action(assessment),
        "requires_human_review": True,
        "record_modified": False,
    }


def resolve_ci_owner(sys_id: str | None, name: str | None) -> dict[str, object]:
    request = CiLookupInput(sys_id=sys_id, name=name, limit=1)
    ci = fetch_single_ci(request.sys_id, request.name)
    owner_fields = {
        "assigned_to": serialize_reference(ci.assigned_to),
        "managed_by": serialize_reference(ci.managed_by),
        "support_group": serialize_reference(ci.support_group),
        "location": serialize_reference(ci.location),
    }
    unresolved_fields = [field_name for field_name, value in owner_fields.items() if value is None]
    return {
        "ci_sys_id": ci.sys_id,
        "ci_name": field_text(ci.name),
        "owner_references": owner_fields,
        "unresolved_owner_fields": unresolved_fields,
        "verified_accountable_owner": None,
        "basis": "Ownership fields are reported as available references only; no precedence rule is assumed.",
        "record_modified": False,
    }


def generate_stale_ci_proposal(
    sys_id: str | None,
    name: str | None,
    assessment: dict[str, object] | None,
    recommendation_context: str | None,
) -> dict[str, object]:
    request = ProposalInput(
        sys_id=sys_id,
        name=name,
        assessment=assessment,
        recommendation_context=recommendation_context,
    )
    ci = fetch_single_ci(request.sys_id, request.name)
    selected_assessment = (
        request.assessment
        if request.assessment is not None
        else analyze_ci_staleness(ci.sys_id, None, ci, DEFAULT_STALE_DAYS_THRESHOLD)
    )
    evidence = selected_assessment.get("evidence", [])
    return {
        "ci_sys_id": ci.sys_id,
        "ci_name": field_text(ci.name),
        "evidence": evidence,
        "risk_assessment": selected_assessment.get("risk_level", "unknown"),
        "recommended_action": selected_assessment.get(
            "recommended_action",
            "Review CI ownership, status, and update history before taking governance action.",
        ),
        "required_verification": [
            "Confirm the CI still exists and is operationally relevant.",
            "Confirm accountable owner or support group.",
            "Confirm whether sys_updated_on is an adequate freshness signal for this CI class.",
        ],
        "recommendation_context": request.recommendation_context,
        "requires_human_review": True,
        "record_modified": False,
    }


def build_servicenow_client() -> ServiceNowClient:
    credentials = read_servicenow_credentials_from_env()
    return ServiceNowClient(credentials=credentials, timeout_seconds=15.0)


def fetch_single_ci(sys_id: str | None, name: str | None) -> ConfigurationItem:
    client = build_servicenow_client()
    cis = client.get_cmdb_ci(sys_id=sys_id, name=name, limit=2)
    if not cis:
        raise ValueError("No ServiceNow CMDB CI matched the supplied identifier.")
    if len(cis) > 1:
        raise ValueError("Multiple ServiceNow CMDB CIs matched the supplied identifier. Use sys_id.")
    return cis[0]


def serialize_ci(ci: ConfigurationItem) -> dict[str, object]:
    return {
        "sys_id": ci.sys_id,
        "name": field_text(ci.name),
        "sys_class_name": field_text(ci.sys_class_name),
        "sys_updated_on": field_text(ci.sys_updated_on),
        "sys_created_on": field_text(ci.sys_created_on),
        "install_status": field_text(ci.install_status),
        "operational_status": field_text(ci.operational_status),
        "assigned_to": serialize_reference(ci.assigned_to),
        "managed_by": serialize_reference(ci.managed_by),
        "support_group": serialize_reference(ci.support_group),
        "location": serialize_reference(ci.location),
    }


def field_text(field: ServiceNowReference | str | None) -> str | None:
    if field is None:
        return None
    if isinstance(field, str):
        return field
    return field.display_value or field.value


def serialize_reference(reference: ServiceNowReference | str | None) -> dict[str, str | None] | str | None:
    if reference is None:
        return None
    if isinstance(reference, str):
        return reference
    return {
        "display_value": reference.display_value,
        "value": reference.value,
    }


def parse_servicenow_datetime(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    normalized_value = value.strip()
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z")
    for date_format in formats:
        try:
            parsed_datetime = datetime.strptime(normalized_value, date_format)
            if parsed_datetime.tzinfo is None:
                return parsed_datetime.replace(tzinfo=UTC)
            return parsed_datetime.astimezone(UTC)
        except ValueError:
            continue
    raise ValueError(f"Unsupported ServiceNow datetime format for sys_updated_on: {normalized_value}")


def build_unknown_staleness_result(ci: ConfigurationItem, threshold_days: int) -> dict[str, object]:
    return {
        "ci_sys_id": ci.sys_id,
        "ci_name": field_text(ci.name),
        "staleness_days": None,
        "threshold_days": threshold_days,
        "assessment": "unknown",
        "risk_level": "unknown",
        "timestamp_field": "sys_updated_on",
        "timestamp_value": field_text(ci.sys_updated_on),
        "basis": "No usable timestamp was available; freshness cannot be calculated.",
        "evidence": ["sys_updated_on is missing or empty."],
        "factual_observations": ["No usable freshness timestamp was retrieved from ServiceNow."],
        "ai_generated_inferences": [],
        "recommended_action": "Verify CI update history and discovery source before governance action.",
        "requires_human_review": True,
        "record_modified": False,
    }


def derive_staleness_risk_level(
    assessment: AssessmentStatus,
    staleness_days: int,
    threshold_days: int,
) -> RiskLevel:
    if assessment == "unknown":
        return "unknown"
    if assessment == "current":
        return "low"
    if staleness_days > threshold_days * 3:
        return "high"
    return "medium"


def build_inferences(assessment: AssessmentStatus) -> list[str]:
    if assessment == "stale":
        return ["The CI may need ownership and operational relevance review."]
    if assessment == "current":
        return ["The CI record update timestamp is within the configured threshold."]
    return []


def build_recommended_action(assessment: AssessmentStatus) -> str:
    if assessment == "stale":
        return "Verify CI ownership, operational status, and discovery/update source before remediation."
    if assessment == "current":
        return "No stale-CI governance action is indicated from sys_updated_on alone."
    return "Collect a valid freshness timestamp before deciding on governance action."
