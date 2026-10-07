import { test } from 'node:test';
import assert from 'node:assert/strict';
import Papa from 'papaparse';
import {
  auditCSV,
  parseCSV,
  validateRules,
  STUDENT_RULES,
  DIRTY_CSV,
  GOOD_CSV,
} from './validation.js';

test('README fixture: 6 total, 2 valid, 4 rejected, 5 failures; both duplicates rejected', () => {
  const { report, artifacts } = auditCSV(DIRTY_CSV, STUDENT_RULES);
  assert.deepEqual(report.summary, {
    total_rows: 6,
    valid_rows: 2,
    rejected_rows: 4,
    valid_percentage: 33.33,
  });
  assert.equal(
    report.failure_counts.reduce((sum, r) => sum + r.count, 0),
    5,
  );
  assert.equal(report.failure_counts.find((r) => r.code === 'DUPLICATE_VALUE').count, 2);
  assert.equal(report.error_preview.find((r) => r.record_number === 3).errors.length, 2);
  assert.equal(artifacts.original, DIRTY_CSV);
  assert.deepEqual(
    Papa.parse(artifacts.valid, { header: true }).data.map((r) => r.student_id),
    ['S001', 'S006'],
  );
});
test('corrected sample passes and produces a rejected CSV with headers only', () => {
  const { report, artifacts } = auditCSV(GOOD_CSV, STUDENT_RULES);
  assert.equal(report.summary.valid_rows, 6);
  assert.equal(report.summary.rejected_rows, 0);
  assert.equal(
    Papa.parse(artifacts.rejected, { header: true, skipEmptyLines: true }).data.length,
    0,
  );
  assert.ok(artifacts.rejected.includes('__dg_error_messages'));
});
test('missing columns complete an audit with every row rejected and a header-only valid export', () => {
  const { report, artifacts } = auditCSV(
    'student_id,name,age\nS001,Asha,20\nS002,Neha,21',
    STUDENT_RULES,
  );
  assert.equal(report.summary.total_rows, 2);
  assert.equal(report.summary.valid_rows, 0);
  assert.equal(report.dataset_errors[0].code, 'MISSING_COLUMN');
  assert.equal(report.error_preview.length, 2);
  assert.equal(Papa.parse(artifacts.valid, { header: true, skipEmptyLines: true }).data.length, 0);
});
test('leading-zero IDs, literal NA, and whitespace remain intact in exports', () => {
  const text = 'student_id,name,age,department\n001,NA,20,COMP\n002, Asha ,21, IT ';
  const { report, artifacts } = auditCSV(text, STUDENT_RULES);
  assert.equal(report.summary.valid_rows, 2);
  const rows = Papa.parse(artifacts.valid, { header: true }).data;
  assert.equal(rows[0].student_id, '001');
  assert.equal(rows[0].name, 'NA');
  assert.equal(rows[1].name, ' Asha ');
  assert.equal(rows[1].department, ' IT ');
});
test('supports BOM, CRLF, quoted commas, multiline values and logical record numbers', () => {
  const text =
    '\uFEFFstudent_id,name,age,department\r\nS001,"Asha,\r\nSharma",20,COMP\r\nS002,Neha,12,IT\r\n';
  const { report } = auditCSV(text, STUDENT_RULES);
  assert.equal(report.summary.total_rows, 2);
  assert.equal(report.error_preview[0].record_number, 2);
});
test('malformed quotes, widths, empty input, duplicate/reserved headers fail clearly', () => {
  for (const text of ['a,b\n"unclosed,b', 'a,b\n1,2,3', 'a,b\n1'])
    assert.throws(
      () => parseCSV(text),
      (e) => e.code === 'MALFORMED_CSV',
    );
  for (const text of ['', 'a,b\n'])
    assert.throws(
      () => parseCSV(text),
      (e) => e.code === 'EMPTY_DATASET',
    );
  for (const text of ['a, a\n1,2', 'a,\n1,2', '__dg_id,b\n1,2'])
    assert.throws(
      () => parseCSV(text),
      (e) => e.code === 'INVALID_HEADERS',
    );
});
test('numeric checks reject nonfinite, hex, nonnumeric, fractional, and out-of-range values', () => {
  for (const value of [
    'NaN',
    'Infinity',
    '-Infinity',
    '0x20',
    '19.5',
    '15',
    '101',
    '',
    'abc',
    '1e999',
  ]) {
    const { report } = auditCSV(
      `student_id,name,age,department\nS001,Asha,${value},COMP`,
      STUDENT_RULES,
    );
    assert.ok(
      report.failure_counts.some((f) => f.code === 'NUMERIC_RANGE'),
      value,
    );
  }
  for (const value of ['16', '100', '2e1', '+20', '20.0'])
    assert.equal(
      auditCSV(`student_id,name,age,department\nS001,Asha,${value},COMP`, STUDENT_RULES).report
        .summary.valid_rows,
      1,
    );
});
test('unique and allowed-value checks trim but stay case sensitive', () => {
  assert.equal(
    auditCSV('student_id,name,age,department\nS01,Asha,20,COMP\n S01 ,Neha,21,IT', STUDENT_RULES)
      .report.summary.rejected_rows,
    2,
  );
  assert.equal(
    auditCSV('student_id,name,age,department\nS01,Asha,20,comp\ns01,Neha,21,IT', STUDENT_RULES)
      .report.summary.rejected_rows,
    1,
  );
});
test('enforces row, column, byte limits and caps error preview at 100', () => {
  assert.throws(
    () => parseCSV('a\n' + '1\n'.repeat(20001)),
    (e) => e.code === 'ROW_LIMIT_EXCEEDED',
  );
  assert.throws(
    () =>
      parseCSV(
        Array.from({ length: 51 }, (_, i) => `c${i}`).join(',') +
          '\n' +
          Array(51).fill('1').join(','),
      ),
    (e) => e.code === 'COLUMN_LIMIT_EXCEEDED',
  );
  assert.throws(
    () => parseCSV('a\n' + 'x'.repeat(5 * 1024 * 1024)),
    (e) => e.code === 'FILE_TOO_LARGE',
  );
  const { report } = auditCSV(
    'student_id,name,age,department\n' +
      Array.from({ length: 110 }, (_, i) => `S${i},Asha,10,COMP`).join('\n'),
    STUDENT_RULES,
  );
  assert.equal(report.error_preview.length, 100);
  assert.equal(report.summary.rejected_rows, 110);
});
test('invalid ranges and missing referenced columns cannot be saved', () => {
  assert.throws(
    () => validateRules({ ...STUDENT_RULES, numeric_ranges: { age: { min: 100, max: 16 } } }),
    /minimum/,
  );
  assert.throws(
    () => validateRules({ ...STUDENT_RULES, required_columns: ['student_id'] }),
    /every rule column/,
  );
  assert.throws(
    () => validateRules({ ...STUDENT_RULES, allowed_values: { department: [] } }),
    /permitted value/,
  );
});
