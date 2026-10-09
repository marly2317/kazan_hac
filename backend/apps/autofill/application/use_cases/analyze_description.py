"""Turn a free-text product description into unified characteristics and a КТРУ code."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal

from apps.autofill.application.extraction import (
    NumberMention,
    characteristic_accepts,
    characteristic_accepts_text,
    find_numbers,
    find_texts,
)
from apps.autofill.application.ports import KtruCatalog, QueryCorrector, UnitNormalizer
from apps.autofill.domain.entities import (
    Analysis,
    ExtractedCharacteristic,
    Issue,
    NarrowingStep,
    NextStep,
    PositionCandidate,
)
from apps.autofill.domain.enums import IssueKind
from apps.ktru.domain.entities import Characteristic

#: How many positions the name search puts on the table before characteristics narrow them.
#: Wide enough that the right one is in there, small enough to hold their characteristics.
CANDIDATE_POOL = 300

#: How many candidates the API returns while the answer is still ambiguous.
SHOWN_CANDIDATES = 10

#: How many "add this next" prompts to offer.
MAX_NEXT_STEPS = 3

#: What one confirmed characteristic is worth against the name-similarity score. Enough to
#: order positions with near-identical names, not enough to overturn a much better name match.
SUPPORT_BOOST = 0.35


@dataclass(frozen=True)
class AnalyzeDescriptionInput:
    description: str
    correct_typos: bool = True


class AnalyzeDescription:
    """The narrowing pipeline.

    Deliberately ordered so each step can be *shown*: the description is corrected, candidate
    positions are proposed by name, then every characteristic recognised in the text removes
    the candidates that disagree with it. The task asks the user to see that happen, so each
    filter records what it eliminated instead of the code just returning an answer.
    """

    def __init__(
        self,
        catalog: KtruCatalog,
        corrector: QueryCorrector,
        normalize_unit: UnitNormalizer,
    ) -> None:
        self._catalog = catalog
        self._corrector = corrector
        self._normalize_unit = normalize_unit

    def execute(self, data: AnalyzeDescriptionInput) -> Analysis:
        correction = (
            self._corrector.execute(data.description)
            if data.correct_typos
            else _unchanged(data.description)
        )
        text = correction.corrected

        issues: list[Issue] = []
        if correction.changed:
            issues.append(
                Issue(
                    kind=IssueKind.TYPO,
                    characteristic="",
                    raw=correction.original,
                    message=(
                        f"В описании исправлены опечатки: «{correction.original}» → "
                        f"«{correction.corrected}»."
                    ),
                    suggestions=[correction.corrected],
                )
            )

        if not text.strip():
            return Analysis(
                original=correction.original,
                corrected=text,
                characteristics=[],
                issues=issues,
                steps=[],
                candidates=[],
                next_steps=[],
                total_candidates=0,
            )

        candidates = self._catalog.find_positions(text, limit=CANDIDATE_POOL)
        steps = [
            NarrowingStep(
                label="Поиск по наименованию",
                detail=f"«{text}»",
                before=0,
                after=len(candidates),
            )
        ]
        if not candidates:
            return Analysis(
                original=correction.original,
                corrected=text,
                characteristics=[],
                issues=issues,
                steps=steps,
                candidates=[],
                next_steps=[],
                total_candidates=0,
            )

        by_code = self._catalog.characteristics_for([c.code for c in candidates])
        pool = [c for c in candidates if c.code in by_code] or candidates

        extracted, numbers = self._extract(text, by_code)

        # Each recognised characteristic is a filter, applied in turn so the elimination is
        # visible rather than happening all at once.
        supports: Counter[str] = Counter()
        for item in extracted:
            before = len(pool)
            kept = []
            for candidate in pool:
                verdict = _verdict(by_code.get(candidate.code, []), item)
                if verdict is CONTRADICTS:
                    continue
                if verdict is SUPPORTS:
                    supports[candidate.code] += 1
                kept.append(candidate)

            # A filter that empties the set is reported but not applied: leaving the user with
            # nothing is worse than leaving them with a choice.
            if kept:
                pool = kept
            steps.append(
                NarrowingStep(
                    label=item.name,
                    detail=f"{item.value} {item.unit}".strip(),
                    before=before,
                    after=len(pool) if kept else before,
                )
            )

        # Support is *added* to the name score rather than ranked ahead of it. Ordering by
        # support first let a position that happens to declare «Сезонность» outrank one whose
        # name is the description word for word — one matched characteristic is weaker evidence
        # than a name that scores four times higher.
        pool.sort(
            key=lambda candidate: -(
                candidate.score + SUPPORT_BOOST * supports[candidate.code]
            )
        )

        issues.extend(self._value_issues(numbers, extracted, by_code, pool))
        issues.extend(self._missing_required(by_code, pool, extracted))

        return Analysis(
            original=correction.original,
            corrected=text,
            characteristics=extracted,
            issues=issues,
            steps=steps,
            candidates=pool[:SHOWN_CANDIDATES],
            next_steps=self._next_steps(by_code, pool, extracted),
            total_candidates=len(pool),
        )

    # --- extraction ---------------------------------------------------------------------

    def _extract(
        self, text: str, by_code: dict[str, list[Characteristic]]
    ) -> tuple[list[ExtractedCharacteristic], list[NumberMention]]:
        every: list[Characteristic] = [c for group in by_code.values() for c in group]

        found: dict[str, ExtractedCharacteristic] = {}

        for mention in find_texts(text, every):
            found.setdefault(
                mention.characteristic,
                ExtractedCharacteristic(
                    name=mention.characteristic, raw=mention.raw, value=mention.value
                ),
            )

        numbers = find_numbers(text, self._normalize_unit)
        by_name: dict[str, list[Characteristic]] = defaultdict(list)
        for characteristic in every:
            by_name[characteristic.name].append(characteristic)

        for mention in numbers:
            if not mention.unit:
                continue
            # Attribute the number to every characteristic measured in that unit which also
            # permits the value. A number alone cannot say which characteristic it belongs to,
            # so the unit plus the permitted range is what disambiguates it.
            for name, group in by_name.items():
                if name in found:
                    continue
                if any(characteristic_accepts(c, mention) for c in group):
                    found[name] = ExtractedCharacteristic(
                        name=name,
                        raw=mention.raw,
                        value=_plain(mention.number),
                        unit=mention.unit,
                        raw_unit=mention.raw_unit,
                    )
                    break

        return list(found.values()), numbers

    # --- problems -----------------------------------------------------------------------

    def _value_issues(
        self,
        numbers: list[NumberMention],
        extracted: list[ExtractedCharacteristic],
        by_code: dict[str, list[Characteristic]],
        pool: list[PositionCandidate],
    ) -> list[Issue]:
        """Numbers nothing in the surviving candidates can accept."""
        issues: list[Issue] = []
        taken = {item.raw for item in extracted}
        live: list[Characteristic] = [
            c for candidate in pool for c in by_code.get(candidate.code, [])
        ]

        for mention in numbers:
            if mention.raw in taken:
                continue
            if mention.raw_unit and not mention.unit:
                issues.append(
                    Issue(
                        kind=IssueKind.UNKNOWN_UNIT,
                        characteristic="",
                        raw=mention.raw,
                        message=(
                            f"Единица измерения «{mention.raw_unit}» отсутствует в ОКЕИ, "
                            f"поэтому значение не удалось сопоставить."
                        ),
                        suggestions=sorted(
                            {unit.name for c in live for unit in c.units}
                        )[:6],
                    )
                )
                continue

            if not mention.unit:
                continue

            # The unit is real and used by a characteristic, but no permitted value covers the
            # number: that is an out-of-range value, and КТРУ can say what the range is.
            matching = [c for c in live if any(u.name == mention.unit for u in c.units)]
            if matching and not any(characteristic_accepts(c, mention) for c in matching):
                allowed = sorted(
                    {
                        value.describe()
                        for c in matching[:4]
                        for value in c.values
                    }
                )[:6]
                issues.append(
                    Issue(
                        kind=IssueKind.OUT_OF_RANGE,
                        characteristic=matching[0].name,
                        raw=mention.raw,
                        message=(
                            f"Значение «{mention.raw}» не входит ни в один допустимый "
                            f"диапазон характеристики «{matching[0].name}»."
                        ),
                        suggestions=allowed,
                    )
                )
        return issues

    @staticmethod
    def _missing_required(
        by_code: dict[str, list[Characteristic]],
        pool: list[PositionCandidate],
        extracted: list[ExtractedCharacteristic],
    ) -> list[Issue]:
        """Required characteristics of the leading candidate that the description omits."""
        if not pool:
            return []
        leader = by_code.get(pool[0].code, [])
        supplied = {item.name for item in extracted}
        missing = [c.name for c in leader if c.is_required and c.name not in supplied]
        if not missing:
            return []
        return [
            Issue(
                kind=IssueKind.MISSING_REQUIRED,
                characteristic=", ".join(missing[:6]),
                raw="",
                message=(
                    "Для подбора кода не хватает обязательных характеристик: "
                    + ", ".join(f"«{name}»" for name in missing[:6])
                    + "."
                ),
                suggestions=missing[:6],
            )
        ]

    @staticmethod
    def _next_steps(
        by_code: dict[str, list[Characteristic]],
        pool: list[PositionCandidate],
        extracted: list[ExtractedCharacteristic],
    ) -> list[NextStep]:
        """What to add next, ranked by how well it splits what is left.

        A characteristic every remaining candidate answers the same way tells the user nothing;
        one that divides them into many groups is the fastest way to a single code.
        """
        if len(pool) <= 1:
            return []

        supplied = {item.name for item in extracted}
        groups: dict[str, Counter[str]] = defaultdict(Counter)

        for candidate in pool:
            for characteristic in by_code.get(candidate.code, []):
                if characteristic.name in supplied:
                    continue
                for value in characteristic.values:
                    label = value.describe()
                    if label:
                        groups[characteristic.name][label] += 1

        ranked = sorted(
            groups.items(), key=lambda pair: len(pair[1]), reverse=True
        )
        return [
            NextStep(
                characteristic=name,
                options=[label for label, _ in counts.most_common(6)],
                splits_into=len(counts),
            )
            for name, counts in ranked[:MAX_NEXT_STEPS]
            if len(counts) > 1
        ]


#: A position either backs up an extracted value, rules it out, or says nothing about it.
SUPPORTS, CONTRADICTS, SILENT = "supports", "contradicts", "silent"


def _verdict(characteristics: list[Characteristic], item: ExtractedCharacteristic) -> str:
    """What a position has to say about one extracted characteristic.

    The distinction between *contradicts* and *silent* is what keeps the narrowing honest.
    КТРУ positions declare different characteristics, so «Куртка зимняя из кожи» may simply not
    carry a «Сезонность» entry while «Обувь» does. Treating that silence as disagreement
    eliminated the right answer and left the wrong one — so only an explicit mismatch removes
    a candidate, and silence merely fails to support it.
    """
    for characteristic in characteristics:
        if characteristic.name != item.name:
            continue
        if item.unit:
            try:
                number = Decimal(item.value)
            except (ArithmeticError, ValueError):
                return SILENT
            return (
                SUPPORTS
                if any(value.contains(number) for value in characteristic.values)
                else CONTRADICTS
            )
        return (
            SUPPORTS if characteristic_accepts_text(characteristic, item.value) else CONTRADICTS
        )
    return SILENT


def _plain(number) -> str:
    return format(number.normalize(), "f")


def _unchanged(text: str):
    from apps.spellcheck.domain.correction import Correction

    return Correction.unchanged(" ".join((text or "").split()))
