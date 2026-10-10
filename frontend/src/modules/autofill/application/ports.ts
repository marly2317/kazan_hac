import type { FamilyHit, Result } from '../domain/result';

/** Порт подбора КТРУ; HTTP-реализация — в `infrastructure`. */
export interface SteGateway {
  preview(text: string, signal?: AbortSignal): Promise<FamilyHit[]>;
  analyze(text: string): Promise<Result>;
  answer(sessionId: string, charKey: string, value: string): Promise<Result>;
  answerText(sessionId: string, charKey: string, text: string): Promise<Result>;
  chooseFamily(sessionId: string, familyId: string): Promise<Result>;
  chooseLeaf(sessionId: string, code: string): Promise<Result>;
  edit(sessionId: string, charKey: string, value: string): Promise<Result>;
}
