"""Настройки gunicorn: прогрев модуля подбора КТРУ в каждом воркере.

База знаний, индекс поиска и эмбеддер грузятся лениво; без прогрева за это заплатил бы первый
запрос пользователя (~10 с). Прогрев идёт в фоне после старта воркера, поэтому healthcheck
отвечает сразу.
"""

import threading


def post_worker_init(worker):  # noqa: ARG001 — сигнатура задана gunicorn
    def warm() -> None:
        try:
            from apps.ste import runtime

            runtime.knowledge_base()
            runtime.corrector()
            retriever = runtime.retriever()
            retriever.search("прогрев", k=1)
        except Exception as error:  # noqa: BLE001 — прогрев не вправе уронить воркер
            worker.log.warning("Прогрев модуля КТРУ не удался: %s", error)

    threading.Thread(target=warm, name="ste-warmup", daemon=True).start()
