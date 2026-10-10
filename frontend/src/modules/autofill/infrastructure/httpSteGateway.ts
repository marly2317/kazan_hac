import { API_ROOT, request } from '@/shared/api/client';

import type { SteGateway } from '../application/ports';
import type { FamilyHit, Result } from '../domain/result';

const ROOT = `${API_ROOT}/ste`;

function post(path: string, body: Record<string, string>): Promise<Result> {
  return request<Result>(`${ROOT}/${path}/`, { method: 'POST', body });
}

export class HttpSteGateway implements SteGateway {
  async preview(text: string, signal?: AbortSignal): Promise<FamilyHit[]> {
    const query = new URLSearchParams({ q: text });
    const payload = await request<{ hits: FamilyHit[] }>(`${ROOT}/preview/?${query}`, { signal });
    return payload.hits;
  }

  analyze(text: string): Promise<Result> {
    return post('analyze', { text });
  }

  answer(sessionId: string, charKey: string, value: string): Promise<Result> {
    return post('answer', { session_id: sessionId, char_key: charKey, value });
  }

  answerText(sessionId: string, charKey: string, text: string): Promise<Result> {
    return post('answer-text', { session_id: sessionId, char_key: charKey, text });
  }

  chooseFamily(sessionId: string, familyId: string): Promise<Result> {
    return post('choose-family', { session_id: sessionId, family_id: familyId });
  }

  chooseLeaf(sessionId: string, code: string): Promise<Result> {
    return post('choose-leaf', { session_id: sessionId, code });
  }

  edit(sessionId: string, charKey: string, value: string): Promise<Result> {
    return post('edit', { session_id: sessionId, char_key: charKey, value });
  }
}
