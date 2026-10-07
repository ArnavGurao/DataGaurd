import { useEffect, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CircleAlert,
  Clock3,
  FileCheck2,
  FileSpreadsheet,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  X,
} from 'lucide-react';
import {
  Button,
  DownloadButton,
  Empty,
  ErrorBox,
  Loading,
  PageHeading,
  Status,
} from '../components/UI.jsx';
import { ACTIVE, date, navigate, number, useWorkspace } from '../context.jsx';
import { RuleSummary } from './Rules.jsx';

export function JobDetails({ id }) {
  const { service, handleError, refresh } = useWorkspace();
  const [job, setJob] = useState(null),
    [report, setReport] = useState(null),
    [error, setError] = useState(null),
    [attempt, setAttempt] = useState(0),
    [tab, setTab] = useState('issues');
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    setJob(null);
    setReport(null);
    setError(null);
    async function poll() {
      try {
        const result = await service.getJob(id, controller.signal);
        if (controller.signal.aborted) return;
        setJob(result);
        if (result.status === 'COMPLETED') {
          const data = await service.getReport(id, controller.signal);
          if (!controller.signal.aborted) {
            setReport(data);
            refresh();
          }
        } else if (ACTIVE.includes(result.status)) timer = setTimeout(poll, 3000);
        else refresh();
      } catch (e) {
        if (!controller.signal.aborted) {
          setError(e);
          handleError(e);
        }
      }
    }
    poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, service, attempt, handleError, refresh]);
  if (!job && !error) return <Loading />;
  return (
    <>
      <button className="back-link" onClick={() => navigate('/datasets')}>
        <ArrowLeft size={15} />
        All datasets
      </button>
      {error && <ErrorBox error={error} onRetry={() => setAttempt((a) => a + 1)} />}
      {job && (
        <>
          <PageHeading
            eyebrow="DATASET REPORT"
            title={job.title}
            description={`${job.original_name || 'CSV dataset'} · Uploaded ${new Date(job.created_at).toLocaleString()}`}
            action={
              <div className="heading-actions">
                <Status status={job.status} />
                <Button variant="secondary" onClick={() => setAttempt((a) => a + 1)}>
                  <RefreshCw size={15} />
                  Refresh
                </Button>
              </div>
            }
          />
          {ACTIVE.includes(job.status) && (
            <section className="panel processing-panel">
              <span className="processing-icon">
                <LoaderCircle size={32} className="spin" />
              </span>
              <h2>
                {job.status === 'RUNNING'
                  ? 'A closer look at your data…'
                  : 'Your dataset is in line.'}
              </h2>
              <p>
                {job.status === 'RUNNING'
                  ? 'Checking each row against your rules. Your report will appear here automatically.'
                  : 'Your upload has been accepted. Validation will begin shortly.'}
              </p>
              <div className="processing-steps">
                {['Upload received', 'Queued', 'Validating', 'Report ready'].map((label, i) => (
                  <span
                    key={label}
                    className={
                      i <= (job.status === 'RUNNING' ? 2 : job.status === 'QUEUED' ? 1 : 0)
                        ? 'done'
                        : ''
                    }
                  >
                    <Check size={14} />
                    {label}
                  </span>
                ))}
              </div>
              <small>Updates every 3 seconds · You can safely return to your workspace</small>
            </section>
          )}
          {job.status === 'FAILED' && (
            <section className="panel failed-panel">
              <CircleAlert size={34} />
              <h2>This audit couldn’t finish</h2>
              <p>
                {job.error?.message ||
                  'Processing was interrupted. Check your CSV and try uploading again.'}
              </p>
              {job.error?.code && <code>{job.error.code}</code>}
              <div className="heading-actions">
                <Button onClick={() => navigate('/upload')}>
                  Upload a corrected file
                  <ArrowRight size={15} />
                </Button>
                {job.available_downloads?.includes('original') && (
                  <DownloadButton job={job} kind="original" label="Original CSV" />
                )}
              </div>
            </section>
          )}
          {job.status === 'COMPLETED' && job.summary && (
            <>
              <section className="report-overview panel">
                <div
                  className="score-ring"
                  style={{ '--score': `${job.summary.valid_percentage}%` }}
                >
                  <div>
                    <strong>
                      {job.summary.valid_percentage.toFixed(1)}
                      <small>%</small>
                    </strong>
                    <span>QUALITY SCORE</span>
                  </div>
                </div>
                <div className="report-summary-copy">
                  <span className="eyebrow">VALIDATION COMPLETE</span>
                  <h2>
                    {job.summary.rejected_rows
                      ? 'A clear picture of what needs attention.'
                      : 'Looking good. Every row passed.'}
                  </h2>
                  <p>
                    {job.summary.rejected_rows
                      ? 'Your audit finished successfully. Review the rejected rows below to see exactly what to fix.'
                      : 'Your dataset meets all the checks in its rule set. Download your validated records below.'}
                  </p>
                  <span className="muted">
                    {job.rule_set_name || 'Saved rule snapshot'} ·{' '}
                    {job.completed_at ? `Completed ${date(job.completed_at)}` : 'Audit complete'}
                  </span>
                </div>
                <div className="report-counts">
                  <div>
                    <span>
                      <FileSpreadsheet size={16} />
                      Total rows
                    </span>
                    <strong>{number(job.summary.total_rows)}</strong>
                  </div>
                  <div>
                    <span>
                      <Check size={16} />
                      Valid rows
                    </span>
                    <strong className="green-text">{number(job.summary.valid_rows)}</strong>
                  </div>
                  <div>
                    <span>
                      <X size={16} />
                      Rejected rows
                    </span>
                    <strong className="amber-text">{number(job.summary.rejected_rows)}</strong>
                  </div>
                </div>
              </section>
              <div className="download-strip">
                <div>
                  <FileCheck2 size={21} />
                  <span>
                    <strong>Your results, ready to go</strong>
                    <small>CSV exports preserve original values.</small>
                  </span>
                </div>
                <div>
                  {(job.available_downloads || []).map((kind) => (
                    <DownloadButton
                      key={kind}
                      job={job}
                      kind={kind}
                      label={
                        {
                          original: 'Original CSV',
                          valid: 'Valid rows',
                          rejected: 'Rejected rows',
                          report: 'JSON report',
                        }[kind]
                      }
                    />
                  ))}
                </div>
              </div>
              {!report && !error && <Loading />}
              {report && (
                <>
                  {report.dataset_errors?.length > 0 && (
                    <div className="schema-warning">
                      <CircleAlert size={20} />
                      <div>
                        <strong>Dataset schema needs attention</strong>
                        {report.dataset_errors.map((e, i) => (
                          <p key={i}>{e.message}</p>
                        ))}
                        <p>
                          All rows were rejected because the required schema could not be validated.
                          The audit itself completed.
                        </p>
                      </div>
                    </div>
                  )}
                  <section className="panel report-panel">
                    <div className="tabs" role="tablist" aria-label="Report sections">
                      {[
                        ['issues', 'Validation results'],
                        ['preview', 'Rejected row preview'],
                        ['rules', 'Rules used'],
                      ].map(([value, label]) => (
                        <button
                          key={value}
                          role="tab"
                          aria-selected={tab === value}
                          aria-controls={`panel-${value}`}
                          id={`tab-${value}`}
                          onClick={() => setTab(value)}
                          className={tab === value ? 'active' : ''}
                        >
                          {label}
                          {value === 'preview' && (
                            <span className="count-pill">{report.error_preview?.length || 0}</span>
                          )}
                        </button>
                      ))}
                    </div>
                    <div
                      className="report-tab-content"
                      role="tabpanel"
                      id={`panel-${tab}`}
                      aria-labelledby={`tab-${tab}`}
                    >
                      {tab === 'issues' && (
                        <>
                          <div className="panel-heading">
                            <div>
                              <h2>Every issue, explained</h2>
                              <p>
                                A row can fail more than one rule, so failure counts may exceed
                                rejected rows.
                              </p>
                            </div>
                          </div>
                          {report.failure_counts?.length ? (
                            <div className="failure-list">
                              {report.failure_counts.map((failure, i) => (
                                <div
                                  className="failure-row"
                                  key={`${failure.code}-${failure.column}-${i}`}
                                >
                                  <span className="failure-icon">
                                    <CircleAlert size={19} />
                                  </span>
                                  <div>
                                    <strong>
                                      {{
                                        REQUIRED_VALUE: 'Missing required value',
                                        DUPLICATE_VALUE: 'Duplicate value',
                                        NUMERIC_RANGE: 'Outside numeric range',
                                        ALLOWED_VALUE: 'Unexpected value',
                                        MISSING_COLUMN: 'Missing required column',
                                      }[failure.code] || failure.code}{' '}
                                      <code>{failure.column}</code>
                                    </strong>
                                    <p>{failure.message}</p>
                                  </div>
                                  <span className="failure-count">
                                    {number(failure.count)} <small>rows</small>
                                  </span>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <Empty
                              title="All checks passed"
                              description="No validation issues were found in this dataset."
                            />
                          )}
                        </>
                      )}
                      {tab === 'preview' && (
                        <>
                          <div className="panel-heading">
                            <div>
                              <h2>Find it. Understand it. Fix it.</h2>
                              <p>
                                Showing up to 100 rejected records. Record numbers count data rows,
                                excluding the header.
                              </p>
                            </div>
                          </div>
                          {report.error_preview?.length ? (
                            <div className="table-scroll">
                              <table className="preview-table">
                                <thead>
                                  <tr>
                                    <th>Record</th>
                                    <th>Original values</th>
                                    <th>What to fix</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {report.error_preview.map((row) => (
                                    <tr key={row.record_number}>
                                      <td>
                                        <span className="record-number">#{row.record_number}</span>
                                      </td>
                                      <td>
                                        <div className="original-values">
                                          {Object.entries(row.values).map(([k, v]) => (
                                            <span key={k}>
                                              <small>{k}</small>
                                              <code>{v || '(blank)'}</code>
                                            </span>
                                          ))}
                                        </div>
                                      </td>
                                      <td>
                                        {row.errors.map((e, i) => (
                                          <p className="row-error" key={i}>
                                            <span />
                                            {e.message}
                                          </p>
                                        ))}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          ) : (
                            <Empty
                              title="No rejected rows"
                              description="Every record passed all of your checks."
                            />
                          )}
                        </>
                      )}
                      {tab === 'rules' && (
                        <>
                          <h2>The checks behind this report</h2>
                          <p className="muted">
                            An immutable snapshot of the rule set used when this dataset was
                            uploaded.
                          </p>
                          <RuleSummary rules={report.rules_snapshot} />
                        </>
                      )}
                    </div>
                  </section>
                </>
              )}
              <div className="next-dataset">
                <span>
                  <strong>A small fix can make a big difference.</strong>
                  <p>Upload a corrected version as a new dataset to see how the results improve.</p>
                </span>
                <Button variant="secondary" onClick={() => navigate('/upload')}>
                  Validate another dataset
                  <ArrowRight size={15} />
                </Button>
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
