"""Векторы карточек семейств для поиска (архитектура §3.9, офлайн).

    cd backend
    python -m apps.ste.kb_build.embed                       # конфиг из окружения
    STE_EMBEDDER_PATH=/models/qwen3-embedding-0.6b python -m apps.ste.kb_build.embed
    python manage.py embed_ste_kb                           # то же через Django

Документ — полная карточка (:func:`apps.ste.domain.cards.full_card`) каждого конкретного
семейства и каждой общей позиции. Результат — два файла рядом с базой знаний:

* ``family_vectors.npy`` — матрица ``n × d`` float32, строки нормированы (косинус = скалярное
  произведение);
* ``family_vectors.json`` — ``{ids, model, dim, card_sha256, built_at, seconds}``.

``card_sha256`` — отпечаток карточек, из которых посчитаны векторы. Карточки меняются вместе с
LLM-метаданными (синонимы, описание), и поиск по отпечатку видит, что векторы устарели.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from apps.ste.domain.cards import full_card
from apps.ste.domain.kb import Family, KnowledgeBase

logger = logging.getLogger(__name__)

VECTORS_FILE = "family_vectors.npy"
VECTORS_META = "family_vectors.json"


@dataclass
class FamilyVectors:
    """Векторы карточек: строка ``i`` матрицы — семейство ``ids[i]``."""

    ids: list[str]
    matrix: np.ndarray
    model: str = ""
    card_sha256: str = ""

    @property
    def dim(self) -> int:
        return int(self.matrix.shape[1]) if self.matrix.ndim == 2 else 0


def embeddable(kb: KnowledgeBase) -> list[Family]:
    """Что индексируем: конкретные семейства и общие позиции (услуг в базе нет вовсе)."""
    return [family for family in kb.families.values() if family.role in {"family", "generic"}]


def cards_sha256(families: list[Family]) -> str:
    """Отпечаток карточек в порядке семейств — меняется с любым синонимом или описанием."""
    digest = hashlib.sha256()
    for family in families:
        digest.update(family.id.encode())
        digest.update(b"\t")
        digest.update(full_card(family).encode())
        digest.update(b"\x1e")
    return digest.hexdigest()


def embed_families(kb: KnowledgeBase, embedder) -> FamilyVectors:
    """Посчитать векторы полных карточек. ``embedder`` — с методом ``encode_docs``."""
    families = embeddable(kb)
    matrix = embedder.encode_docs([full_card(family) for family in families])
    if matrix is None:
        raise RuntimeError("Эмбеддер недоступен: проверьте STE_EMBEDDER_PATH")
    return FamilyVectors(
        ids=[family.id for family in families],
        matrix=np.ascontiguousarray(matrix, dtype=np.float32),
        model=getattr(embedder, "model_name", ""),
        card_sha256=cards_sha256(families),
    )


def save_vectors(vectors: FamilyVectors, directory: Path, seconds: float | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / VECTORS_FILE
    np.save(path, vectors.matrix.astype(np.float32), allow_pickle=False)
    meta = {
        "ids": vectors.ids,
        "model": vectors.model,
        "dim": vectors.dim,
        "card_sha256": vectors.card_sha256,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if seconds is not None:
        meta["seconds"] = round(seconds, 1)
    (directory / VECTORS_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return path


def load_vectors(directory: Path) -> FamilyVectors | None:
    """Прочитать векторы; ``None`` — файлов нет или они не согласованы между собой.

    Битые векторы хуже отсутствующих: поиск молча ранжировал бы чужие семейства. Поэтому
    любое несоответствие (число строк ≠ числу id, размерность ≠ ``dim``) — отказ с
    предупреждением, и поиск работает только лексикой.
    """
    matrix_path, meta_path = directory / VECTORS_FILE, directory / VECTORS_META
    if not (matrix_path.exists() and meta_path.exists()):
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        matrix = np.load(matrix_path, allow_pickle=False)
    except (OSError, ValueError) as error:
        logger.warning("Векторы семейств не читаются (%s) — поиск только лексический", error)
        return None
    ids = [str(item) for item in meta.get("ids", [])]
    if (
        matrix.ndim != 2
        or matrix.shape[0] != len(ids)
        or (meta.get("dim") and int(meta["dim"]) != matrix.shape[1])
    ):
        logger.warning(
            "Векторы семейств не согласованы: матрица %s, id %d — поиск только лексический",
            matrix.shape,
            len(ids),
        )
        return None
    return FamilyVectors(
        ids=ids,
        matrix=np.ascontiguousarray(matrix, dtype=np.float32),
        model=str(meta.get("model", "")),
        card_sha256=str(meta.get("card_sha256", "")),
    )


def run(kb_dir: Path, out_dir: Path | None = None, embedder=None) -> tuple[FamilyVectors, float]:
    """Собрать и сохранить векторы; возвращает их и время счёта в секундах."""
    from apps.ste.kb_build.build import load

    if embedder is None:
        from apps.ste.infrastructure.embedder import get_embedder

        embedder = get_embedder()
    kb = load(kb_dir)
    started = time.perf_counter()
    vectors = embed_families(kb, embedder)
    seconds = time.perf_counter() - started
    save_vectors(vectors, out_dir or kb_dir, seconds)
    return vectors, seconds


def main(argv: list[str] | None = None) -> int:
    from apps.ste import config

    parser = argparse.ArgumentParser(description="Векторы карточек семейств КТРУ")
    parser.add_argument("--kb", default=str(config.KB_DIR), help="каталог базы знаний")
    parser.add_argument("--out", default=None, help="куда писать (по умолчанию — в --kb)")
    parser.add_argument("--model", default=None, help="путь к модели (иначе STE_EMBEDDER_PATH)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    embedder = None
    if args.model:
        from apps.ste.infrastructure.embedder import Embedder

        embedder = Embedder(args.model, True)
    kb_dir = Path(args.kb)
    try:
        vectors, seconds = run(kb_dir, Path(args.out) if args.out else None, embedder)
    except (FileNotFoundError, RuntimeError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    print(
        f"{len(vectors.ids)} карточек → {vectors.matrix.shape[0]}×{vectors.dim} "
        f"({vectors.model}) · {seconds:.0f} с · {Path(args.out or kb_dir) / VECTORS_FILE}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
