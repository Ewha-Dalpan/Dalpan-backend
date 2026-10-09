#Liner 호출, 이미지 변환, JSON 응답 검증

import base64
import json
import logging
from io import BytesIO

import requests
from django.conf import settings
from PIL import Image, ImageOps
from rest_framework import serializers

from .prompts import ANALYSIS_PROMPT, JUDGMENT_PROMPT, FACTOR_PLAN_PROMPT, SOURCE_SELECTION_PROMPT
from .search_client import SearchUnavailable, search_sources

logger = logging.getLogger(__name__)


LINER_ENDPOINT = "https://platform.liner.com/api/v1/chat/completions"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_BODY_BYTES = 32 * 1024 * 1024


class AIClientError(Exception):
    """사용자에게 공개해도 되는 AI 처리 오류."""


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


ANALYSIS_SCHEMA = object_schema({
    "analyzable": {"type": "boolean"},
    "summary": {"type": "string"},
    "conflict_core": {"type": "string"},
    "messages": {
        "type": "array",
        "items": object_schema({
            "image_order": {"type": "integer"},
            "speaker_side": {
                "type": "string",
                "enum": ["LEFT", "RIGHT", "UNKNOWN"],
            },
            "text": {"type": "string"},
        }),
    },
    "uncertainties": {
        "type": "array",
        "items": {"type": "string"},
    },
})


FACTOR_PROPERTIES = {
    "key": {"type": "string"},
    "side": {"type": "string", "enum": ["SELF", "OTHER"]},
    "name": {"type": "string"},
    "summary": {"type": "string"},
    "evidence": {"type": "array", "items": {"type": "integer"}},
}
PLAN_SCHEMA = object_schema({
    "factors": {"type": "array", "items": object_schema({
        **FACTOR_PROPERTIES,
        "scholar_query": {"type": "string"},
        "web_query": {"type": "string"},
    })},
})
SELECTION_SCHEMA = object_schema({
    "selections": {"type": "array", "items": object_schema({
        "factor_key": {"type": "string"},
        "source_id": {"type": ["string", "null"]},
        "reference_summary": {"type": "string"},
    })},
})
JUDGMENT_SCHEMA = object_schema({
    "judgeable": {"type": "boolean"},
    "title": {"type": "string"},
    "public_title": {"type": "string"},
    "public_summary": {"type": "string"},
    "fault_ratio": {"type": "integer"},
    "one_line": {"type": "string"},
    "case_summary": {"type": "string"},
    "judgment_text": {"type": "string"},
    "recommended_reply": {"type": "string"},
    "factors": {"type": "array", "items": object_schema({
        **FACTOR_PROPERTIES,
        "source_id": {"type": ["string", "null"]},
        "reference_summary": {"type": "string"},
    })},
})

class MessageResultSerializer(serializers.Serializer):
    image_order = serializers.IntegerField(min_value=0, max_value=5)
    speaker_side = serializers.ChoiceField(
        choices=["LEFT", "RIGHT", "UNKNOWN"],
    )
    text = serializers.CharField(max_length=10000)


class AnalysisResultSerializer(serializers.Serializer):
    analyzable = serializers.BooleanField()
    summary = serializers.CharField(max_length=10000)
    conflict_core = serializers.CharField(
        max_length=255,
        allow_blank=True,
    )
    messages = MessageResultSerializer(many=True, max_length=1000)
    uncertainties = serializers.ListField(
        child=serializers.CharField(max_length=2000),
        max_length=100,
    )


    def validate(self, attrs):
        if attrs["analyzable"]:
            if not attrs["conflict_core"] or not attrs["messages"]:
                raise serializers.ValidationError(
                    "갈등 핵심 또는 대화 내용이 없습니다."
                )
        return attrs


class FactorSerializer(serializers.Serializer):
    key = serializers.RegexField(r"^[a-z][a-z0-9_]{0,49}$")
    side = serializers.ChoiceField(choices=["SELF", "OTHER"])
    name = serializers.CharField(max_length=50)
    summary = serializers.CharField(max_length=5000)
    evidence = serializers.ListField(child=serializers.IntegerField(min_value=0), max_length=50)

class PlanFactorSerializer(FactorSerializer):
    scholar_query = serializers.CharField(max_length=300)
    web_query = serializers.CharField(max_length=300)

class PlanSerializer(serializers.Serializer):
    factors = PlanFactorSerializer(many=True, max_length=4)

    def validate_factors(self, factors):
        keys = [factor["key"] for factor in factors]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("판단 요소 키가 중복됩니다.")
        return factors

class FinalFactorSerializer(FactorSerializer):
    source_id = serializers.CharField(allow_null=True, max_length=100)
    reference_summary = serializers.CharField(allow_blank=True, max_length=5000)

class SelectionSerializer(serializers.Serializer):
    factor_key = serializers.CharField(max_length=50)
    source_id = serializers.CharField(allow_null=True, max_length=100)
    reference_summary = serializers.CharField(allow_blank=True, max_length=5000)

class SelectionsSerializer(serializers.Serializer):
    selections = SelectionSerializer(many=True, max_length=4)

class JudgmentResultSerializer(serializers.Serializer):
    judgeable = serializers.BooleanField()
    title = serializers.CharField(max_length=100, allow_blank=True)
    public_title = serializers.CharField(max_length=100, allow_blank=True)
    public_summary = serializers.CharField(max_length=5000, allow_blank=True)
    fault_ratio = serializers.IntegerField(min_value=0, max_value=100)
    one_line = serializers.CharField(max_length=255, allow_blank=True)
    case_summary = serializers.CharField(max_length=10000, allow_blank=True)
    judgment_text = serializers.CharField(max_length=20000)
    recommended_reply = serializers.CharField(max_length=5000, allow_blank=True)
    factors = FinalFactorSerializer(many=True, max_length=4)

    def validate(self, attrs):
        if attrs["judgeable"]:
            for field in ["title", "public_title", "public_summary", "one_line", "case_summary", "recommended_reply"]:
                if not attrs[field]:
                    raise serializers.ValidationError(f"{field} 값이 없습니다.")
            if not attrs["factors"]:
                raise serializers.ValidationError("판단 요소가 없습니다.")
        keys = [factor["key"] for factor in attrs["factors"]]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("판단 요소 키가 중복됩니다.")
        return attrs

def _call_liner(*, prompt, content, schema, schema_name, timeout=None):
    api_key = settings.LINER_API_KEY
    if not api_key:
        raise AIClientError("Liner API 설정이 없습니다.")

    payload = {
        "model": settings.LINER_MODEL,
        "stream": False,
        "store": False,
        "max_completion_tokens": settings.LINER_MAX_COMPLETION_TOKENS,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
    }

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise AIClientError("AI 요청 크기가 너무 큽니다.")

    try:
        response = requests.post(
            LINER_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            data=body,
            timeout=(5, timeout or settings.LINER_HTTP_TIMEOUT_SECONDS),
        )
    except requests.Timeout:
        raise AIClientError("AI 응답 시간이 초과되었습니다.") from None
    except requests.RequestException:
        raise AIClientError("AI 서버에 연결하지 못했습니다.") from None

    if not response.ok:
        # 외부 응답 본문은 사용자나 로그에 그대로 노출하지 않는다.
        errors = {
            400: "AI가 요청을 처리할 수 없습니다.",
            401: "Liner API 인증에 실패했습니다.",
            402: "Liner API 크레딧이 부족합니다.",
            413: "AI 요청 크기가 너무 큽니다.",
            429: "AI 요청이 많습니다. 잠시 후 다시 시도해 주세요.",
        }
        raise AIClientError(
            errors.get(
                response.status_code,
                f"AI 서버 오류가 발생했습니다. HTTP {response.status_code}",
            )
        )

    try:
        response_data = response.json()
        choice = response_data["choices"][0]

        if choice.get("finish_reason") != "stop":
            raise AIClientError("AI 응답이 정상적으로 완료되지 않았습니다.")

        message = choice["message"]
        if message.get("refusal"):
            raise AIClientError("AI가 이 요청에 대한 응답을 거절했습니다.")

        result = json.loads(message["content"])
        if not isinstance(result, dict):
            raise AIClientError("AI 응답 형식이 올바르지 않습니다.")

        return result

    except (KeyError, IndexError, TypeError, ValueError):
        raise AIClientError("AI 응답을 읽을 수 없습니다.") from None


def _validate_result(serializer_class, data):
    serializer = serializer_class(data=data)
    if not serializer.is_valid():
        raise AIClientError("AI 결과의 필드 또는 값이 올바르지 않습니다.")
    return dict(serializer.validated_data)


def _image_parts(images):
    parts = []
    total_bytes = 0

    for image_order, case_image in enumerate(images):
        # 저장소 API를 사용하므로 로컬 파일과 원격 저장소에 모두 대응한다.
        with case_image.image_key.open("rb") as image_file:
            with Image.open(image_file) as original:
                normalized = ImageOps.exif_transpose(original)

                # 지나치게 큰 사진만 축소하고 비율은 유지한다.
                normalized.thumbnail((4096, 4096))

                output = BytesIO()
                normalized.convert("RGB").save(
                    output,
                    format="JPEG",
                    quality=90,
                )
                image_bytes = output.getvalue()

        total_bytes += len(image_bytes)
        if total_bytes > MAX_IMAGE_BYTES:
            raise AIClientError("첨부 사진의 전체 크기가 너무 큽니다.")

        encoded = base64.b64encode(image_bytes).decode("ascii")
        parts.extend([
            {
                "type": "text",
                "text": f"다음 사진의 image_order는 {image_order}입니다.",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{encoded}",
                },
            },
        ])

    return parts


def analyze_case(*, relation, images):
    images = list(images)
    if not 1 <= len(images) <= 6:
        raise AIClientError("대화 사진이 1~6장 필요합니다.")

    content = [{
        "type": "text",
        "text": json.dumps(
            {
                "relation": relation,
                "instruction": "사진을 분석하고 JSON으로 응답해 주세요.",
            },
            ensure_ascii=False,
        ),
    }]
    content.extend(_image_parts(images))

    result = _call_liner(
        prompt=ANALYSIS_PROMPT,
        content=content,
        schema=ANALYSIS_SCHEMA,
        schema_name="case_analysis",
    )
    result = _validate_result(AnalysisResultSerializer, result)

    if not result["analyzable"]:
        raise AIClientError(
            "대화 사진을 분석할 수 없습니다. "
            "대화 내용이 선명하게 보이는 사진으로 다시 접수해 주세요."
        )

    if any(
        message["image_order"] >= len(images)
        for message in result["messages"]
    ):
        raise AIClientError("AI 결과의 사진 번호가 올바르지 않습니다.")

    return result


def judgment_context(*, situation, conflict_core, analysis):
    return {
        "relation": situation.relation,
        "original_summary": analysis["summary"],
        "confirmed_summary": situation.summary,
        "user_speaker_side": situation.user_speaker_side,
        "conflict_core": conflict_core,
        "messages": analysis["messages"],
        "uncertainties": analysis["uncertainties"],
    }

def _review_sources(factors, candidates, check_progress):
    if not candidates:
        return {}
    timeout = check_progress("REVIEWING_SOURCES")
    try:
        result = _call_liner(
            prompt=SOURCE_SELECTION_PROMPT,
            content=json.dumps({"factors": factors, "candidates": candidates}, ensure_ascii=False),
            schema=SELECTION_SCHEMA, schema_name="source_selection", timeout=timeout,
        )
        selections = _validate_result(SelectionsSerializer, result)["selections"]
    except AIClientError:
        logger.warning("Source relevance review unavailable")
        return None
    known_factors = {factor["key"] for factor in factors}
    index = {candidate["id"]: candidate for candidate in candidates}
    chosen = {}
    seen = set()
    for selection in selections:
        key = selection["factor_key"]
        source_id = selection["source_id"]
        if key not in known_factors or key in seen:
            raise AIClientError("참고 자료의 판단 요소가 올바르지 않습니다.")
        seen.add(key)
        if source_id is None:
            continue
        candidate = index.get(source_id)
        if not candidate or candidate["factor_key"] != key:
            raise AIClientError("검색하지 않은 참고 자료가 선택되었습니다.")
        if selection["reference_summary"].strip():
            chosen[key] = {**candidate, "reference_summary": selection["reference_summary"]}
    return chosen

def judge_case(*, situation, conflict_core, analysis, check_progress=None):
    # 2차에는 이미지나 추가 설명을 전달하지 않는다.
    if check_progress is None:
        check_progress = lambda step: settings.LINER_HTTP_TIMEOUT_SECONDS
    context = judgment_context(situation=situation, conflict_core=conflict_core, analysis=analysis)
    timeout = check_progress("PLANNING_FACTORS")
    plan = _validate_result(PlanSerializer, _call_liner(
        prompt=FACTOR_PLAN_PROMPT, content=json.dumps(context, ensure_ascii=False),
        schema=PLAN_SCHEMA, schema_name="factor_plan", timeout=timeout,
    ))["factors"]
    message_count = len(analysis["messages"])
    for factor in plan:
        if any(index >= message_count for index in factor["evidence"]):
            raise AIClientError("판단 요소의 대화 근거 번호가 올바르지 않습니다.")
    candidates = []
    scholar_completed = set()
    for factor in plan:
        timeout = check_progress("SEARCHING_SCHOLAR")
        try:
            results = search_sources(
                factor["scholar_query"], "SCHOLAR",
                timeout=min(timeout, settings.LINER_SEARCH_TIMEOUT_SECONDS),
            )
            scholar_completed.add(factor["key"])
        except SearchUnavailable:
            logger.warning("Scholar search unavailable: factor=%s", factor["key"])
            continue
        for result in results:
            candidates.append({**result, "id": f"scholar_{len(candidates)}", "factor_key": factor["key"]})
    scholar_review = _review_sources(plan, candidates, check_progress)
    selected = scholar_review or {}
    web_factors = [
        factor for factor in plan
        if scholar_review is not None and factor["key"] in scholar_completed and factor["key"] not in selected
    ]
    web_candidates = []
    for factor in web_factors:
        timeout = check_progress("SEARCHING_WEB")
        try:
            results = search_sources(
                factor["web_query"], "WEB",
                timeout=min(timeout, settings.LINER_SEARCH_TIMEOUT_SECONDS),
            )
        except SearchUnavailable:
            logger.warning("Web search unavailable: factor=%s", factor["key"])
            continue
        for result in results:
            web_candidates.append({**result, "id": f"web_{len(web_candidates)}", "factor_key": factor["key"]})
    selected.update(_review_sources(web_factors, web_candidates, check_progress) or {})
    timeout = check_progress("GENERATING_JUDGMENT")
    result = _validate_result(JudgmentResultSerializer, _call_liner(
        prompt=JUDGMENT_PROMPT,
        content=json.dumps({**context, "plan": plan, "selected_sources": list(selected.values())}, ensure_ascii=False),
        schema=JUDGMENT_SCHEMA, schema_name="case_judgment", timeout=timeout,
    ))
    if not result["judgeable"]:
        raise AIClientError("판단에 필요한 정보가 부족합니다: " + result["judgment_text"][:700])
    planned = {factor["key"]: factor for factor in plan}
    for factor in result["factors"]:
        original = planned.get(factor["key"])
        if not original or factor["side"] != original["side"]:
            raise AIClientError("판단 요소 또는 화자 구분이 올바르지 않습니다.")
        if any(index >= message_count for index in factor["evidence"]):
            raise AIClientError("판결의 대화 근거 번호가 올바르지 않습니다.")
        source_id = factor.pop("source_id")
        reference_summary = factor.pop("reference_summary")
        source = selected.get(factor["key"])
        if source_id is not None:
            if not source or source["id"] != source_id or not reference_summary.strip():
                raise AIClientError("판결의 참고 자료가 검색 결과와 일치하지 않습니다.")
            factor["source"] = {
                key: source[key] for key in
                ("kind", "title", "url", "publisher", "authors", "published_date")
            }
            factor["source"]["reference_summary"] = reference_summary
        else:
            if reference_summary:
                raise AIClientError("출처 없이 참고 자료 설명이 생성되었습니다.")
            factor["source"] = None
    result["input_snapshot"] = context
    return result
