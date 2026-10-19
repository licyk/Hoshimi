import { describe, expect, it } from 'vitest';
import { accountFromForm, AccountFormError, type CredentialForm } from '@/api/accountForms';

describe('credential forms', () => {
  const keyForm: CredentialForm = { method: 'api_key', defaults: {}, fields: [{ key: 'api_key', kind: 'secret', choices: [], required: true }, { key: 'models', kind: 'list', choices: [], required: false }] };
  it('only sends selected fields and preserves vendor-qualified model IDs', () => {
    expect(accountFromForm(keyForm, { api_key: ' key ', models: 'vendor/Model, second', aws_secret_access_key: 'stale secret' })).toEqual({ api_key: 'key', models: ['vendor/Model', 'second'] });
  });
  it('keeps ADC independent of a key entered in an earlier method', () => {
    expect(accountFromForm({ method: 'adc', defaults: { auth: 'adc' }, fields: [{ key: 'project', kind: 'text', choices: [], required: true }] }, { project: 'project', api_key: 'stale' })).toEqual({ auth: 'adc', project: 'project' });
  });
  it('adds a bundled EAC reference without retaining custom relay secrets', () => {
    const form: CredentialForm = { method: 'bundled', defaults: { relay: 'bundled' }, fields: [] };
    expect(accountFromForm(form, { signing_secret: 'stale secret', access_token: 'stale token', base_url: 'https://custom.example/v1' })).toEqual({ relay: 'bundled' });
  });
  it('refuses missing required fields and invalid deployment maps', () => {
    expect(() => accountFromForm(keyForm, {})).toThrow(AccountFormError);
    const form: CredentialForm = { method: 'api_key', defaults: {}, fields: [{ key: 'deployments', kind: 'json', choices: [], required: true }] };
    expect(() => accountFromForm(form, { deployments: '[]' })).toThrow(AccountFormError);
    expect(accountFromForm(form, { deployments: '{"model":"deployment"}' })).toEqual({ deployments: { model: 'deployment' } });
  });
  it('allows local servers without a key and validates explicit protocol choices', () => {
    const local: CredentialForm = { method: 'local', defaults: { base_url: 'http://localhost:11434/v1' }, fields: [{ key: 'api_key', kind: 'secret', choices: [], required: false }] };
    expect(accountFromForm(local, {})).toEqual({ base_url: 'http://localhost:11434/v1' });
    const protocol: CredentialForm = { method: 'api_key', defaults: {}, fields: [{ key: 'protocol', kind: 'select', required: true, choices: ['chat', 'gemini'] }] };
    expect(accountFromForm(protocol, { protocol: 'chat' })).toEqual({ protocol: 'chat' });
    expect(() => accountFromForm(protocol, { protocol: 'invalid' })).toThrow(AccountFormError);
  });
});
