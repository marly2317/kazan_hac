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
    #: Описание дописано, а не набирается прямо сейчас.
    #:
    #: Разбор идёт по мере ввода, поэтому «куртк», «куртка», «куртка зим» — тоже запросы. Для
    #: пользователя разницы нет, а для сбора наблюдений о нехватке в справочнике она
    #: решающая: без этого флага в отчёт попали бы все обрывки слов, которые человек набрал
    #: по дороге, и найти в нём настоящие пробелы стало бы невозможно. Интерфейс ставит `1`
    #: только тогда, когда ввод закончен: нажата кнопка, Enter или подсказка.
    settled = serializers.BooleanField(required=False, default=False)


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
    #: How many extracted characteristics this position explicitly confirms.
    support = serializers.IntegerField()
    #: ``«Характеристика: значение»`` lines separating this position from the other candidates.
    distinctions = serializers.ListField(child=serializers.CharField())


class NextStepSerializer(serializers.Serializer):
    characteristic = serializers.CharField()
    options = serializers.ListField(child=serializers.CharField())
    splits_into = serializers.IntegerField()
    #: A number with a unit is expected, not one of `options` verbatim.
    is_quantitative = serializers.BooleanField()
    unit = serializers.CharField()


class AnalysisSerializer(serializers.Serializer):
    """Documentation of the payload the view already builds."""

    original = serializers.CharField()
    corrected = serializers.CharField()
    #: True when `corrected` differs from `original` by more than letter case.
    text_changed = serializers.BooleanField()
    state = serializers.CharField()
    resolved_code = serializers.CharField(allow_null=True)
    #: How strongly the leading candidate is named by the description, in ``[0, 1]``.
    confidence = serializers.FloatField()
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
            "text_changed": analysis.text_changed,
            "state": analysis.state.value,
            "resolved_code": resolved.code if resolved else None,
            "confidence": round(analysis.confidence, 4),
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
                    "score": round(c.rank, 4),
                    "support": c.support,
                    "distinctions": c.distinctions,
                }
                for c in analysis.candidates
            ],
            "next_steps": [
                {
                    "characteristic": step.characteristic,
                    "options": step.options,
                    "splits_into": step.splits_into,
                    "is_quantitative": step.is_quantitative,
                    "unit": step.unit,
                }
                for step in analysis.next_steps
            ],
        }


assert MAX_DESCRIPTION >= MAX_QUERY_LENGTH
