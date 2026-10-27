import { useEffect, useState } from 'react';
import { Button, ErrorBox } from './UI.jsx';
import { useWorkspace } from '../context.jsx';

export function IngestionFolders() {
  const { rules, service, handleError, refresh } = useWorkspace();
  const [sources, setSources] = useState([]), [name, setName] = useState(''), [ruleId, setRuleId] = useState(rules[0]?.id || '');
  const [busy, setBusy] = useState(false), [error, setError] = useState(null);
  useEffect(() => {
    let active = true;
    service.listSources().then((data) => { if (active) setSources(data); }).catch((e) => { if (active) setError(e); });
    return () => { active = false; };
  }, [service]);
  async function create(e) {
    e.preventDefault(); if (busy) return;
    setBusy(true); setError(null);
    try {
      const source = await service.createSource({ name: name.trim(), rule_set_id: ruleId });
      setSources((items) => [source, ...items]); setName('');
    } catch (e) { setError(e); handleError(e); }
    finally { setBusy(false); }
  }
  async function disable(id) {
    setBusy(true); setError(null);
    try { await service.disableSource(id); setSources((items) => items.map((s) => s.id === id ? { ...s, enabled: false } : s)); }
    catch (e) { setError(e); handleError(e); }
    finally { setBusy(false); }
  }
  return <section className="panel ingestion-panel">
    <h2>Automatic S3 uploads</h2>
    <p>Create a folder linked to a rule set. CSV files placed there by an authorized AWS uploader are automatically validated and appear in your dataset history.</p>
    <form className="rule-form" onSubmit={create}>
      <div className="form-two-columns">
        <label className="field">Folder name<input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} placeholder="Student imports" /></label>
        <label className="field">Folder rule set<select aria-label="Folder rule set" required value={ruleId} onChange={(e) => setRuleId(e.target.value)}>
          <option value="" disabled>Select a rule set</option>{rules.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select></label>
      </div>
      <Button type="submit" disabled={busy || !rules.length}>Create ingestion folder</Button>
    </form>
    {error && <ErrorBox error={error} />}
    {sources.map((s) => <article className="ingestion-source" key={s.id}>
      <strong>{s.name} {s.enabled ? '' : '(disabled)'}</strong>
      <p>Rule set: {rules.find((r) => r.id === s.rule_set_id)?.name || 'Unavailable'}</p>
      <code>{s.uri}</code>
      {s.enabled && <Button type="button" variant="secondary" disabled={busy} onClick={() => disable(s.id)}>Disable folder</Button>}
    </article>)}
    <p className="help-text">Upload CSV files directly into this folder using the AWS S3 console or CLI. The bucket stays private. Each uploaded version is processed once; disabling a folder stops future imports.</p>
    <Button type="button" variant="secondary" onClick={refresh}>Refresh dataset history</Button>
  </section>;
}
