"""DRF serializers for the autofill surface."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.autofill.domain.entities import Analysis
from apps.autofill.domain.enums import ISSUE_KIND_CHOICES
from apps.spellcheck.domain.correction import MAX_QUERY_LENGTH

#: A description is a sentence or two, not a document.
MAX_DESCRIPTION = 1000


class AnalyzeRequestSerializer(serializers.Serializer):
    description = serializers.CharField(
        allow_blank=True, max_length=MAX_DESCRIPTION, trim_whitespace=True
    )
    correct = serializers.BooleanField(required=False, default=True)


class CharacteristicSerializer(serializers.Serializer):
    name = serializers.CharField()
    raw = serializers.CharField()
    value = serializers.CharField()
    unit = serializers.CharField()
    raw_unit = serializers.CharField()
    was_unified = serializers.BooleanField()


class IssueSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=ISSUE_KIND_CHOICES)
    characteristic = serializers.CharField()
    raw = serializers.CharField()
    message = serializers.CharField()
    suggestions = serializers.ListField(child=serializers.CharField())


class StepSerializer(serializers.Serializer):
    label = serializers.CharField()
    detail = serializers.CharField()
    before = serializers.IntegerField()
    after = serializers.IntegerField()
    eliminated = serializers.IntegerField()


class CandidateSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    okpd2_code = serializers.CharField()
    score = serializers.FloatField()


class NextStepSerializer(serializers.Serializer):
    characteristic = serializers.CharField()
    options = serializers.ListField(child=serializers.CharField())
    splits_into = serializers.IntegerField()


class AnalysisSerializer(serializers.Serializer):
    """Documentation of the payload the view already builds."""

    original = serializers.CharField()
    corrected = serializers.CharField()
    state = serializers.CharField()
    resolved_code = serializers.CharField(allow_null=True)
    total_candidates = serializers.IntegerField()
    characteristics = CharacteristicSerializer(many=True)
    issues = IssueSerializer(many=True)
    steps = StepSerializer(many=True)
    candidates = CandidateSerializer(many=True)
    next_steps = NextStepSerializer(many=True)

    @staticmethod
    def from_analysis(analysis: Analysis) -> dict[str, Any]:
        resolved = analysis.resolved
        return {
            "original": analysis.original,
            "corrected": analysis.corrected,
            "state": analysis.state.value,
            "resolved_code": resolved.code if resolved else None,
            "total_candidates": analysis.total_candidates,
            "characteristics": [
                {
                    "name": item.name,
                    "raw": item.raw,
                    "value": item.value,
                    "unit": item.unit,
                    "raw_unit": item.raw_unit,
                    "was_unified": item.was_unified,
                }
                for item in analysis.characteristics
            ],
            "issues": [
                {
                    "kind": issue.kind.value,
                    "characteristic": issue.characteristic,
                    "raw": issue.raw,
                    "message": issue.message,
                    "suggestions": issue.suggestions,
                }
                for issue in analysis.issues
            ],
            "steps": [
                {
                    "label": step.label,
                    "detail": step.detail,
                    "before": step.before,
                    "after": step.after,
                    "eliminated": step.eliminated,
                }
                for step in analysis.steps
            ],
            "candidates": [
                {
                    "code": c.code,
                    "name": c.name,
                    "okpd2_code": c.okpd2_code,
                    "score": round(c.score, 4),
                }
                for c in analysis.candidates
            ],
            "next_steps": [
                {
                    "characteristic": step.characteristic,
                    "options": step.options,
                    "splits_into": step.splits_into,
                }
                for step in analysis.next_steps
            ],
        }


assert MAX_DESCRIPTION >= MAX_QUERY_LENGTH
