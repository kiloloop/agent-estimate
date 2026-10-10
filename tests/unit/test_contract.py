"""Pin the leg-1 contract grammar and the ruled ownership/context invariants."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pytest
import yaml
from pydantic import ValidationError

from agent_estimate import contract
from agent_estimate.contract import schema


@pytest.fixture
def request_data() -> dict:
    return {
        "schema_version": "agent-estimate/estimate-request/v1",
        "task_spec": {
            "schema_version": "agent-estimate/task-spec/v1",
            "task_id": "caller-task-1",
            "title": "Forecast contract",
            "description": "Define the typed forecast contract.",
            "task_type": "coding",
            "required_capabilities": [],
            "dependency_task_ids": [],
        },
        "execution_profile": {
            "schema_version": "agent-estimate/execution-profile/v1",
            "execution_profile_id": "caller-profile-v1",
            "runtime": {"name": "codex", "agent_name": "codex"},
            "model": {"unknown_reason": "serving model unavailable"},
            "config_profile": {"name": "default", "revision": "v1"},
            "context": {"state": "cold"},
            "review": {"mode": "none", "expected_rounds": 0, "intensity": "standard"},
            "execution_mode": "single",
        },
        "admission": {"schema_version": "agent-estimate/admission-envelope/v1"},
    }


# Every row pins the entire field set, required fields, and nullable fields.
# This deliberately avoids a full Pydantic-generated schema snapshot, whose
# cosmetic details can change across the supported dependency versions.
SHAPES = [
    (
        schema.TaskSpec,
        "schema_version task_id title description task_type required_capabilities dependency_task_ids",
        "source scope tags",
        "source",
    ),
    (
        schema.ExecutionProfile,
        "schema_version execution_profile_id runtime model config_profile context review execution_mode",
        "estimate_multiplier reasoning_effort modifiers",
        "reasoning_effort",
    ),
    (
        schema.AdmissionEnvelope,
        "schema_version",
        "declared_cap_minutes declared_cap_files_touched source minutes_calculation files_calculation",
        "declared_cap_minutes declared_cap_files_touched source minutes_calculation files_calculation",
    ),
    (
        schema.EstimateRequest,
        "schema_version task_spec execution_profile admission",
        "request_id token_prior",
        "request_id token_prior",
    ),
    (
        schema.ForecastRecord,
        "schema_version request created_at_utc engine",
        ("forecast_id expected_minutes expected_files_touched expected_review_minutes basis source "
         "as_of tokens subscription"),
        ("forecast_id expected_minutes expected_files_touched expected_review_minutes source as_of "
         "subscription"),
    ),
    (
        schema.OutcomeObservation,
        "schema_version task_id",
        "observation_id forecast_id execution_profile_id execution_id source actual",
        "observation_id forecast_id execution_profile_id execution_id source",
    ),
    (schema.SourceReference, "", "system record_id revision", "system record_id revision"),
    (
        schema.TaskScope,
        "",
        "expected_files_touched estimated_lines_changed estimated_tests concerns",
        "expected_files_touched estimated_lines_changed estimated_tests concerns",
    ),
    (schema.RuntimeIdentity, "name agent_name", "", ""),
    (schema.ModelIdentity, "", "id unknown_reason", "id unknown_reason"),
    (schema.ConfigProfile, "name revision", "", ""),
    (
        schema.ExecutionContext,
        "state",
        "context_key basis implicit_co_dispatch",
        "context_key basis",
    ),
    (schema.ExecutionModifiers, "", "spec_clarity warm_context agent_fit", "warm_context"),
    (schema.ReviewPlan, "mode expected_rounds intensity", "", ""),
    (schema.EngineProvenance, "version registry_version", "name", ""),
    (
        schema.TokenForecast,
        "",
        (
            "expected_tokens_total expected_tokens_output expected_tokens_cache_read basis source "
            "as_of population warnings segment window"
        ),
        (
            "expected_tokens_total expected_tokens_output expected_tokens_cache_read source as_of "
            "population segment window"
        ),
    ),
    (
        schema.LocalTokenPrior,
        "basis source as_of population",
        "expected_tokens_total expected_tokens_output expected_tokens_cache_read warnings segment window",
        "expected_tokens_total expected_tokens_output expected_tokens_cache_read segment window",
    ),
    (schema.TokenSegment, "task_type execution_profile_id n", "", ""),
    (schema.ObservationWindow, "start end", "", ""),
    (
        schema.ObservedTokens,
        "",
        "tokens_total tokens_output total_definition coverage",
        "tokens_total tokens_output total_definition coverage",
    ),
    (
        schema.ObservationCensoring,
        "",
        "admission_pause_excluded peer_wait_included parallel_work_possible",
        "admission_pause_excluded peer_wait_included parallel_work_possible",
    ),
    (
        schema.ObservedActuals,
        "",
        (
            "wall_minutes work_minutes total_minutes time_basis files_touched review_rounds tokens "
            "started_at_utc completed_at_utc censoring"
        ),
        (
            "wall_minutes work_minutes total_minutes time_basis files_touched review_rounds "
            "started_at_utc completed_at_utc"
        ),
    ),
]


@pytest.mark.parametrize("model,required,optional,nullable", SHAPES)
def test_schema_shape_is_pinned(model, required, optional, nullable):
    artifact = model.model_json_schema()
    assert artifact["title"] == model.__name__
    assert artifact["additionalProperties"] is False
    assert set(artifact["properties"]) == set(required.split() + optional.split())
    assert set(artifact.get("required", [])) == set(required.split())
    actual_nullable = {
        name
        for name, field in artifact["properties"].items()
        if {"type": "null"} in field.get("anyOf", [])
    }
    assert actual_nullable == set(nullable.split())


VERSIONS = [
    (schema.TaskSpec, "task-spec"),
    (schema.ExecutionProfile, "execution-profile"),
    (schema.AdmissionEnvelope, "admission-envelope"),
    (schema.EstimateRequest, "estimate-request"),
    (schema.ForecastRecord, "forecast"),
    (schema.OutcomeObservation, "outcome-observation"),
]


def test_public_contract_surface():
    assert set(contract.__all__) == {model.__name__ for model, _ in VERSIONS} | {
        "BindingReceipt", "ReceiptError", "forecast_key", "forecast_sha256",
        "read_binding_receipt", "receipt_path", "write_binding_receipt",
        "TokenObservation", "TokenObservations", "fit_segments", "measured_token_forecast",
        "MeterTable", "subscription_forecast",
    }
    for model, _ in VERSIONS:
        assert getattr(contract, model.__name__) is model


@pytest.mark.parametrize("model,name", VERSIONS)
def test_version_tag_is_required_and_exact(model, name):
    artifact = model.model_json_schema()
    assert "schema_version" in artifact["required"]
    assert artifact["properties"]["schema_version"]["const"] == f"agent-estimate/{name}/v1"
    with pytest.raises(ValidationError) as error:
        model.model_validate({"schema_version": f"agent-estimate/{name}/v2"})
    assert any(e["loc"] == ("schema_version",) for e in error.value.errors())


@pytest.mark.parametrize(
    "model,field,values",
    [
        (
            schema.TaskSpec,
            "task_type",
            ["coding", "brainstorm", "research", "config", "documentation", "frontend", "app_dev"],
        ),
        (schema.ExecutionContext, "state", ["cold", "project_warm", "task_warm"]),
        (schema.ExecutionProfile, "execution_mode", ["single", "parallel", "co_dispatch"]),
        (schema.ReviewPlan, "mode", ["none", "single_round", "review_loop"]),
        (schema.ReviewPlan, "intensity", ["standard", "complex"]),
    ],
)
def test_finite_enums(model, field, values):
    assert model.model_json_schema()["properties"][field]["enum"] == values


@pytest.mark.parametrize(
    "model,field,nested",
    [
        (schema.TaskSpec, "source", schema.SourceReference),
        (schema.TaskSpec, "scope", schema.TaskScope),
        (schema.ExecutionProfile, "runtime", schema.RuntimeIdentity),
        (schema.ExecutionProfile, "model", schema.ModelIdentity),
        (schema.ExecutionProfile, "config_profile", schema.ConfigProfile),
        (schema.ExecutionProfile, "context", schema.ExecutionContext),
        (schema.ExecutionProfile, "review", schema.ReviewPlan),
        (schema.ExecutionProfile, "modifiers", schema.ExecutionModifiers),
        (schema.AdmissionEnvelope, "source", schema.SourceReference),
        (schema.EstimateRequest, "task_spec", schema.TaskSpec),
        (schema.EstimateRequest, "execution_profile", schema.ExecutionProfile),
        (schema.EstimateRequest, "admission", schema.AdmissionEnvelope),
        (schema.EstimateRequest, "token_prior", schema.LocalTokenPrior),
        (schema.ForecastRecord, "request", schema.EstimateRequest),
        (schema.ForecastRecord, "engine", schema.EngineProvenance),
        (schema.ForecastRecord, "tokens", schema.TokenForecast),
        (schema.TokenForecast, "segment", schema.TokenSegment),
        (schema.TokenForecast, "window", schema.ObservationWindow),
        (schema.OutcomeObservation, "source", schema.SourceReference),
        (schema.OutcomeObservation, "actual", schema.ObservedActuals),
        (schema.ObservedActuals, "tokens", schema.ObservedTokens),
        (schema.ObservedActuals, "censoring", schema.ObservationCensoring),
    ],
)
def test_nested_schema_references_are_pinned(model, field, nested):
    prop = model.model_json_schema()["properties"][field]
    variants = prop.get("anyOf", prop.get("allOf", [prop]))
    assert [v["$ref"] for v in variants if "$ref" in v] == [f"#/$defs/{nested.__name__}"]


def test_outcome_coverage_and_engine_name_vocabulary():
    prop = schema.ObservedTokens.model_json_schema()["properties"]["coverage"]
    assert prop["anyOf"][0]["enum"] == ["complete", "partial", "unavailable"]
    assert (
        schema.EngineProvenance.model_json_schema()["properties"]["name"]["const"]
        == "agent-estimate"
    )


def test_request_joins_three_distinct_owners(request_data):
    request = schema.EstimateRequest.model_validate(request_data)
    assert isinstance(request.task_spec, schema.TaskSpec)
    assert isinstance(request.execution_profile, schema.ExecutionProfile)
    assert isinstance(request.admission, schema.AdmissionEnvelope)
    assert request.admission.declared_cap_minutes is None
    assert request.admission.declared_cap_files_touched is None
    assert request.request_id is None  # The contract does not generate identities.
    assert request.execution_profile.modifiers.warm_context is None
    assert request.execution_profile.context.implicit_co_dispatch is False
    assert request.execution_profile.estimate_multiplier == 1.0


@pytest.mark.parametrize(
    "owner,field,value",
    [
        ("task_spec", "context_key", "project/task"),
        ("task_spec", "context", {"state": "cold"}),
        ("task_spec", "model", "some-model"),
        ("execution_profile", "context_key", "project/task"),
        ("task_spec", "declared_cap_minutes", 20),
        ("execution_profile", "declared_cap_minutes", 20),
        ("task_spec", "declared_cap_files_touched", 3),
        ("execution_profile", "declared_cap_files_touched", 3),
    ],
)
def test_r5_r6_fields_cannot_move_to_other_owners(request_data, owner, field, value):
    request_data[owner][field] = value
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        schema.EstimateRequest.model_validate(request_data)


def test_cap_slots_preserve_caller_values_without_arithmetic(request_data):
    request_data["task_spec"]["scope"] = {"expected_files_touched": 9}
    request_data["admission"].update(declared_cap_minutes=7.5, declared_cap_files_touched=2)
    request = schema.EstimateRequest.model_validate(request_data)
    assert request.task_spec.scope.expected_files_touched == 9
    assert request.admission.declared_cap_minutes == 7.5
    assert request.admission.declared_cap_files_touched == 2


@pytest.mark.parametrize("state", ["cold", "project_warm", "task_warm"])
@pytest.mark.parametrize("warm_context", [0.3, 0.5, 1.0, 1.15])
def test_r6_rejects_explicit_and_implicit_warmth_on_same_key(request_data, state, warm_context):
    profile = request_data["execution_profile"]
    profile.update(execution_mode="co_dispatch", modifiers={"warm_context": warm_context})
    profile["context"] = {
        "state": state,
        "context_key": "project/task",
        "implicit_co_dispatch": True,
    }
    with pytest.raises(ValidationError, match="explicit warm_context wins"):
        schema.EstimateRequest.model_validate(request_data)


@pytest.mark.parametrize("mode", ["single", "parallel", "co_dispatch"])
@pytest.mark.parametrize("warm_context", [0.5, 1.0])
def test_r6_explicit_wins_when_implicit_is_disabled(request_data, mode, warm_context):
    profile = request_data["execution_profile"]
    profile.update(execution_mode=mode, modifiers={"warm_context": warm_context})
    profile["context"] = {"state": "task_warm", "context_key": "project/task"}
    request = schema.EstimateRequest.model_validate(request_data)
    assert request.execution_profile.modifiers.warm_context == warm_context
    assert request.execution_profile.context.implicit_co_dispatch is False


def test_r6_implicit_warmth_allowed_without_explicit_modifier(request_data):
    profile = request_data["execution_profile"]
    profile["execution_mode"] = "co_dispatch"
    profile["context"].update(
        state="project_warm", context_key="project/task", implicit_co_dispatch=True
    )
    request = schema.EstimateRequest.model_validate(request_data)
    assert request.execution_profile.modifiers.warm_context is None
    assert request.execution_profile.context.implicit_co_dispatch is True


@pytest.mark.parametrize(
    "mode,key", [("single", "key"), ("parallel", "key"), ("co_dispatch", None)]
)
def test_r6_implicit_warmth_requires_mode_and_key(request_data, mode, key):
    profile = request_data["execution_profile"]
    profile["execution_mode"] = mode
    profile["context"].update(context_key=key, implicit_co_dispatch=True)
    with pytest.raises(ValidationError, match="requires co_dispatch mode and context_key"):
        schema.EstimateRequest.model_validate(request_data)


@pytest.mark.parametrize("invalid", ["true", "false", 0, 1, None])
def test_implicit_flag_is_strict_boolean(invalid):
    with pytest.raises(ValidationError):
        schema.ExecutionContext(state="cold", implicit_co_dispatch=invalid)


@pytest.mark.parametrize("invalid", ["warm", "project-warm", "COLD", "", None])
def test_context_state_has_no_implicit_aliases(invalid):
    with pytest.raises(ValidationError):
        schema.ExecutionContext(state=invalid)


@pytest.mark.parametrize("invalid", ["", "   ", 123])
def test_context_key_must_be_nonempty_string(invalid):
    with pytest.raises(ValidationError):
        schema.ExecutionContext(state="cold", context_key=invalid)


@pytest.mark.parametrize("data", [{}, {"id": "model", "unknown_reason": "unknown"}, {"id": ""}])
def test_model_identity_requires_exactly_one_identity_or_reason(data):
    with pytest.raises(ValidationError):
        schema.ModelIdentity.model_validate(data)


@pytest.mark.parametrize("data", [{"id": "caller-model"}, {"unknown_reason": "not exposed"}])
def test_model_identity_preserves_explicit_provenance(data):
    model = schema.ModelIdentity.model_validate(data)
    assert model.model_dump(exclude_none=True) == data


@pytest.mark.parametrize(
    "mode,rounds,intensity",
    [
        ("none", 1, "standard"),
        ("none", 0, "complex"),
        ("single_round", 0, "standard"),
        ("single_round", 2, "complex"),
        ("review_loop", 0, "standard"),
    ],
)
def test_inconsistent_review_plan_rejected(mode, rounds, intensity):
    with pytest.raises(ValidationError):
        schema.ReviewPlan(mode=mode, expected_rounds=rounds, intensity=intensity)


@pytest.mark.parametrize(
    "mode,rounds,intensity",
    [
        ("none", 0, "standard"),
        ("single_round", 1, "standard"),
        ("single_round", 1, "complex"),
        ("review_loop", 1, "standard"),
        ("review_loop", 3, "complex"),
    ],
)
def test_consistent_review_plan_accepted(mode, rounds, intensity):
    plan = schema.ReviewPlan(mode=mode, expected_rounds=rounds, intensity=intensity)
    assert plan.expected_rounds == rounds


@pytest.mark.parametrize("field", ["required_capabilities", "dependency_task_ids"])
def test_duplicate_capabilities_and_dependencies_rejected(request_data, field):
    request_data["task_spec"][field] = ["same", " same "]
    with pytest.raises(ValidationError, match="entries must be unique"):
        schema.EstimateRequest.model_validate(request_data)


@pytest.mark.parametrize("value", ["", " ", "x" * 201, 12])
def test_caller_identifier_is_bounded_nonempty_string(request_data, value):
    request_data["task_spec"]["task_id"] = value
    with pytest.raises(ValidationError):
        schema.EstimateRequest.model_validate(request_data)


@pytest.mark.parametrize(
    "path",
    [
        ("task_spec", "scope", "expected_files_touched"),
        ("task_spec", "scope", "estimated_lines_changed"),
        ("task_spec", "scope", "estimated_tests"),
        ("task_spec", "scope", "concerns"),
        ("admission", "declared_cap_files_touched"),
        ("execution_profile", "review", "expected_rounds"),
    ],
)
@pytest.mark.parametrize("value", [-1, 1.5, True, "2"])
def test_counts_are_nonnegative_integers(request_data, path, value):
    target = request_data
    for key in path[:-1]:
        target = target.setdefault(key, {})
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        schema.EstimateRequest.model_validate(request_data)


@pytest.mark.parametrize(
    "path",
    [
        ("admission", "declared_cap_minutes"),
        ("execution_profile", "estimate_multiplier"),
        ("execution_profile", "modifiers", "warm_context"),
        ("execution_profile", "modifiers", "spec_clarity"),
        ("execution_profile", "modifiers", "agent_fit"),
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -1, 0, True, "1.0"])
def test_numeric_factors_are_finite_positive_numbers(request_data, path, value):
    target = request_data
    for key in path[:-1]:
        target = target.setdefault(key, {})
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        schema.EstimateRequest.model_validate(request_data)


def test_every_nested_record_forbids_unknown_fields(request_data):
    complete = schema.EstimateRequest.model_validate(request_data).model_dump()
    complete["task_spec"]["source"] = {"system": "caller"}
    paths = [
        (),
        ("task_spec",),
        ("task_spec", "source"),
        ("task_spec", "scope"),
        ("execution_profile",),
        ("admission",),
    ]
    paths += [
        ("execution_profile", k)
        for k in ("runtime", "model", "config_profile", "context", "review", "modifiers")
    ]
    for path in paths:
        data = deepcopy(complete)
        target = data
        for key in path:
            target = target[key]
        target["unexpected"] = "value"
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            schema.EstimateRequest.model_validate(data)


def test_yaml_json_roundtrip_retains_owners_and_absent_warmth(request_data):
    request = schema.EstimateRequest.model_validate(yaml.safe_load(yaml.safe_dump(request_data)))
    restored = schema.EstimateRequest.model_validate_json(request.model_dump_json())
    assert restored == request
    assert restored.execution_profile.modifiers.warm_context is None


def test_forecast_scaffold_retains_request_and_provenance(request_data):
    record = schema.ForecastRecord(
        schema_version="agent-estimate/forecast/v1",
        request=request_data,
        created_at_utc="2026-09-05T00:00:00Z",
        engine={"version": "caller-version", "registry_version": "caller-registry"},
    )
    assert record.request.task_spec.task_id == "caller-task-1"
    assert record.forecast_id is None
    assert schema.ForecastRecord.model_validate_json(record.model_dump_json()) == record
    assert record.tokens.expected_tokens_total is None
    assert record.tokens.expected_tokens_output is None
    assert record.tokens.basis == "unavailable"


def test_outcome_slots_default_to_unknown_not_zero():
    observation = schema.OutcomeObservation(
        schema_version="agent-estimate/outcome-observation/v1", task_id="caller-task-1"
    )
    actual = observation.actual.model_dump()
    assert all(v is None for k, v in actual.items() if k not in {"tokens", "censoring"})
    assert all(v is None for v in actual["tokens"].values())
    assert all(v is None for v in actual["censoring"].values())
    assert observation.observation_id is None
    assert observation.forecast_id is None
    assert (
        schema.OutcomeObservation.model_validate_json(observation.model_dump_json()) == observation
    )


@pytest.mark.parametrize("field", ["wall_minutes", "work_minutes", "total_minutes"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True])
def test_actual_minutes_reject_nonfinite_or_negative_values(field, value):
    with pytest.raises(ValidationError):
        schema.ObservedActuals.model_validate({field: value})


def test_observation_accepts_explicit_zero_and_aware_timestamps():
    actual = schema.ObservedActuals(
        wall_minutes=0, files_touched=0, started_at_utc="2026-09-05T00:00:00Z"
    )
    assert actual.wall_minutes == 0
    assert actual.files_touched == 0
    with pytest.raises(ValidationError):
        schema.ObservedActuals(started_at_utc="2026-09-05T00:00:00")


@pytest.mark.parametrize(
    "path,value",
    [
        (("execution_profile", "context", "implicit_co_dispatch"), True),
        (("execution_profile", "modifiers", "warm_context"), 0.5),
        (("execution_profile", "modifiers", "spec_clarity"), 99.0),
        (("admission", "declared_cap_minutes"), -1),
        (("task_spec", "scope", "expected_files_touched"), -1),
    ],
)
def test_validated_request_cannot_be_mutated_in_place(request_data, path, value):
    request = schema.EstimateRequest.model_validate(request_data)
    before = request.model_dump_json()
    target = request
    for key in path[:-1]:
        target = getattr(target, key)
    with pytest.raises(ValidationError, match="frozen"):
        setattr(target, path[-1], value)
    assert request.model_dump_json() == before


@pytest.mark.parametrize("field", ["required_capabilities", "dependency_task_ids", "tags"])
def test_task_collections_are_immutable_without_changing_wire_arrays(request_data, field):
    request_data["task_spec"][field] = ["original"]
    task = schema.TaskSpec.model_validate(request_data["task_spec"])
    request_data["task_spec"][field].append("later")
    assert getattr(task, field) == ("original",)
    assert task.model_dump(mode="json")[field] == ["original"]
    assert task.model_json_schema()["properties"][field]["type"] == "array"
    with pytest.raises(ValidationError, match="frozen"):
        setattr(task, field, getattr(task, field) + ("duplicate", "duplicate"))


def test_all_contract_records_are_frozen():
    for model, *_ in SHAPES:
        assert model.model_config["frozen"] is True


def test_utc_timestamps_normalize_offsets_and_roundtrip(request_data):
    record = schema.ForecastRecord(
        schema_version="agent-estimate/forecast/v1",
        request=request_data,
        created_at_utc="2026-09-05T09:00:00+09:00",
        engine={"version": "caller-version", "registry_version": "caller-registry"},
    )
    actual = schema.ObservedActuals(
        started_at_utc="2026-09-05T09:00:00+09:00",
        completed_at_utc="2026-09-04T17:00:00-07:00",
    )
    restored_record = schema.ForecastRecord.model_validate_json(record.model_dump_json())
    restored_actual = schema.ObservedActuals.model_validate_json(actual.model_dump_json())
    for value in (
        restored_record.created_at_utc,
        restored_actual.started_at_utc,
        restored_actual.completed_at_utc,
    ):
        assert value.utcoffset() == timedelta(0)
        assert value.isoformat() == "2026-09-05T00:00:00+00:00"
