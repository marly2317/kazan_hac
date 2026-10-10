"""Turn a free-text product description into unified characteristics and a КТРУ code."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal

from apps.autofill.application.extraction import (
    NumberMention,
    characteristic_accepts,
    characteristic_accepts_text,
    find_numbers,
    find_texts,
    in_characteristic_units,
    label_affinity,
    names_a_characteristic,
    stem,
    unit_is_compatible,
)
from apps.autofill.application.ports import (
    CatalogNarrowing,
    KtruCatalog,
    Lexicon,
    QueryCorrector,
    UnitNormalizer,
)
from apps.autofill.domain.entities import (
    Analysis,
    ExtractedCharacteristic,
    Issue,
    NarrowingStep,
    NextStep,
    PositionCandidate,
)
from apps.autofill.domain.enums import IssueKind
from apps.autofill.domain.matching import (
    NAME_OVERLAP_FLOOR,
    name_coverage,
    name_overlap,
)
from apps.autofill.domain.query import SplitQuery, split
from apps.ktru.domain.entities import Characteristic

#: Any run of letters — position names are split on this to find the product's own words.
_WORD_RE = re.compile(r"[а-яёa-z]+", re.IGNORECASE)

#: How many positions the name search puts on the table before characteristics narrow them.
#: Wide enough that the right one is in there, small enough to hold their characteristics.
CANDIDATE_POOL = 300

#: How many candidates the API returns while the answer is still ambiguous.
SHOWN_CANDIDATES = 10

#: Label of the step that drops positions the description does not name. Named here because
#: the abstention branch keeps exactly this step and the initial search, and nothing else.
NAMED_STEP = "Соответствие наименованию"

#: Подпись шага, на котором описание разделяется на наименование и характеристики.
#:
#: Это первый шаг сужения и единственный, который ничего не отбрасывает, — но показывать его
#: нужно: дальше весь поиск идёт по называющей части, и пользователь вправе видеть, что
#: сервис счёл названием товара, а что его описанием.
SPLIT_STEP = "Разделение описания"

#: How many "add this next" prompts to offer.
MAX_NEXT_STEPS = 3



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
        lexicon: Lexicon | None = None,
        narrowing: CatalogNarrowing | None = None,
    ) -> None:
        self._catalog = catalog
        self._corrector = corrector
        self._normalize_unit = normalize_unit
        # Без словаря описание не разделяется, и поиск идёт по тексту целиком — прежнее
        # поведение. Нужно это тестам: разделение проверяется своими тестами на своих
        # словарях, а сто девяносто тестов сужения не должны зависеть от содержимого
        # справочника.
        self._lexicon = lexicon
        # Сужение средствами движка — возможность, а не обязанность: на Postgres его нет, и
        # тогда сужение делает перебор ниже. См. порт `CatalogNarrowing`.
        self._narrowing = narrowing

    def execute(self, data: AnalyzeDescriptionInput) -> Analysis:
        correction = (
            self._corrector.execute(data.description)
            if data.correct_typos
            else _unchanged(data.description)
        )
        text = correction.corrected

        issues: list[Issue] = _correction_issues(correction)

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

        # Описание разделяется на называющую часть и описывающую, и позиции ищутся **первой**.
        # До разделения в поиск по наименованиям уходило описание целиком: слова «48» и
        # «размер» участвовали в сравнении с наименованиями позиций, хотя в наименованиях их
        # не бывает — они не помогали найти куртку, а размывали оценку.
        query = _split(text, self._lexicon)

        candidates = self._catalog.find_positions(query, limit=CANDIDATE_POOL)
        steps = []
        if query.is_split:
            steps.append(
                NarrowingStep(
                    label=SPLIT_STEP,
                    detail=f"наименование: «{query.name}» · характеристики: «{query.attributes}»",
                    before=0,
                    after=0,
                )
            )
        steps.append(
            NarrowingStep(
                label="Поиск по наименованию",
                detail=f"«{query.name}»",
                before=0,
                after=len(candidates),
            )
        )
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

        # Насколько каждое найденное наименование **названо** описанием. Считается здесь, до
        # всего остального, потому что это и есть решение о наименовании.
        candidates = [
            replace(
                candidate,
                name_overlap=name_overlap(text, candidate.name),
                name_coverage=name_coverage(text, candidate.name),
            )
            for candidate in candidates
        ]

        # Наименование определяется **первым**, и только потом характеристики.
        #
        # Порядок не ради скорости. `NAME_OVERLAP_FLOOR` и так запрещает отвечать позицией,
        # которую описание не называет, — то есть решение о наименовании логически
        # предшествует всему остальному уже сейчас. Применять его последним значило бы
        # распознавать характеристики по пулу из трёхсот позиций, а потом выбрасывать
        # распознанное вместе с позициями: «Высота изножья (от спального места): 150 мм» для
        # отвёртки — не частичный успех, а уверенное утверждение о кровати, которой
        # пользователь не называл. Теперь оно просто не вычисляется.
        #
        # Выигрывает и распознавание. `_attribute` перебирает характеристики кандидатов, и на
        # трёхстах позициях единица сужает лишь до «всего, что измеряется в миллиметрах» —
        # для шкафа это высота, ширина, глубина и ещё дюжина. На разрешённом наименовании
        # словарь характеристик — десятки, и число почти всегда достаётся одной.
        #
        # Разрешается **группа одноимённых позиций**, а не одна. КТРУ держит двенадцать
        # позиций «Молоток» и три «Куртка зимняя из кожи», различающихся только
        # характеристиками: сузить дальше наименованием нечем, и дальше действительно дело за
        # характеристиками.
        named = [c for c in candidates if c.name_overlap >= NAME_OVERLAP_FLOOR]

        if not named:
            # Воздержание — здесь и сразу, до распознавания. Ближайшие промахи называются
            # промахами; больше о них сказать нечего, и советовать по ним нечего. Уцелел сам
            # поиск: сколько наименований просмотрено и что ни одно из них описание не назвало.
            issues.append(_no_match(text, candidates))
            candidates.sort(key=lambda candidate: (-candidate.rank, candidate.code))
            return Analysis(
                original=correction.original,
                corrected=text,
                characteristics=[],
                issues=issues,
                steps=steps,
                candidates=candidates[:SHOWN_CANDIDATES],
                next_steps=[],
                total_candidates=len(candidates),
            )

        if len(named) < len(candidates):
            steps.append(
                NarrowingStep(
                    label=NAMED_STEP,
                    detail="отброшены позиции, которые описание не называет",
                    before=len(candidates),
                    after=len(named),
                )
            )

        by_code = self._catalog.characteristics_for([c.code for c in named])
        pool = [c for c in named if c.code in by_code] or named

        # Слова, которыми называется сам товар. Они не могут служить подсказкой к тому, какой
        # характеристике принадлежит число: в наименованиях характеристик молотка слово
        # «молоток» встречается сплошь и рядом.
        product_stems = frozenset(
            stem(word) for candidate in pool for word in _name_words(candidate.name)
        )

        extracted, numbers, attributed = self._extract(text, by_code, product_stems)

        if self._narrowing is not None and extracted:
            pool, by_code, narrowed_steps, supports = self._narrow_in_engine(
                query, extracted, pool, by_code
            )
            steps.extend(narrowed_steps)
        else:
            pool, narrowed_steps, supports = _narrow_by_hand(pool, by_code, extracted)
            steps.extend(narrowed_steps)

        # Support is *added* to the name score rather than ranked ahead of it — see
        # `PositionCandidate.rank`. Ordering by support first let a position that happens to
        # declare «Сезонность» outrank one whose name is the description word for word; one
        # matched characteristic is weaker evidence than a name that scores four times higher.
        #
        # Совпадение с наименованием здесь уже посчитано — до сужения, вместе с решением о
        # наименовании, — поэтому добавляется только поддержка. Так порядок, который видит
        # пользователь, и уверенность, по которой решается состояние, остаются одними и теми
        # же числами.
        pool = [replace(candidate, support=supports[candidate.code]) for candidate in pool]

        pool.sort(key=lambda candidate: (-candidate.rank, candidate.code))

        issues.extend(
            self._value_issues(numbers, extracted, attributed, by_code, pool, product_stems)
        )

        issues.extend(self._missing_required(by_code, pool, extracted))

        return Analysis(
            original=correction.original,
            corrected=text,
            characteristics=extracted,
            issues=issues,
            steps=steps,
            candidates=_with_distinctions(pool[:SHOWN_CANDIDATES], by_code),
            next_steps=self._next_steps(by_code, pool, extracted),
            total_candidates=len(pool),
        )

    def _narrow_in_engine(
        self,
        query: SplitQuery,
        extracted: list[ExtractedCharacteristic],
        pool: list[PositionCandidate],
        by_code: dict[str, list[Characteristic]],
    ) -> tuple[
        list[PositionCandidate],
        dict[str, list[Characteristic]],
        list[NarrowingStep],
        Counter[str],
    ]:
        """Сужение одним запросом к движку — по всему индексу, а не по отобранному пулу.

        Разница с перебором не в правиле, а в охвате. Перебор мог отбросить только то, что уже
        попало в пул по наименованию, поэтому позиция, чьё наименование оказалось на 301-м
        месте, не восстанавливалась никакими совпавшими характеристиками. Условие в запросе
        этого ограничения не имеет.

        Счётчики «было — стало» приходят из `track_total_hits`, то есть это точное число
        позиций, а не длина обрезанной страницы.
        """
        narrowed = self._narrowing.narrow(
            query,
            extracted,
            # Сужение идёт внутри разрешённого наименования: позиция, которую описание не
            # называет, уже отвергнута порогом, и искать её характеристиками значило бы
            # искать среди отвергнутого.
            within=[candidate.code for candidate in pool],
            limit=CANDIDATE_POOL,
        )
        if not narrowed.candidates:
            # Пустой результат не применяется — ровно по той же причине, по которой его не
            # применяет перебор: оставить пользователя ни с чем хуже, чем оставить ему выбор.
            return pool, by_code, [], Counter()

        # Совпадение с наименованием посчитано до сужения; движок о нём не знает и вернул бы
        # нули. Поэтому от его ответа берётся только то, что он действительно решал, — остался
        # ли кандидат и чем он подтверждён, — а меры наименования переносятся из прежних.
        known = {candidate.code: candidate for candidate in pool}
        narrowed_pool = [
            replace(known[candidate.code], support=candidate.support)
            for candidate in narrowed.candidates
            if candidate.code in known
        ]
        if not narrowed_pool:
            return pool, by_code, [], Counter()
        pool = narrowed_pool

        steps = [
            NarrowingStep(
                label=name,
                detail=f"{item.value} {item.unit}".strip(),
                before=before,
                after=after,
            )
            for item, (name, before, after) in zip(extracted, narrowed.steps, strict=True)
        ]
        supports = Counter({candidate.code: candidate.support for candidate in pool})
        return pool, by_code, steps, supports

    # --- extraction ---------------------------------------------------------------------

    def _extract(
        self,
        text: str,
        by_code: dict[str, list[Characteristic]],
        product_stems: frozenset[str],
    ) -> tuple[
        list[ExtractedCharacteristic], list[NumberMention], list[tuple[str, NumberMention]]
    ]:
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

        attributed_numbers: list[tuple[str, NumberMention]] = []
        for mention in numbers:
            attributed = self._attribute(
                mention, by_name, taken=set(found), product_stems=product_stems
            )
            if attributed is None:
                continue
            name, expressed = attributed
            attributed_numbers.append((name, mention))
            # `raw` keeps what the user wrote and `value`/`unit` carry the unified form, so the
            # interface can show «500 г → 0.5 Килограмм» rather than silently substituting one
            # for the other. `was_unified` is derived from exactly that difference.
            found[name] = ExtractedCharacteristic(
                name=name,
                raw=mention.raw,
                value=_plain(expressed.number),
                unit=expressed.unit,
                raw_unit=mention.raw_unit,
            )

        return list(found.values()), numbers, attributed_numbers

    @staticmethod
    def _attribute(
        mention: NumberMention,
        by_name: dict[str, list[Characteristic]],
        taken: set[str],
        product_stems: frozenset[str],
    ) -> tuple[str, NumberMention] | None:
        """Which characteristic a number belongs to, or ``None`` when nothing claims it.

        Three pieces of evidence, in order of how much they actually tell us:

        1. **the words beside the number** — «высота» next to «2000 мм» is the user naming the
           characteristic. This ranks first because it is the only evidence that distinguishes
           «Высота шкафа» from «Глубина шкафа», both of which are millimetres and both of which
           accept 2000;
        2. **the unit** — narrows to the characteristics measured that way;
        3. **the permitted values** — the number has to be one КТРУ allows.

        Candidates are considered in name order so the result does not depend on the order
        positions came back from the database.
        """
        best: tuple[int, int] | None = None
        chosen: tuple[str, NumberMention] | None = None

        for name in sorted(by_name):
            if name in taken:
                continue
            group = by_name[name]
            accepting = [c for c in group if characteristic_accepts(c, mention)]
            affinity = label_affinity(name, mention.context, product_stems)

            if affinity:
                # The user named this characteristic, so the number belongs to it **whether or
                # not КТРУ permits the value**. Requiring acceptance here is what hid
                # out-of-range values: «матрац длина 400 см» could not be filed under «Длина»
                # (permitted 60–250 см), so it silently landed on «Высота», whose range is
                # open — the one real error in the description disappeared, and «Длина» was
                # then reported as *missing*. An unacceptable value on the right
                # characteristic is exactly what the validation step needs to see.
                #
                # A qualitative characteristic is still skipped unless it accepts the number:
                # a number is not one of its enumerated texts, and the text pass already
                # claimed the ones that are.
                if not accepting and not any(c.is_quantitative for c in group):
                    continue
            else:
                # No label: the unit and the permitted values are the only evidence, and
                # neither alone is enough. Without both, the only thing known is that the
                # value happens to be allowed — true of hundreds of characteristics. Guessing
                # here is how a bare «48» ends up filed under whichever was looked at first.
                if not accepting or not mention.unit:
                    continue

            fits_unit = any(unit_is_compatible(c, mention) for c in group)
            key = (affinity, 1 if (mention.unit and fits_unit) else 0)
            if best is None or key > best:
                # The mention as the *winning* characteristic measures it, so a conversion is
                # reported in the unit КТРУ states. Preferring one that accepted the value
                # keeps the conversion target sensible when the group disagrees.
                base = accepting[0] if accepting else group[0]
                expressed = in_characteristic_units(base, mention) or mention
                best, chosen = key, (name, expressed)

        return chosen

    # --- problems -----------------------------------------------------------------------

    def _value_issues(
        self,
        numbers: list[NumberMention],
        extracted: list[ExtractedCharacteristic],
        attributed: list[tuple[str, NumberMention]],
        by_code: dict[str, list[Characteristic]],
        pool: list[PositionCandidate],
        product_stems: frozenset[str],
    ) -> list[Issue]:
        """Everything wrong with the numbers in the description, with КТРУ's reasons."""
        live: list[Characteristic] = [
            c for candidate in pool for c in by_code.get(candidate.code, [])
        ]
        issues: list[Issue] = []

        # Values that *were* attributed are checked first, against the characteristic they
        # were attributed to. Skipping them — on the grounds that attribution had already
        # succeeded — was the second half of the hidden-error problem: attribution no longer
        # requires the value to be permitted, so «матрац длина 400 см» now reaches «Длина»
        # and this is where КТРУ gets to say 400 см is not one of 60…250.
        for name, mention in attributed:
            named = next((c for c in live if c.name == name), None)
            if named is None:
                continue
            issue = _judge(named, mention)
            if issue is not None:
                issues.append(issue)

        claimed = {mention.raw for _, mention in attributed} | {item.raw for item in extracted}

        for mention in numbers:
            if mention.raw in claimed:
                continue

            # A word ОКЕИ does not know is only an error if it was not a characteristic name
            # standing where a unit happened to fit. `48 размер` is valid input.
            if mention.unit_is_unknown and not names_a_characteristic(mention.raw_unit, live):
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

            # The characteristic the user was *naming* — the words beside the number — decides
            # what the complaint is about. Reporting against "the first characteristic that
            # uses this unit" named the wrong one: «2000 мм высота» would be judged against
            # whichever millimetre characteristic came back first, and the explanation quoted
            # that one's ranges, so a correct value could be called out against a range it was
            # never meant to satisfy.
            named = _named_characteristic(live, mention, product_stems)
            if named is None:
                continue

            issue = _judge(named, mention)
            if issue is not None:
                issues.append(issue)
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
        quantitative: dict[str, bool] = {}
        units: dict[str, str] = {}

        for candidate in pool:
            for characteristic in by_code.get(candidate.code, []):
                if characteristic.name in supplied:
                    continue
                quantitative[characteristic.name] = characteristic.is_quantitative
                for unit in characteristic.units:
                    units.setdefault(characteristic.name, unit.name)
                for value in characteristic.values:
                    label = value.describe()
                    if label:
                        groups[characteristic.name][label] += 1

        # Qualitative characteristics first, however many values the quantitative ones have.
        # Ranking by value count alone put «Масса молотка» — eleven numeric ranges — above
        # «Материал бойка» — three words the user can simply click. The question that can be
        # answered in one tap is the better question to ask, even if it splits the pool less.
        ranked = sorted(
            groups.items(),
            key=lambda pair: (quantitative.get(pair[0], False), -len(pair[1]), pair[0]),
        )
        return [
            NextStep(
                characteristic=name,
                options=[label for label, _ in counts.most_common(6)],
                splits_into=len(counts),
                is_quantitative=quantitative.get(name, False),
                unit=units.get(name, ""),
            )
            for name, counts in ranked[:MAX_NEXT_STEPS]
            if len(counts) > 1
        ]


#: How many distinguishing characteristics to show against a candidate. Three fits a card and
#: is enough to tell near-identical positions apart; more turns the choice back into a wall of
#: text.
MAX_DISTINCTIONS = 3


def _with_distinctions(
    shown: list[PositionCandidate], by_code: dict[str, list[Characteristic]]
) -> list[PositionCandidate]:
    """Annotate each shown candidate with what makes it different from the others.

    A characteristic is *discriminating* when the candidates do not all permit the same values
    for it. Characteristics they agree on are dropped however important they look: telling the
    user that all three positions are «Пальто/Куртка» does not help them pick one. The ones
    that disagree are ranked by how many distinct answers they produce, so the sharpest
    distinction is shown first.
    """
    if len(shown) < 2:
        return shown

    # code -> characteristic name -> the set of values that position permits
    permitted: dict[str, dict[str, frozenset[str]]] = {
        candidate.code: {
            characteristic.name: frozenset(
                value.describe() for value in characteristic.values if value.describe()
            )
            for characteristic in by_code.get(candidate.code, [])
        }
        for candidate in shown
    }

    discriminating: list[tuple[int, str]] = []
    for name in {name for values in permitted.values() for name in values}:
        answers = {values.get(name, frozenset()) for values in permitted.values()}
        if len(answers) > 1:
            discriminating.append((len(answers), name))
    # Most distinct answers first; name as the tiebreak so the order is stable across requests.
    discriminating.sort(key=lambda pair: (-pair[0], pair[1]))

    return [
        replace(
            candidate,
            distinctions=[
                f"{name}: {_short(permitted[candidate.code].get(name, frozenset()))}"
                for _, name in discriminating[:MAX_DISTINCTIONS]
            ],
        )
        for candidate in shown
    ]


def _short(values: frozenset[str]) -> str:
    """A characteristic's permitted values in one line, or «не указана» when it has none.

    The absence of a characteristic is itself a distinction, and the most informative one: it
    is why one «Куртка зимняя из кожи» is not the other.
    """
    if not values:
        return "не указана"
    ordered = sorted(values)
    head = ", ".join(ordered[:3])
    return head if len(ordered) <= 3 else f"{head} и ещё {len(ordered) - 3}"


def _correction_issues(correction) -> list[Issue]:
    """What the spelling pipeline did, reported word by word.

    One issue per word rather than one per query: «В описании исправлены опечатки: "<вся
    строка>" → "<вся строка>"» leaves the user to diff two sentences themselves, and the
    interface cannot attach it to anything. Per word, each report names the word, says whether
    it was a mistake or a form of the reference's own wording, and carries the replacement as a
    suggestion the user can accept or reject on its own.

    The whole-query message survives as a fallback for a rewrite that changed the number of
    words, where no honest word-to-word pairing exists.
    """
    if not correction.changed:
        return []

    edits = correction.edits
    if not edits:
        return [
            Issue(
                kind=IssueKind.TYPO,
                characteristic="",
                raw=correction.original,
                message=(
                    f"Описание исправлено: «{correction.original}» → «{correction.corrected}»."
                ),
                suggestions=[correction.corrected],
            )
        ]

    return [
        Issue(
            kind=IssueKind.NORMALIZED if edit.is_inflection else IssueKind.TYPO,
            characteristic="",
            raw=edit.before,
            message=(
                f"«{edit.before}» приведено к форме справочника КТРУ — «{edit.after}»."
                if edit.is_inflection
                else (
                    f"Слова «{edit.before}» нет в справочнике КТРУ; ближайшее по написанию — "
                    f"«{edit.after}», оно и подставлено в описание."
                )
            ),
            suggestions=[edit.after],
        )
        for edit in edits
    ]


def _no_match(text: str, pool: list[PositionCandidate]) -> Issue:
    """The honest answer when КТРУ has nothing close enough.

    КТРУ genuinely does not cover everything a user will type: there is no position for
    «отвертка» anywhere in the reference outside the medical group. Trigram similarity has no
    way to return nothing, so it returns the least-bad row — and without this check that row
    was presented as the answer. Naming the nearest positions turns a dead end into a choice,
    and says plainly that none of them was matched *to* the description.
    """
    # Deduplicated by name, in rank order. КТРУ gives several positions the same name, so the
    # raw list offered «Кровать детская» three times — three chips that look like three
    # choices and are not. The codes behind them are still listed below as candidates.
    nearest: list[str] = []
    for candidate in pool:
        if candidate.name not in nearest:
            nearest.append(candidate.name)
        if len(nearest) == 5:
            break
    return Issue(
        kind=IssueKind.NO_MATCH,
        characteristic="",
        raw=text,
        message=(
            "В справочнике КТРУ не нашлось позиции, уверенно соответствующей описанию "
            f"«{text}». Уточните наименование товара или выберите из близких позиций."
        ),
        suggestions=nearest,
    )


def _judge(named: Characteristic, mention: NumberMention) -> Issue | None:
    """What КТРУ has to say against one number, once we know whose number it is.

    Two different complaints, kept apart because they lead to different fixes: a wrong unit is
    «эта характеристика измеряется в миллиметрах», an out-of-range value is «допустимо от 400
    до 2400». One boolean for both made it impossible to say which had happened.
    """
    if not unit_is_compatible(named, mention):
        return Issue(
            kind=IssueKind.WRONG_UNIT,
            characteristic=named.name,
            raw=mention.raw,
            message=(
                f"Характеристика «{named.name}» измеряется в "
                + _units_phrase(named)
                + f", а значение указано в «{mention.raw_unit}»."
            ),
            suggestions=[unit.name for unit in named.units][:6],
        )

    if not characteristic_accepts(named, mention):
        allowed = _allowed(named)
        return Issue(
            kind=IssueKind.OUT_OF_RANGE,
            characteristic=named.name,
            raw=mention.raw,
            message=(
                f"Значение «{mention.raw}» не входит ни в один допустимый диапазон "
                f"характеристики «{named.name}». КТРУ допускает: {', '.join(allowed)}."
            ),
            suggestions=allowed,
        )
    return None


def _named_characteristic(
    live: list[Characteristic], mention: NumberMention, product_stems: frozenset[str]
) -> Characteristic | None:
    """The characteristic the words around a number point at, if any.

    Only a *named* characteristic is judged. A number with no label beside it could belong to
    any of a dozen characteristics sharing its unit, and complaining that it fits none of them
    would be complaining about a guess.
    """
    best = 0
    chosen: Characteristic | None = None
    for characteristic in sorted(live, key=lambda c: c.name):
        affinity = label_affinity(characteristic.name, mention.context, product_stems)
        if affinity > best:
            best, chosen = affinity, characteristic
    if chosen is not None:
        return chosen

    # No label — but if only one characteristic is measured in this unit, there is nothing to
    # be ambiguous about and the number can still be judged. This is the common case for a
    # simple product: «молоток 9000 мм» against a position whose only millimetre characteristic
    # is «Длина рукоятки» is an out-of-range value, and staying silent about it would forfeit
    # the error detection the task is scored on. A wardrobe, with height, width and depth all
    # in millimetres, falls through to `None` instead of being judged against a guess.
    matching = {
        characteristic.name: characteristic
        for characteristic in live
        if mention.unit and unit_is_compatible(characteristic, mention)
        and any(unit.name == mention.unit for unit in characteristic.units)
    }
    return next(iter(matching.values())) if len(matching) == 1 else None


def _allowed(characteristic: Characteristic) -> list[str]:
    """The permitted values of one characteristic, as КТРУ states them.

    Quoted verbatim in the explanation: «не входит в диапазон» on its own is an accusation,
    «КТРУ допускает ≥ 400 … ≤ 2400 Миллиметр» is something the user can act on.
    """
    return sorted({value.describe() for value in characteristic.values if value.describe()})[:6]


def _units_phrase(characteristic: Characteristic) -> str:
    names = [unit.name for unit in characteristic.units]
    return ", ".join(f"«{name}»" for name in names[:3]) or "безразмерных величинах"


def _split(text: str, lexicon: Lexicon | None) -> SplitQuery:
    """Разделить описание, либо оставить его одной частью, когда словаря нет.

    Без словаря разделить нельзя: роль слова определяется тем, где оно встречается в
    справочнике, и выдумывать её из текста значило бы угадывать. Поиск тогда идёт по описанию
    целиком — прежнее поведение, хуже, но не неверное.
    """
    if lexicon is None:
        return SplitQuery(full=text, name=text, attributes="")
    return split(text, lexicon)


def _narrow_by_hand(
    pool: list[PositionCandidate],
    by_code: dict[str, list[Characteristic]],
    extracted: list[ExtractedCharacteristic],
) -> tuple[list[PositionCandidate], list[NarrowingStep], Counter[str]]:
    """Сужение перебором пула — путь без поискового движка.

    Each recognised characteristic is a filter, applied in turn so the elimination is visible
    rather than happening all at once.
    """
    steps: list[NarrowingStep] = []
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

    return pool, steps, supports


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


def _name_words(name: str) -> list[str]:
    """Words of a position name, for the "this is the product, not an attribute" set."""
    return _WORD_RE.findall(name)


def _plain(number) -> str:
    return format(number.normalize(), "f")


def _unchanged(text: str):
    from apps.spellcheck.domain.correction import Correction

    return Correction.unchanged(" ".join((text or "").split()))
