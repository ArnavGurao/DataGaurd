import { ArrowRight, Database, Download, FileCheck2, ShieldCheck, UploadCloud } from 'lucide-react';
import { Button, PageHeading } from '../components/UI.jsx';
import { navigate, useWorkspace } from '../context.jsx';
import { DIRTY_CSV, GOOD_CSV, saveFile } from '../lib/validation.js';

export function Guide() {
  return (
    <>
      <PageHeading
        eyebrow="A LITTLE GUIDANCE"
        title="From CSV to confidence"
        description="Everything you need to make your first validation count."
      />
      <section className="guide-grid">
        {[
          [
            ShieldCheck,
            '01',
            'Define good data',
            'Choose Student records or create a rule set. Checks cover required columns, required values, duplicates, numeric ranges, and allowed values.',
            '/rules',
          ],
          [
            UploadCloud,
            '02',
            'Upload your CSV',
            'Use a comma-separated UTF-8 file with one header row. Files can contain up to 20,000 data rows, 50 columns, and 5 MiB.',
            '/upload',
          ],
          [
            FileCheck2,
            '03',
            'Understand the results',
            'A completed audit can contain rejected rows. Failed means the audit could not finish. Review each rule and the row-level error preview.',
            '/reports',
          ],
          [
            Download,
            '04',
            'Take the next step',
            'Download your original, valid rows, rejected rows with explanations, and JSON report. Upload a corrected version as a new job.',
            '/datasets',
          ],
        ].map(([Icon, num, title, description, path]) => (
          <article className="panel guide-card" key={num}>
            <span className="guide-icon">
              <Icon size={25} />
            </span>
            <small>STEP {num}</small>
            <h2>{title}</h2>
            <p>{description}</p>
            <button className="text-button" onClick={() => navigate(path)}>
              Let’s go
              <ArrowRight size={15} />
            </button>
          </article>
        ))}
      </section>
      <section className="panel guide-sample">
        <div>
          <h2>Practice with a familiar example</h2>
          <p>
            The sample has 6 rows: 2 pass, 4 are rejected. There are 5 rule failures because one row
            fails twice.
          </p>
          <p>The corrected sample passes all checks. Use the Student records preset for both.</p>
        </div>
        <div className="heading-actions">
          <Button variant="secondary" onClick={() => saveFile(DIRTY_CSV, 'students_dirty.csv')}>
            <Download size={15} />
            Dirty sample
          </Button>
          <Button onClick={() => saveFile(GOOD_CSV, 'students_corrected.csv')}>
            <Download size={15} />
            Corrected sample
          </Button>
        </div>
      </section>
      <div className="info-strip">
        <ShieldCheck size={20} />
        <p>
          Checks use trimmed, case-sensitive values. Exports preserve leading zeros, literal “NA”,
          and original strings, including spreadsheet formulas. Use a text editor for untrusted
          CSVs.
        </p>
      </div>
    </>
  );
}
export function Settings() {
  const { mode, user, logout, notify } = useWorkspace();
  return (
    <>
      <PageHeading
        eyebrow="MAKE YOURSELF AT HOME"
        title="Workspace settings"
        description="A clear view of your session and connection."
      />
      <section className="panel settings-panel">
        <div className="settings-row">
          <div>
            <h3>Current workspace</h3>
            <p>
              {mode === 'demo'
                ? 'A browser demo with synthetic sample data.'
                : 'Connected to your DataGuard backend.'}
            </p>
          </div>
          <span className={`status ${mode === 'demo' ? 'queued' : 'completed'}`}>
            <span />
            {mode === 'demo' ? 'Demo mode' : 'API mode'}
          </span>
        </div>
        <div className="settings-row">
          <div>
            <h3>Signed in as</h3>
            <p>{user.email}</p>
          </div>
          <Button variant="secondary" onClick={logout}>
            Sign out
          </Button>
        </div>
        <div className="settings-row">
          <div>
            <h3>Data and session</h3>
            <p>
              {mode === 'demo'
                ? 'Demo uploads and custom rules stay in memory and reset on page reload. No files are sent to a server.'
                : 'Login tokens stay in memory. Reloading requires you to sign in again; your datasets remain on the server.'}
            </p>
          </div>
        </div>
        <div className="settings-row">
          <div>
            <h3>Backend connection</h3>
            <p>
              Live mode uses the relative /api path. Local development forwards requests to FastAPI
              on port 8000.
            </p>
          </div>
          {mode === 'demo' && (
            <Button onClick={logout}>
              Connect your backend
              <ArrowRight size={15} />
            </Button>
          )}
        </div>
      </section>
    </>
  );
}
