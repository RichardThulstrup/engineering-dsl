'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {Checker} = require('../src/client');
const root = path.resolve(__dirname, '../..');
const helper = path.resolve(__dirname, '../python/checker.py');
const python = process.env.EDSL_TEST_PYTHON || 'python';

test('checker protocol handles Unicode and independent concurrent notebook requests', async () => {
  const worker = new Checker(python, helper, root, () => {});
  try {
    const [valid, invalid] = await Promise.all([
      worker.check(['symbols: x', 'solve(x² ≡ 4, x)', 'assert 2 ≟ 2'], 'python'),
      worker.check(['_bad = )'], 'python'),
    ]);
    assert.ok(valid.every(cell => cell.diagnostics.length === 0));
    assert.equal(invalid[0].diagnostics.length, 1);
    assert.ok(valid[1].python.includes('Eq('));
  } finally { worker.dispose(); }
  await assert.rejects(worker.check(['1'], 'python'), /stopped/);
});

test('a missing interpreter fails the pending check promptly', async () => {
  const worker = new Checker(path.join(__dirname, 'missing-python'), helper, root, () => {});
  try { await assert.rejects(worker.check(['1'], 'python'), /ENOENT/); }
  finally { worker.dispose(); }
});

test('disposal rejects in-flight work instead of leaking promises', async () => {
  const worker = new Checker(python, helper, root, () => {});
  const request = worker.check(['symbols: x', 'x ≡ 2'], 'python');
  worker.dispose();
  await assert.rejects(request, /stopped/);
});


test('checker environment follows the registered DSL kernel', () => {
  const fs = require('node:fs'), os = require('node:os');
  const {registeredKernel} = require('../src/kernel-environment');
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'edsl-kernel-spec-'));
  try {
    const kernel = path.join(temporary, 'kernels', 'engineering-dsl');
    fs.mkdirSync(kernel, {recursive: true});
    const script = path.join(temporary, 'utils', 'vscode_kernel.py');
    fs.mkdirSync(path.dirname(script));
    fs.writeFileSync(script, '# fixture');
    const file = path.join(kernel, 'kernel.json');
    const env = {JUPYTER_DATA_DIR: temporary, APPDATA: temporary};
    fs.writeFileSync(file, JSON.stringify({language: 'engineering-dsl',
      argv: [process.execPath, script, '-f', '{connection_file}']}));
    assert.deepEqual(registeredKernel(env, temporary), {python: process.execPath, root: temporary});
    fs.writeFileSync(file, JSON.stringify({language: 'python', argv: [process.execPath]}));
    assert.equal(registeredKernel(env, temporary), undefined);
  } finally { fs.rmSync(temporary, {recursive: true, force: true}); }
});
