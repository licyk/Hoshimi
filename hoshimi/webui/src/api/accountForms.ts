import type { components } from '@/api/schema';

export type CredentialForm = components['schemas']['CredentialFormView'];

export class AccountFormError extends Error {
  constructor(public field: string, public reason: 'required' | 'json' | 'choice') {
    super(reason);
  }
}

/** Serialize only the selected method's fields; stale secrets never cross methods. */
export function accountFromForm(form: CredentialForm, values: Record<string, string | number | null | undefined>): Record<string, unknown> {
  const account: Record<string, unknown> = { ...form.defaults };
  for (const field of form.fields) {
    const value = String(values[field.key] ?? '').trim();
    if (!value) {
      if (field.required) throw new AccountFormError(field.key, 'required');
      continue;
    }
    if (field.kind === 'select' && !(field.choices ?? []).includes(value)) throw new AccountFormError(field.key, 'choice');
    if (field.kind === 'json' || (field.kind === 'list' && value.startsWith('['))) {
      try {
        const parsed: unknown = JSON.parse(value);
        if (field.kind === 'list' ? !Array.isArray(parsed) : !parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('shape');
        if (field.required && Array.isArray(parsed) && !parsed.length) throw new Error('empty');
        account[field.key] = parsed;
      } catch {
        throw new AccountFormError(field.key, 'json');
      }
    } else {
      account[field.key] = field.kind === 'list' ? value.split(/[\s,]+/).filter(Boolean) : value;
    }
  }
  return account;
}
