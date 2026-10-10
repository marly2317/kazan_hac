"""Скачать энкодер в образ на этапе сборки.

Отдельный файл, а не строчка в `Dockerfile`: загрузка модели — единственный шаг сборки,
которому нужна сеть, и когда он падает, хочется видеть его собственную трассировку, а не
«RUN python -c … exited with code 1».

Модель вшивается в образ намеренно. На стенде хакатона сети может не быть, а модель,
скачиваемая при первом запросе, — это отказ в самый неподходящий момент: поиск работает на
машине разработчика и не работает на защите.

    python docker/fetch_encoder.py cointegrated/rubert-tiny2 /opt/models/encoder
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    name, destination = argv[1], argv[2]

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(name)
    model.save(destination)
    print(f"{name} → {destination}, {model.get_sentence_embedding_dimension()} измерений")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
