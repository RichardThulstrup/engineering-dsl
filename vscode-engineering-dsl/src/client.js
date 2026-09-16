'use strict';
const {spawn} = require('node:child_process');
const readline = require('node:readline');

// One process per interpreter/root, shared by notebooks. Each request resets
// transformer state in the helper; no notebook code is ever executed.
class Checker {
  constructor(python, helper, root, log) {
    this.pending = new Map();
    this.sequence = 0;
    this.dead = false;
    this.child = spawn(python, ['-B', '-u', helper], {
      cwd: root || undefined, windowsHide: true,
      env: {...process.env, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1',
        ENGINEERING_DSL_ROOT: root || ''},
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this.lines = readline.createInterface({input: this.child.stdout});
    this.lines.on('line', line => {
      let message;
      try { message = JSON.parse(line); } catch { this.fail(new Error('Invalid syntax checker response')); return; }
      const request = this.pending.get(message.id);
      if (!request) return;
      this.pending.delete(message.id);
      clearTimeout(request.timer);
      message.error ? request.reject(new Error(message.error)) : request.resolve(message.cells);
    });
    this.child.stderr.on('data', data => log(data.toString()));
    this.child.stdin.on('error', error => this.fail(error));
    this.child.on('error', error => this.fail(error));
    this.child.on('exit', code => this.fail(new Error(`Syntax checker exited (${code})`)));
  }
  check(cells, mode) {
    if (this.dead) return Promise.reject(new Error('Syntax checker is stopped'));
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(() => this.fail(new Error('Syntax check timed out after 30 seconds')), 30000);
      this.pending.set(id, {resolve, reject, timer});
      this.child.stdin.write(JSON.stringify({id, cells, mode}) + '\n');
    });
  }
  fail(error) {
    if (this.dead) return;
    this.dead = true;
    for (const request of this.pending.values()) {
      clearTimeout(request.timer);
      request.reject(error);
    }
    this.pending.clear();
    this.lines.close();
    this.child.kill();
  }
  dispose() { this.fail(new Error('Syntax checker stopped')); }
}
module.exports = {Checker};
