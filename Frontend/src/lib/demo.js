import { auditCSV, STUDENT_RULES, validateRules, saveFile } from './validation.js';

const rules = [
  {
    id: 'students',
    name: 'Student records',
    description: 'Reliable records, from enrollment to graduation.',
    rules: STUDENT_RULES,
    created_at: new Date().toISOString(),
  },
  {
    id: 'customers',
    name: 'Customer directory',
    description: 'Keep customer IDs unique and contact details complete.',
    rules: {
      required_columns: ['customer_id', 'name', 'email', 'region'],
      required_values: ['customer_id', 'name', 'email'],
      unique_columns: ['customer_id'],
      numeric_ranges: {},
      allowed_values: { region: ['North', 'South', 'East', 'West'] },
    },
    created_at: new Date().toISOString(),
  },
  {
    id: 'inventory',
    name: 'Product inventory',
    description: 'Consistent SKUs, sensible prices, accurate stock.',
    rules: {
      required_columns: ['sku', 'name', 'price', 'stock'],
      required_values: ['sku', 'name'],
      unique_columns: ['sku'],
      numeric_ranges: {
        price: { min: 0, max: 1000000, integer: false },
        stock: { min: 0, max: 1000000, integer: true },
      },
      allowed_values: {},
    },
    created_at: new Date().toISOString(),
  },
];
const jobs = [],
  outputs = new Map(),
  pending = new Map();
const names = ['Asha', 'Arjun', 'Rohan', 'Meera', 'Ishan', 'Neha'];
function sampleCSV(count, invalid) {
  return (
    'student_id,name,age,department\n' +
    Array.from(
      { length: count },
      (_, i) =>
        `S${String(i + 1).padStart(4, '0')},${i < invalid && i % 3 === 0 ? '' : names[i % names.length]},${i < invalid && i % 3 === 1 ? 12 : 18 + (i % 12)},${i < invalid && i % 3 === 2 ? 'MECH' : ['COMP', 'IT', 'EXTC'][i % 3]}`,
    ).join('\n')
  );
}
const seed = [
  ['Engineering admissions', 'engineering_admissions.csv', 1250, 24, 0],
  ['Fall semester enrollment', 'fall_enrollment.csv', 2400, 0, 1],
  ['Scholarship applications', 'scholarship_applications.csv', 860, 68, 2],
  ['Department directory', 'department_directory.csv', 540, 12, 3],
  ['New student intake', 'student_intake.csv', 1800, 145, 4],
  ['Campus registrations', 'campus_registrations.csv', 1100, 91, 5],
  ['Orientation attendance', 'orientation_attendance.csv', 960, 108, 6],
];
seed.forEach(([title, original_name, count, invalid, days], index) => {
  const job_id = `demo-${index + 1}`;
  const created = new Date();
  created.setDate(created.getDate() - days);
  created.setHours(9, 24 - index, 0, 0);
  if (created > new Date()) created.setTime(Date.now() - (index + 1) * 3600000);
  const result = auditCSV(sampleCSV(count, invalid), STUDENT_RULES, job_id);
  result.report.processed_at = new Date(created.getTime() + 3200).toISOString();
  result.report.processing_duration_ms = 3200;
  result.artifacts.report = JSON.stringify(result.report, null, 2);
  outputs.set(job_id, result);
  jobs.push({
    job_id,
    title,
    original_name,
    rule_set_id: 'students',
    rule_set_name: 'Student records',
    status: 'COMPLETED',
    created_at: created.toISOString(),
    completed_at: result.report.processed_at,
    summary: result.report.summary,
    available_downloads: ['original', 'valid', 'rejected', 'report'],
    error: null,
  });
});
function progress(job) {
  const p = pending.get(job.job_id);
  if (!p) return job;
  const elapsed = Date.now() - p.started;
  job.status = elapsed < 700 ? 'PENDING_DISPATCH' : elapsed < 1800 ? 'QUEUED' : 'RUNNING';
  if (elapsed >= 3800) {
    try {
      const output = auditCSV(p.text, p.rules, job.job_id);
      outputs.set(job.job_id, output);
      Object.assign(job, {
        status: 'COMPLETED',
        summary: output.report.summary,
        available_downloads: ['original', 'valid', 'rejected', 'report'],
      });
    } catch (error) {
      outputs.set(job.job_id, { artifacts: { original: p.text } });
      Object.assign(job, {
        status: 'FAILED',
        error: { code: error.code || 'INVALID_INPUT', message: error.message },
        available_downloads: ['original'],
      });
    }
    job.completed_at = new Date().toISOString();
    pending.delete(job.job_id);
  }
  return job;
}
export const demoService = {
  async listJobs() {
    return structuredClone(jobs.map(progress));
  },
  async listRules() {
    return structuredClone(rules);
  },
  async createRule(body) {
    validateRules(body.rules);
    const rule = {
      ...structuredClone(body),
      id: crypto.randomUUID(),
      created_at: new Date().toISOString(),
    };
    rules.push(rule);
    return structuredClone(rule);
  },
  async createJob(form) {
    const file = form.get('file');
    if (file.size > 5242880) throw new Error('Upload a CSV of 5 MiB or less.');
    const rule = rules.find((r) => r.id === form.get('rule_set_id'));
    if (!rule) throw new Error('Select an available rule set.');
    let text;
    try {
      text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
    } catch {
      throw new Error('Save your CSV with UTF-8 encoding and try again.');
    }
    const job = {
      job_id: crypto.randomUUID(),
      title: form.get('title'),
      original_name: file.name,
      rule_set_id: rule.id,
      rule_set_name: rule.name,
      status: 'PENDING_DISPATCH',
      created_at: new Date().toISOString(),
      summary: null,
      available_downloads: [],
      error: null,
    };
    pending.set(job.job_id, { started: Date.now(), text, rules: structuredClone(rule.rules) });
    jobs.unshift(job);
    return { job_id: job.job_id, status: job.status };
  },
  async getJob(id) {
    const job = jobs.find((j) => j.job_id === id);
    if (!job) throw Object.assign(new Error('This dataset could not be found.'), { status: 404 });
    return structuredClone(progress(job));
  },
  async getReport(id) {
    if (!outputs.get(id)?.report) throw new Error('This report is not ready yet.');
    return structuredClone(outputs.get(id).report);
  },
  async download(id, kind) {
    const job = jobs.find((j) => j.job_id === id),
      content = outputs.get(id)?.artifacts[kind];
    if (content === undefined) throw new Error('This download is not available yet.');
    saveFile(
      content,
      kind === 'original'
        ? job.original_name
        : `${job.original_name.replace(/\.csv$/i, '')}_${kind}.${kind === 'report' ? 'json' : 'csv'}`,
      kind === 'report' ? 'application/json' : 'text/csv;charset=utf-8',
    );
  },
};
