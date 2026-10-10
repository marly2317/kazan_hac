import type { Analysis, Suggestion } from '../domain/analysis';

export type AnalyzeParams = {
  description: string;
  /**
   * `false` отправляется, когда пользователь отклонил исправление: он его увидел и отказался,
   * и повторное исправление отменило бы его выбор.
   */
  correctTypos?: boolean;
  /**
   * Описание дописано, а не набирается прямо сейчас.
   *
   * Разбор идёт по мере ввода, поэтому «куртк» и «куртка зим» — тоже запросы. Пользователю
   * разницы нет; бэкенд по этому признаку решает, стоит ли запоминать наблюдение о нехватке
   * в справочнике, и без него в отчёт попали бы все обрывки слов, набранные по дороге.
   */
  settled?: boolean;
};

/** Порт, от которого зависит хранилище. HTTP-реализация живёт в `infrastructure`. */
export interface AutofillGateway {
  analyze(params: AnalyzeParams, signal?: AbortSignal): Promise<Analysis>;
}

/** Порт подсказок: отдельно от разбора — его зовут на каждый символ. */
export interface SuggestGateway {
  suggest(text: string, signal?: AbortSignal): Promise<Suggestion[]>;
}
