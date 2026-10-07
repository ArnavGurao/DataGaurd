import { useState } from 'react';
import {
  ArrowRight,
  Check,
  CircleCheck,
  Copy,
  Fingerprint,
  Hash,
  ListFilter,
  LoaderCircle,
  Plus,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import { Button, Empty, ErrorBox, Modal, PageHeading, SearchInput } from '../components/UI.jsx';
import { useWorkspace } from '../context.jsx';
import { STUDENT_RULES, validateRules } from '../lib/validation.js';

const split = (text) =>
  text
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
function draftFrom(rule) {
  const r = rule?.rules || STUDENT_RULES;
  return {
    name: rule ? `${rule.name} copy` : '',
    required_columns: r.required_columns.join(', '),
    required_values: r.required_values.join(', '),
    unique_columns: r.unique_columns.join(', '),
    numeric: Object.entries(r.numeric_ranges).map(([column, range]) => ({ column, ...range })),
    allowed: Object.entries(r.allowed_values).map(([column, values]) => ({
      column,
      values: values.join(', '),
    })),
  };
}
export function Rules() {
  const { rules } = useWorkspace();
  const [search, setSearch] = useState(''),
    [editor, setEditor] = useState(null),
    [view, setView] = useState(null);
  const filtered = rules.filter((r) => r.name.toLowerCase().includes(search.toLowerCase()));
  return (
    <>
      <PageHeading
        eyebrow="YOUR QUALITY STANDARD"
        title="Rules worth keeping"
        description="Define what good data looks like. Reuse it with every upload."
        action={
          <Button onClick={() => setEditor({})}>
            <Plus size={17} />
            Create rule set
          </Button>
        }
      />
      <div className="rules-toolbar">
        <SearchInput value={search} onChange={setSearch} placeholder="Search rule sets…" />
        <span className="muted">{rules.length} reusable rule sets</span>
      </div>
      <div className="rules-grid">
        {filtered.map((rule, i) => (
          <article className="panel rule-card" key={rule.id}>
            <div className="rule-card-top">
              <span className={`rule-card-icon tone-${i % 3}`}>
                <ShieldCheck size={25} />
              </span>
              <span className="subtle-badge">{rule.rules.required_columns.length} columns</span>
            </div>
            <h2>{rule.name}</h2>
            <p>{rule.description || 'Your standards, applied consistently to every dataset.'}</p>
            <div className="rule-tags">
              {rule.rules.required_values.length > 0 && (
                <span>
                  <CircleCheck size={12} />
                  Required values
                </span>
              )}
              {rule.rules.unique_columns.length > 0 && (
                <span>
                  <Fingerprint size={12} />
                  Unique values
                </span>
              )}
              {Object.keys(rule.rules.numeric_ranges).length > 0 && (
                <span>
                  <Hash size={12} />
                  Numeric range
                </span>
              )}
              {Object.keys(rule.rules.allowed_values).length > 0 && (
                <span>
                  <ListFilter size={12} />
                  Allowed values
                </span>
              )}
            </div>
            <div className="rule-card-footer">
              <button className="text-button" onClick={() => setView(rule)}>
                View rules
                <ArrowRight size={14} />
              </button>
              <button
                className="icon-button"
                aria-label={`Duplicate ${rule.name}`}
                title="Create a copy"
                onClick={() => setEditor(rule)}
              >
                <Copy size={16} />
              </button>
            </div>
          </article>
        ))}
      </div>
      {!filtered.length && (
        <Empty
          title="No matching rule sets"
          description="Try a different search or create your first set of checks."
        />
      )}
      <div className="info-strip">
        <ShieldCheck size={19} />
        <p>
          Every validation saves a snapshot of its rules. Your existing reports always reflect the
          checks used at upload.
        </p>
      </div>
      {editor && <RuleEditor source={editor.id ? editor : null} onClose={() => setEditor(null)} />}
      {view && (
        <Modal title={view.name} onClose={() => setView(null)}>
          <div className="modal-body">
            <RuleSummary rules={view.rules} />
            <div className="info-strip">
              <p>
                Values are trimmed for checking and are case-sensitive. Exports retain original
                strings. Every occurrence of a duplicate is rejected.
              </p>
            </div>
            <Button
              onClick={() => {
                setEditor(view);
                setView(null);
              }}
            >
              <Copy size={15} />
              Create a copy
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
export function RuleSummary({ rules }) {
  return (
    <dl className="rule-summary">
      <dt>Required columns</dt>
      <dd>{rules.required_columns.join(', ')}</dd>
      <dt>Required values</dt>
      <dd>{rules.required_values.join(', ') || 'None'}</dd>
      <dt>Unique values</dt>
      <dd>{rules.unique_columns.join(', ') || 'None'}</dd>
      <dt>Numeric ranges</dt>
      <dd>
        {Object.entries(rules.numeric_ranges).map(([col, r]) => (
          <div key={col}>
            <code>{col}</code>: {r.min}–{r.max}
            {r.integer ? ' · Whole numbers only' : ''}
          </div>
        ))}
        {!Object.keys(rules.numeric_ranges).length && 'None'}
      </dd>
      <dt>Allowed values</dt>
      <dd>
        {Object.entries(rules.allowed_values).map(([col, values]) => (
          <div key={col}>
            <code>{col}</code>: {values.join(', ')}
          </div>
        ))}
        {!Object.keys(rules.allowed_values).length && 'None'}
      </dd>
    </dl>
  );
}
function RuleEditor({ source, onClose }) {
  const { service, refresh, notify, handleError } = useWorkspace();
  const [draft, setDraft] = useState(() => draftFrom(source)),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(null);
  const set = (key, value) => setDraft((d) => ({ ...d, [key]: value }));
  const updateRow = (key, index, field, value) =>
    setDraft((d) => ({
      ...d,
      [key]: d[key].map((r, i) => (i === index ? { ...r, [field]: value } : r)),
    }));
  async function submit(e) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    try {
      if (!draft.name.trim()) throw new Error('Give your rule set a name.');
      for (const key of ['numeric', 'allowed']) {
        const columns = draft[key].map((r) => r.column.trim());
        if (columns.some((c) => !c) || new Set(columns).size !== columns.length)
          throw new Error('Each rule needs a unique, nonblank column name within its check type.');
      }
      if (draft.numeric.some((r) => r.min === '' || r.max === ''))
        throw new Error('Set both numeric bounds.');
      const rules = validateRules({
        required_columns: split(draft.required_columns),
        required_values: split(draft.required_values),
        unique_columns: split(draft.unique_columns),
        numeric_ranges: Object.fromEntries(
          draft.numeric.map((r) => [
            r.column.trim(),
            { min: Number(r.min), max: Number(r.max), integer: r.integer },
          ]),
        ),
        allowed_values: Object.fromEntries(
          draft.allowed.map((r) => [r.column.trim(), split(r.values)]),
        ),
      });
      setBusy(true);
      await service.createRule({ name: draft.name.trim(), rules });
      refresh();
      notify('Your rule set is ready to use.');
      onClose();
    } catch (e) {
      setError(e);
      handleError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={source ? 'Make it your own' : 'Create a rule set'}
      onClose={() => {
        if (!busy) onClose();
      }}
      wide
    >
      <form className="modal-body rule-form" onSubmit={submit}>
        <p className="muted">Start with the student preset, then tailor each check to your CSV.</p>
        <label className="field">
          Rule-set name
          <input
            autoFocus
            required
            maxLength={100}
            placeholder="e.g. Student records"
            value={draft.name}
            onChange={(e) => set('name', e.target.value)}
          />
        </label>
        <label className="field">
          Required columns
          <input
            required
            value={draft.required_columns}
            onChange={(e) => set('required_columns', e.target.value)}
          />
          <small>Separate names with commas. Include every column referenced below.</small>
        </label>
        <div className="form-two-columns">
          <label className="field">
            Must have a value
            <input
              value={draft.required_values}
              onChange={(e) => set('required_values', e.target.value)}
            />
          </label>
          <label className="field">
            Must be unique
            <input
              value={draft.unique_columns}
              onChange={(e) => set('unique_columns', e.target.value)}
            />
          </label>
        </div>
        <div className="editor-label">
          <h3>Numeric ranges</h3>
          <button
            type="button"
            className="text-button"
            onClick={() =>
              set('numeric', [...draft.numeric, { column: '', min: 0, max: 100, integer: false }])
            }
          >
            <Plus size={14} />
            Add range
          </button>
        </div>
        {draft.numeric.map((r, i) => (
          <div className="numeric-row" key={i}>
            <label className="field">
              Column
              <input
                required
                value={r.column}
                onChange={(e) => updateRow('numeric', i, 'column', e.target.value)}
              />
            </label>
            <label className="field">
              Min
              <input
                required
                type="number"
                step="any"
                value={r.min}
                onChange={(e) => updateRow('numeric', i, 'min', e.target.value)}
              />
            </label>
            <label className="field">
              Max
              <input
                required
                type="number"
                step="any"
                value={r.max}
                onChange={(e) => updateRow('numeric', i, 'max', e.target.value)}
              />
            </label>
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={r.integer}
                onChange={(e) => updateRow('numeric', i, 'integer', e.target.checked)}
              />
              Integer
            </label>
            <button
              className="icon-button"
              type="button"
              aria-label={`Remove numeric range ${i + 1}`}
              onClick={() =>
                set(
                  'numeric',
                  draft.numeric.filter((_, n) => n !== i),
                )
              }
            >
              <X size={16} />
            </button>
          </div>
        ))}
        <div className="editor-label">
          <h3>Allowed values</h3>
          <button
            className="text-button"
            type="button"
            onClick={() => set('allowed', [...draft.allowed, { column: '', values: '' }])}
          >
            <Plus size={14} />
            Add check
          </button>
        </div>
        {draft.allowed.map((r, i) => (
          <div className="allowed-row" key={i}>
            <label className="field">
              Column
              <input
                required
                value={r.column}
                onChange={(e) => updateRow('allowed', i, 'column', e.target.value)}
              />
            </label>
            <label className="field">
              Permitted values
              <input
                required
                value={r.values}
                onChange={(e) => updateRow('allowed', i, 'values', e.target.value)}
              />
            </label>
            <button
              className="icon-button"
              type="button"
              aria-label={`Remove allowed values ${i + 1}`}
              onClick={() =>
                set(
                  'allowed',
                  draft.allowed.filter((_, n) => n !== i),
                )
              }
            >
              <X size={16} />
            </button>
          </div>
        ))}
        <p className="help-text">
          Comma-separated values are case-sensitive. Every occurrence of a duplicate will be
          rejected.
        </p>
        {error && <ErrorBox error={error} />}
        <div className="modal-actions">
          <Button type="button" variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}Save rule set
          </Button>
        </div>
      </form>
    </Modal>
  );
}
