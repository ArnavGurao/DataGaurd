import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  CheckCheck,
  CircleHelp,
  Database,
  FileCheck2,
  Layers3,
  Plus,
  ShieldCheck,
  Sparkles,
  UploadCloud,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { date, navigate, number, useWorkspace } from '../context.jsx';
import { Button, Empty, JobsTable, PageHeading } from '../components/UI.jsx';
import { saveFile } from '../lib/validation.js';

export function Dashboard() {
  const { jobs, rules, mode, notify } = useWorkspace();
  const [days, setDays] = useState(7);
  const completed = jobs.filter((j) => j.status === 'COMPLETED' && j.summary);
  const totalRows = completed.reduce((a, j) => a + j.summary.total_rows, 0);
  const validRows = completed.reduce((a, j) => a + j.summary.valid_rows, 0);
  const score = totalRows ? (validRows / totalRows) * 100 : 0;
  const recent = jobs.slice(0, 5);
  const chart = useMemo(
    () =>
      Array.from({ length: days }, (_, i) => {
        const day = new Date();
        day.setDate(day.getDate() - days + 1 + i);
        day.setHours(0, 0, 0, 0);
        const entries = completed.filter(
          (j) => new Date(j.created_at).toDateString() === day.toDateString(),
        );
        const total = entries.reduce((s, j) => s + j.summary.total_rows, 0),
          valid = entries.reduce((s, j) => s + j.summary.valid_rows, 0);
        return { date: day, score: total ? (valid / total) * 100 : null, total };
      }),
    [jobs, days],
  );
  const exportSummary = () => {
    saveFile(
      JSON.stringify(
        {
          exported_at: new Date().toISOString(),
          mode,
          total_datasets: jobs.length,
          total_rows: totalRows,
          valid_rows: validRows,
          rejected_rows: totalRows - validRows,
          quality_percentage: Number(score.toFixed(2)),
          jobs,
        },
        null,
        2,
      ),
      'dataguard-workspace-report.json',
      'application/json',
    );
    notify('Workspace report downloaded.');
  };
  return (
    <>
      <PageHeading
        eyebrow="YOUR DATA, IN GOOD HANDS"
        title="Workspace overview"
        description="A little clarity. A lot more confidence in your data."
        action={
          <div className="heading-actions">
            <Button variant="secondary" onClick={exportSummary}>
              <ArrowDownToLine size={16} />
              Export report
            </Button>
            <Button onClick={() => navigate('/upload')}>
              <Plus size={17} />
              Upload dataset
            </Button>
          </div>
        }
      />
      <section className="welcome-card">
        <div className="welcome-copy">
          <div className="welcome-label">
            <span className="live-dot" />
            READY FOR WHAT’S NEXT
          </div>
          <h2>
            Good decisions start
            <br />
            with good data.
          </h2>
          <p>
            Catch the gaps, find the duplicates, and turn
            <br className="desktop-br" /> your next CSV into data you can trust.
          </p>
          <Button variant="light" onClick={() => navigate('/upload')}>
            Validate a dataset
            <ArrowUpRight size={16} />
          </Button>
          <span className="welcome-footnote">
            <ShieldCheck size={13} />
            Your original data stays untouched
          </span>
        </div>
        <div className="hero-visual" aria-hidden="true">
          <div className="orb orb-one" />
          <div className="orb orb-two" />
          <div className="orbit orbit-one" />
          <div className="orbit orbit-two" />
          <div className="floating-label label-top">
            <span>
              <CheckCheck size={15} />
            </span>
            Duplicates detected
          </div>
          <div className="data-sheet">
            <div className="sheet-top">
              <span>
                <FileCheck2 size={17} />
              </span>
              <span>DATA QUALITY CHECK</span>
              <i />
            </div>
            {[0, 1, 2, 3].map((i) => (
              <div className="sheet-row" key={i}>
                <span>0{i + 1}</span>
                <i />
                <b />
                <CheckCheck size={14} />
              </div>
            ))}
            <div className="sheet-bottom">
              <span />
              <span />
              <span />
            </div>
          </div>
          <div className="hero-shield">
            <ShieldCheck size={56} strokeWidth={1.4} />
          </div>
          <div className="floating-label label-bottom">
            <span className="sparkle">
              <Sparkles size={14} />
            </span>
            A cleaner dataset awaits
          </div>
          <span className="visual-cross cross-one">+</span>
          <span className="visual-cross cross-two">+</span>
        </div>
      </section>
      <section className="stats-grid" aria-label="Workspace statistics">
        <Stat
          icon={Database}
          label="Total datasets"
          value={number(jobs.length)}
          note={`${completed.length} successfully audited`}
        />
        <Stat
          icon={Layers3}
          label="Rows processed"
          value={number(totalRows)}
          note="Across completed datasets"
        />
        <Stat
          icon={ShieldCheck}
          label="Overall data quality"
          value={
            <>
              {score.toFixed(1)}
              <span className="stat-unit">%</span>
            </>
          }
          note={
            totalRows
              ? `${number(validRows)} rows passed every check`
              : 'Upload a dataset to get started'
          }
          highlight
        />
        <Stat
          icon={FileCheck2}
          label="Validation rule sets"
          value={String(rules.length).padStart(2, '0')}
          note="Reusable checks, consistent results"
        />
      </section>
      <div className="insight-grid">
        <section className="panel trend-panel">
          <div className="panel-heading">
            <div>
              <h2>
                Data quality over time
                <CircleHelp size={14}>
                  <title>Daily percentage of rows passing every check</title>
                </CircleHelp>
              </h2>
              <p>Better data, one upload at a time.</p>
            </div>
            <select
              aria-label="Chart date range"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
            >
              <option value={7}>Last 7 days</option>
              <option value={14}>Last 14 days</option>
              <option value={30}>Last 30 days</option>
            </select>
          </div>
          <div className="chart-legend">
            <span />
            Valid rows <small>Daily pass rate</small>
          </div>
          <QualityChart data={chart} />
        </section>
        <section className="panel quick-panel">
          <div className="panel-heading">
            <div>
              <h2>A smooth start</h2>
              <p>Your next clean dataset is a few steps away.</p>
            </div>
            <span className="small-sparkle">
              <Sparkles size={19} />
            </span>
          </div>
          <div className="steps">
            <QuickStep
              index="01"
              title="Set your standards"
              text="Choose a preset or create your own rules."
              onClick={() => navigate('/rules')}
            />
            <QuickStep
              index="02"
              title="Bring your dataset"
              text="Upload a CSV. We’ll take it from here."
              onClick={() => navigate('/upload')}
            />
            <QuickStep
              index="03"
              title="See the full picture"
              text="Explore issues and export validated rows."
              onClick={() => navigate('/reports')}
            />
          </div>
          <button className="guide-link" onClick={() => navigate('/guide')}>
            <CircleHelp size={15} />
            New to DataGuard? Explore the guide
            <ArrowUpRight size={15} />
          </button>
        </section>
      </div>
      <section className="panel recent-panel">
        <div className="panel-heading">
          <div>
            <h2>
              Recent datasets <span className="count-pill">{jobs.length}</span>
            </h2>
            <p>A quick look at your latest validation activity.</p>
          </div>
          <button className="text-button" onClick={() => navigate('/datasets')}>
            View all datasets
            <ArrowRight size={15} />
          </button>
        </div>
        {recent.length ? (
          <JobsTable jobs={recent} />
        ) : (
          <Empty
            title="Your first dataset belongs here"
            description="Upload a CSV to see your data quality at a glance."
            action={
              <Button onClick={() => navigate('/upload')}>
                <UploadCloud size={16} />
                Upload dataset
              </Button>
            }
          />
        )}
      </section>
      <footer className="page-footer">
        <span>
          <ShieldCheck size={14} />
          Built for clarity. Designed for trust.
        </span>
        <span>
          DataGuard <span className="footer-dot">·</span>{' '}
          {mode === 'demo' ? 'Demo workspace' : 'Your workspace'}
        </span>
      </footer>
    </>
  );
}
function Stat({ icon: Icon, label, value, note, highlight }) {
  return (
    <article className={`stat-card ${highlight ? 'highlight' : ''}`}>
      <div className="stat-top">
        <span>{label}</span>
        <span className="stat-icon">
          <Icon size={18} />
        </span>
      </div>
      <strong className="stat-value">{value}</strong>
      <p>
        {highlight && <span className="green-dot" />}
        {note}
      </p>
    </article>
  );
}
function QuickStep({ index, title, text, onClick }) {
  return (
    <button className="quick-step" onClick={onClick}>
      <span className="step-number">{index}</span>
      <span>
        <strong>{title}</strong>
        <small>{text}</small>
      </span>
      <ArrowRight size={15} />
    </button>
  );
}
function QualityChart({ data }) {
  const [hover, setHover] = useState(null),
    w = 660,
    h = 150,
    left = 46,
    right = 15,
    top = 14,
    bottom = 125;
  const x = (i) => left + (i * (w - left - right)) / (data.length - 1);
  const y = (score) => bottom - (score / 100) * (bottom - top);
  // Gaps remain gaps: a day with no completed rows never becomes a fake zero.
  let path = '',
    connected = false;
  data.forEach((d, i) => {
    if (d.score === null) {
      connected = false;
      return;
    }
    path += `${connected ? 'L' : 'M'}${x(i)},${y(d.score)} `;
    connected = true;
  });
  const first = data.findIndex((d) => d.score !== null),
    last = data.findLastIndex((d) => d.score !== null);
  const continuous = first >= 0 && data.slice(first, last + 1).every((d) => d.score !== null);
  return (
    <div className="quality-chart">
      <svg
        viewBox={`0 0 ${w} ${h}`}
        role="img"
        aria-label={`Daily data quality for ${data.length} days: ${data.map((d) => `${date(d.date)} ${d.score === null ? 'no data' : d.score.toFixed(1) + '%'}`).join(', ')}`}
      >
        <defs>
          <linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#51846a" stopOpacity=".2" />
            <stop offset="100%" stopColor="#51846a" stopOpacity=".01" />
          </linearGradient>
        </defs>
        {[0, 25, 50, 75, 100].map((tick) => (
          <g key={tick}>
            <text x="0" y={y(tick) + 4} className="chart-tick">
              {tick}%
            </text>
            <line
              x1={left}
              x2={w - right}
              y1={y(tick)}
              y2={y(tick)}
              stroke="#e8ece7"
              strokeDasharray="3 4"
            />
          </g>
        ))}
        {continuous && (
          <path
            d={`${path} L${x(last)},${bottom} L${x(first)},${bottom} Z`}
            fill="url(#chart-fill)"
          />
        )}
        <path d={path} fill="none" stroke="#387158" strokeWidth="2.5" strokeLinejoin="round" />
        {data.map((d, i) => (
          <g key={i}>
            {d.score !== null && (
              <circle
                cx={x(i)}
                cy={y(d.score)}
                r={hover === i ? 5 : 3}
                fill="#fff"
                stroke="#387158"
                strokeWidth="2"
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              >
                <title>
                  {date(d.date)}: {d.score.toFixed(1)}% · {number(d.total)} rows
                </title>
              </circle>
            )}
            {(data.length <= 7 ||
              i % Math.ceil(data.length / 7) === 0 ||
              i === data.length - 1) && (
              <text x={x(i)} y="147" textAnchor="middle" className="chart-tick">
                {d.date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
              </text>
            )}
          </g>
        ))}
      </svg>
      {first < 0 && <div className="chart-empty">Completed audits will appear here.</div>}
    </div>
  );
}
