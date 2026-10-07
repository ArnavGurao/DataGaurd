import { useRef, useState } from 'react';
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Check,
  FileSpreadsheet,
  LoaderCircle,
  ShieldCheck,
  UploadCloud,
  X,
} from 'lucide-react';
import { Button, CheckItem, ErrorBox, PageHeading } from '../components/UI.jsx';
import { navigate, useWorkspace } from '../context.jsx';
import { DIRTY_CSV, GOOD_CSV, LIMITS, saveFile } from '../lib/validation.js';

export function Upload() {
  const { rules, service, refresh, handleError, mode } = useWorkspace();
  const [file, setFile] = useState(null),
    [title, setTitle] = useState(''),
    [ruleId, setRuleId] = useState(rules[0]?.id || '');
  const [dragging, setDragging] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(null);
  const input = useRef(null),
    submitting = useRef(false);
  const selected = rules.find((r) => r.id === ruleId);
  function choose(candidate) {
    setError(null);
    if (!candidate) return;
    if (!/\.csv$/i.test(candidate.name)) {
      setError('Choose a .csv file. Excel workbooks are not supported.');
      return;
    }
    if (candidate.size > LIMITS.bytes) {
      setError('This file is too large. Upload a CSV of 5 MiB or less.');
      return;
    }
    if (!candidate.size) {
      setError('This file is empty. Add a header and at least one data row.');
      return;
    }
    setFile(candidate);
    if (!title) setTitle(candidate.name.replace(/\.csv$/i, '').replace(/[_-]/g, ' '));
  }
  async function submit(e) {
    e.preventDefault();
    if (submitting.current) return;
    if (!file || !selected) {
      setError('Choose a CSV file and a rule set to continue.');
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError(null);
    const body = new FormData();
    body.append('title', title.trim());
    body.append('file', file);
    body.append('rule_set_id', ruleId);
    try {
      const job = await service.createJob(body);
      refresh();
      navigate(`/jobs/${job.job_id}`);
    } catch (e) {
      setError(e);
      handleError(e);
    } finally {
      setBusy(false);
      submitting.current = false;
    }
  }
  return (
    <>
      <button className="back-link" onClick={() => navigate('/datasets')}>
        <ArrowLeft size={15} />
        Back to datasets
      </button>
      <PageHeading
        eyebrow="A FRESH START"
        title="Let’s check your data"
        description="Bring your CSV. We’ll help you find what needs attention."
      />
      <div className="upload-layout">
        <form className="panel upload-form" onSubmit={submit}>
          <div className="form-section-title">
            <span>01</span>
            <div>
              <h2>Add your dataset</h2>
              <p>One file. A clearer picture of your data.</p>
            </div>
          </div>
          <input
            ref={input}
            className="sr-only"
            type="file"
            accept=".csv,text/csv"
            aria-label="Choose CSV file"
            disabled={busy}
            onChange={(e) => {
              choose(e.target.files?.[0]);
              e.target.value = '';
            }}
          />
          <div
            className={`dropzone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
            onDragOver={(e) => {
              e.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (busy) return;
              if (e.dataTransfer.files.length > 1) setError('Upload one CSV at a time.');
              else choose(e.dataTransfer.files[0]);
            }}
          >
            {file ? (
              <>
                <span className="upload-icon">
                  <FileSpreadsheet size={29} />
                </span>
                <strong>{file.name}</strong>
                <p>{(file.size / 1024).toFixed(1)} KB · Ready to validate</p>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => setFile(null)}
                >
                  <X size={15} />
                  Remove file
                </Button>
              </>
            ) : (
              <>
                <span className="upload-icon">
                  <UploadCloud size={30} />
                </span>
                <h3>Drop your CSV right here</h3>
                <p>or choose a file from your computer</p>
                <Button type="button" variant="secondary" onClick={() => input.current.click()}>
                  Browse files
                  <ArrowRight size={15} />
                </Button>
                <small>CSV only · Up to 5 MiB · UTF-8 encoding</small>
              </>
            )}
          </div>
          <label className="field">
            Dataset title
            <input
              required
              maxLength={120}
              placeholder="e.g. October student records"
              value={title}
              disabled={busy}
              onChange={(e) => setTitle(e.target.value)}
            />
            <small>A recognizable name makes it easier to find later.</small>
          </label>
          <div className="form-divider" />
          <div className="form-section-title">
            <span>02</span>
            <div>
              <h2>Choose your checks</h2>
              <p>Apply a reusable rule set to this dataset.</p>
            </div>
          </div>
          <label className="field">
            Validation rule set
            <select
              required
              value={ruleId}
              disabled={busy}
              onChange={(e) => setRuleId(e.target.value)}
            >
              <option value="" disabled>
                Select a rule set
              </option>
              {rules.map((rule) => (
                <option key={rule.id} value={rule.id}>
                  {rule.name}
                </option>
              ))}
            </select>
          </label>
          {!rules.length && (
            <p className="help-text">
              You need a rule set first.{' '}
              <button type="button" className="text-button" onClick={() => navigate('/rules')}>
                Create a rule set
                <ArrowRight size={14} />
              </button>
            </p>
          )}
          {selected && (
            <div className="rule-preview">
              <ShieldCheck size={19} />
              <div>
                <strong>{selected.rules.required_columns.length} required columns</strong>
                <p>{selected.rules.required_columns.join(', ')}</p>
                <small>Case-sensitive checks · Original values preserved</small>
              </div>
            </div>
          )}
          {error && <ErrorBox error={error} />}
          <div className="form-actions">
            <span>
              <ShieldCheck size={14} />
              Your source file stays untouched
            </span>
            <Button disabled={busy || !file || !selected || !title.trim()} type="submit">
              {busy ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{' '}
              {busy ? 'Starting validation…' : 'Validate dataset'}
            </Button>
          </div>
        </form>
        <aside className="upload-aside">
          <section className="panel side-note">
            <span className="note-icon">
              <ShieldCheck size={24} />
            </span>
            <h2>A few things to know</h2>
            <p>Small details make a big difference.</p>
            <CheckItem>Up to 20,000 data rows</CheckItem>
            <CheckItem>Up to 50 columns</CheckItem>
            <CheckItem>One header row, separated by commas</CheckItem>
            <CheckItem>UTF-8, with or without a BOM</CheckItem>
            <div className="form-divider" />
            <p>
              Invalid rows are separated with clear explanations. Your original values are never
              silently changed.
            </p>
          </section>
          <section className="sample-card">
            <span className="eyebrow">JUST EXPLORING?</span>
            <h3>Meet your first dataset.</h3>
            <p>Try the sample from the project guide: 6 rows, with a few intentional mistakes.</p>
            <button
              className="text-button"
              onClick={() => {
                choose(new File([DIRTY_CSV], 'students_dirty.csv', { type: 'text/csv' }));
                setTitle('Student records audit');
                const preset = rules.find((r) => r.rules.required_columns.includes('student_id'));
                if (preset) setRuleId(preset.id);
              }}
            >
              Use sample dataset
              <ArrowRight size={15} />
            </button>
            <button
              className="sample-download"
              onClick={() => saveFile(GOOD_CSV, 'students_corrected.csv')}
            >
              <ArrowDownToLine size={14} />
              Download corrected sample
            </button>
          </section>
          {mode === 'demo' && (
            <p className="demo-note">
              Demo uploads are processed in your browser and reset when you reload the page. Use
              synthetic data.
            </p>
          )}
        </aside>
      </div>
    </>
  );
}
