import Papa from 'papaparse';

export const LIMITS = { bytes: 5 * 1024 * 1024, rows: 20000, columns: 50 };
export const STUDENT_RULES = {
  required_columns: ['student_id', 'name', 'age', 'department'],
  required_values: ['student_id', 'name', 'age', 'department'],
  unique_columns: ['student_id'],
  numeric_ranges: { age: { min: 16, max: 100, integer: true } },
  allowed_values: { department: ['COMP', 'IT', 'EXTC'] },
};
export const DIRTY_CSV =
  'student_id,name,age,department\r\nS001,Asha,20,COMP\r\nS002,,21,IT\r\nS003,Rohan,15,COMP\r\nS003,Meera,22,COMP\r\nS005,Ishan,19,MECH\r\nS006,Neha,23,IT\r\n';
export const GOOD_CSV =
  'student_id,name,age,department\r\nS001,Asha,20,COMP\r\nS002,Arjun,21,IT\r\nS003,Rohan,20,COMP\r\nS004,Meera,22,COMP\r\nS005,Ishan,19,EXTC\r\nS006,Neha,23,IT\r\n';

function fail(code, message) {
  throw Object.assign(new Error(message), { code });
}
export function validateRules(rules) {
  const cols = rules.required_columns;
  if (!Array.isArray(cols) || !cols.length || cols.length > LIMITS.columns)
    fail('INVALID_RULES', 'Add between 1 and 50 required columns.');
  if (
    cols.some((c) => !c.trim() || c !== c.trim() || c.startsWith('__dg_')) ||
    new Set(cols).size !== cols.length
  )
    fail('INVALID_RULES', 'Column names must be unique, nonblank, and cannot begin with __dg_.');
  const referenced = [
    ...rules.required_values,
    ...rules.unique_columns,
    ...Object.keys(rules.numeric_ranges),
    ...Object.keys(rules.allowed_values),
  ];
  if (referenced.some((c) => !cols.includes(c)))
    fail('INVALID_RULES', 'Include every rule column in Required columns.');
  for (const range of Object.values(rules.numeric_ranges)) {
    if (!Number.isFinite(range.min) || !Number.isFinite(range.max) || range.min > range.max)
      fail('INVALID_RULES', 'Numeric bounds must be finite and minimum cannot exceed maximum.');
  }
  if (
    Object.values(rules.allowed_values).some(
      (values) => !values.length || values.some((v) => !v.trim()),
    )
  )
    fail('INVALID_RULES', 'Add at least one nonblank permitted value.');
  return rules;
}

export function parseCSV(text) {
  if (new TextEncoder().encode(text).length > LIMITS.bytes)
    fail('FILE_TOO_LARGE', 'Upload a CSV of 5 MiB or less.');
  const parsed = Papa.parse(text.replace(/^\uFEFF/, ''), { skipEmptyLines: true, delimiter: ',' });
  if (parsed.errors.length)
    fail(
      'MALFORMED_CSV',
      'The CSV has broken quoting. Check quotes and commas, then upload again.',
    );
  if (!parsed.data.length)
    fail('EMPTY_DATASET', 'Your CSV is empty. Include a header and at least one data row.');
  const [rawHeaders, ...records] = parsed.data;
  const headers = rawHeaders.map((h) => h.trim());
  if (headers.length > LIMITS.columns)
    fail('COLUMN_LIMIT_EXCEEDED', 'Use no more than 50 columns.');
  if (headers.some((h) => !h || h.startsWith('__dg_')) || new Set(headers).size !== headers.length)
    fail(
      'INVALID_HEADERS',
      'Headers must be nonblank, unique after trimming, and cannot start with __dg_.',
    );
  if (!records.length) fail('EMPTY_DATASET', 'Include at least one data row below the header.');
  if (records.length > LIMITS.rows)
    fail('ROW_LIMIT_EXCEEDED', 'Use no more than 20,000 data rows.');
  if (records.some((row) => row.length !== headers.length))
    fail(
      'MALFORMED_CSV',
      'Every row must have the same number of fields as the header. Check commas and quoting.',
    );
  return {
    headers,
    rows: records.map((row) => Object.fromEntries(headers.map((h, i) => [h, row[i]]))),
  };
}

export function auditCSV(text, rules, jobId = 'preview') {
  validateRules(rules);
  const start = performance.now();
  const { headers, rows } = parseCSV(text);
  const missing = rules.required_columns.filter((c) => !headers.includes(c));
  const counts = new Map();
  const duplicates = Object.fromEntries(
    rules.unique_columns.map((column) => {
      const seen = new Map();
      rows.forEach((row) => {
        const v = row[column]?.trim();
        seen.set(v, (seen.get(v) || 0) + 1);
      });
      return [column, seen];
    }),
  );
  const valid = [],
    rejected = [];
  rows.forEach((row, i) => {
    const errors = [];
    const add = (code, column, message) => {
      errors.push({ code, column, message });
      const key = `${code}:${column}`;
      const count = counts.get(key) || { code, column, message, count: 0 };
      count.count++;
      counts.set(key, count);
    };
    if (missing.length) {
      missing.forEach((column) =>
        add('MISSING_COLUMN', column, `Required column “${column}” is missing.`),
      );
    } else {
      rules.required_values.forEach((column) => {
        if (!row[column].trim()) add('REQUIRED_VALUE', column, `${column} must not be blank.`);
      });
      rules.unique_columns.forEach((column) => {
        if (duplicates[column].get(row[column].trim()) > 1)
          add(
            'DUPLICATE_VALUE',
            column,
            `${column} must be unique; all duplicate occurrences are rejected.`,
          );
      });
      Object.entries(rules.numeric_ranges).forEach(([column, range]) => {
        const value = row[column].trim(),
          number = Number(value);
        if (
          !/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(value) ||
          !Number.isFinite(number) ||
          number < range.min ||
          number > range.max ||
          (range.integer && !Number.isInteger(number))
        ) {
          add(
            'NUMERIC_RANGE',
            column,
            `${column} must be ${range.integer ? 'an integer' : 'a number'} from ${range.min} to ${range.max}.`,
          );
        }
      });
      Object.entries(rules.allowed_values).forEach(([column, values]) => {
        if (!values.includes(row[column].trim()))
          add('ALLOWED_VALUE', column, `${column} must be one of: ${values.join(', ')}.`);
      });
    }
    if (errors.length) rejected.push({ record_number: i + 1, values: row, errors });
    else valid.push(row);
  });
  const report = {
    schema_version: '1.0',
    job_id: jobId,
    processed_at: new Date().toISOString(),
    rules_snapshot: structuredClone(rules),
    summary: {
      total_rows: rows.length,
      valid_rows: valid.length,
      rejected_rows: rejected.length,
      valid_percentage: Number(((valid.length / rows.length) * 100).toFixed(2)),
    },
    failure_counts: [...counts.values()],
    dataset_errors: missing.map((column) => ({
      code: 'MISSING_COLUMN',
      column,
      message: `Required column “${column}” is missing.`,
    })),
    error_preview: rejected.slice(0, 100),
    processing_duration_ms: Math.round(performance.now() - start),
  };
  const csv = (fields, data) => Papa.unparse({ fields, data });
  return {
    report,
    artifacts: {
      original: text,
      valid: csv(
        headers,
        valid.map((row) => headers.map((h) => row[h])),
      ),
      rejected: csv(
        [...headers, '__dg_record_number', '__dg_error_codes', '__dg_error_messages'],
        rejected.map((r) => [
          ...headers.map((h) => r.values[h]),
          r.record_number,
          r.errors.map((e) => e.code).join('; '),
          r.errors.map((e) => e.message).join('; '),
        ]),
      ),
      report: JSON.stringify(report, null, 2),
    },
  };
}

export function saveFile(content, filename, type = 'text/csv;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
