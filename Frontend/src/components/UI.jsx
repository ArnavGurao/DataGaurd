import {
  ArrowDownToLine,
  ArrowRight,
  Check,
  CircleAlert,
  FileSpreadsheet,
  FolderOpen,
  LoaderCircle,
  Search,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { date, navigate, number, useWorkspace } from '../context.jsx';

export function Button({ children, variant = 'primary', className = '', ...props }) {
  return (
    <button className={`button ${variant} ${className}`} {...props}>
      {children}
    </button>
  );
}
export function Status({ status }) {
  const label =
    {
      COMPLETED: 'Completed',
      FAILED: 'Failed',
      RUNNING: 'Running',
      QUEUED: 'Queued',
      PENDING_DISPATCH: 'Waiting',
    }[status] || status;
  return (
    <span className={`status ${status.toLowerCase()}`}>
      <span />
      {label}
    </span>
  );
}
export function ErrorBox({ error, onRetry }) {
  return (
    <div role="alert" className="error-box">
      <CircleAlert size={18} />
      <div>
        {error?.message || error}
        {onRetry && (
          <button className="text-button" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}
export function Empty({ title = 'Nothing here yet', description, action }) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <FolderOpen size={28} />
      </span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" size={24} />
      <span>Loading your workspace…</span>
    </div>
  );
}
export function SearchInput({ value, onChange, placeholder = 'Search datasets…', ...props }) {
  return (
    <div className="search-input">
      <Search size={17} />
      <input
        aria-label={placeholder}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        {...props}
      />
      {value && (
        <button aria-label="Clear search" onClick={() => onChange('')}>
          <X size={15} />
        </button>
      )}
    </div>
  );
}
export function PageHeading({ eyebrow, title, description, action }) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </div>
  );
}
export function DownloadButton({ job, kind = 'report', label, compact = false }) {
  const { service, notify, handleError } = useWorkspace();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      variant={compact ? 'icon-button' : 'secondary'}
      disabled={busy}
      aria-label={label || `Download ${kind} for ${job.title}`}
      title={label || `Download ${kind}`}
      onClick={async (e) => {
        e.stopPropagation();
        if (busy) return;
        setBusy(true);
        try {
          await service.download(job.job_id, kind);
          notify('Your download is ready.');
        } catch (e) {
          handleError(e);
          notify(e.message, 'error');
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? <LoaderCircle size={16} className="spin" /> : <ArrowDownToLine size={16} />}
      {!compact && (label || `Download ${kind}`)}
    </Button>
  );
}
export function JobsTable({ jobs, reportMode = false }) {
  return (
    <div className="table-scroll">
      <table className="jobs-table">
        <thead>
          <tr>
            <th>Dataset name</th>
            <th>Status</th>
            <th>Total rows</th>
            <th>Quality score</th>
            <th>Uploaded</th>
            <th>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.job_id}>
              <td>
                <button className="dataset-link" onClick={() => navigate(`/jobs/${job.job_id}`)}>
                  <span className="file-icon">
                    <FileSpreadsheet size={19} />
                  </span>
                  <span>
                    <strong>{job.title}</strong>
                    <small>{job.original_name || job.rule_set_name || 'CSV dataset'}</small>
                  </span>
                </button>
              </td>
              <td>
                <Status status={job.status} />
              </td>
              <td className="tabular">{job.summary ? number(job.summary.total_rows) : '—'}</td>
              <td>
                {job.summary ? (
                  <div className="quality-cell">
                    <span
                      className={`mini-progress ${job.summary.valid_percentage < 95 ? 'amber' : ''}`}
                    >
                      <i style={{ width: `${job.summary.valid_percentage}%` }} />
                    </span>
                    <span className="tabular">
                      {job.summary.valid_percentage.toFixed(1)}
                      <small>%</small>
                    </span>
                  </div>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td className="muted nowrap">{date(job.created_at)}</td>
              <td>
                {reportMode && job.available_downloads?.includes('report') ? (
                  <DownloadButton job={job} compact />
                ) : (
                  <button
                    className="row-arrow"
                    aria-label={`View ${job.title}`}
                    onClick={() => navigate(`/jobs/${job.job_id}`)}
                  >
                    <ArrowRight size={17} />
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function Modal({ title, children, onClose, wide = false }) {
  const ref = useRef(null);
  useEffect(() => {
    const previous = document.activeElement,
      dialog = ref.current;
    dialog.showModal();
    return () => {
      dialog.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`modal ${wide ? 'wide' : ''}`}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      aria-labelledby="dialog-title"
    >
      <div className="modal-header">
        <h2 id="dialog-title">{title}</h2>
        <button className="icon-button" onClick={onClose} aria-label="Close dialog">
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function CheckItem({ children }) {
  return (
    <div className="check-item">
      <Check size={15} />
      {children}
    </div>
  );
}
