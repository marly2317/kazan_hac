import { describe, expect, it } from 'vitest';

import {
  byUrgency,
  issueTitle,
  positionsLeft,
  severityOf,
  stateLabel,
} from '@/modules/autofill/domain/analysis';
import type { Issue } from '@/modules/autofill/domain/analysis';

function issue(kind: Issue['kind']): Issue {
  return { kind, characteristic: '', raw: '', message: '', suggestions: [] };
}

describe('severityOf', () => {
  it('не считает приведение к справочнику ошибкой', () => {
    // «колесиках» → «колесиков» — это форма слова, а не опечатка. Красная карточка здесь
    // обвиняет в ошибке правильный ввод.
    expect(severityOf('normalized')).toBe('info');
    expect(severityOf('typo')).toBe('warning');
  });

  it('помечает недопустимое значение как ошибку', () => {
    expect(severityOf('out_of_range')).toBe('error');
    expect(severityOf('wrong_unit')).toBe('error');
    expect(severityOf('no_match')).toBe('error');
  });
});

describe('byUrgency', () => {
  it('поднимает ошибки над информационными сообщениями', () => {
    // Пользователь читает сверху вниз и останавливается, когда понял, что делать.
    const sorted = [issue('normalized'), issue('out_of_range'), issue('typo')].sort(byUrgency);

    expect(sorted.map((item) => item.kind)).toEqual(['out_of_range', 'typo', 'normalized']);
  });
});

describe('positionsLeft', () => {
  it('согласует числительное с существительным', () => {
    // Проверка `count === 1` читается носителем как поломка на каждом числе на 2, 3, 4.
    expect(positionsLeft(1)).toBe('1 позиция');
    expect(positionsLeft(2)).toBe('2 позиции');
    expect(positionsLeft(5)).toBe('5 позиций');
    expect(positionsLeft(21)).toBe('21 позиция');
    expect(positionsLeft(11)).toBe('11 позиций');
    expect(positionsLeft(114)).toBe('114 позиций');
  });
});

describe('подписи', () => {
  it('у каждого вида проблемы есть заголовок', () => {
    expect(issueTitle('out_of_range')).toBe('Значение вне диапазона');
    expect(issueTitle('normalized')).toBe('Приведено к справочнику');
  });

  it('у каждого состояния есть подпись', () => {
    expect(stateLabel('resolved')).toBe('Код КТРУ определён');
    expect(stateLabel('not_found')).toBe('Подходящей позиции не нашлось');
  });
});
