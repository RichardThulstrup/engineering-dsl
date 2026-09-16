'use strict';
const vscode = require('vscode');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const log = value => fs.appendFileSync(process.env.EDSL_TEST_RESULT, JSON.stringify(value) + '\n');
async function until(predicate, label, timeout = 15000) {
  const start = Date.now();
  while (!predicate()) {
    if (Date.now() - start > timeout) throw new Error(`Timed out: ${label}`);
    await pause(100);
  }
}

exports.run = async () => {
  try {
    const notebook = await vscode.workspace.openNotebookDocument(vscode.Uri.file(process.env.EDSL_TEST_NOTEBOOK));
    await vscode.window.showNotebookDocument(notebook);
    const extension = vscode.extensions.getExtension('richardthulstrup.engineering-dsl');
    const api = await extension.activate();
    await api.syncLanguages(notebook);
    assert.ok(notebook.getCells().every(cell => cell.document.languageId === 'engineering-dsl'));
    log({phase: 'DSL cell language'});

    const errorIndex = notebook.cellCount;
    const add = new vscode.WorkspaceEdit();
    add.set(notebook.uri, [vscode.NotebookEdit.insertCells(errorIndex, [new vscode.NotebookCellData(
      vscode.NotebookCellKind.Code, '_ok = 5 kΩ\n_bad = (2 + )', 'engineering-dsl')])]);
    await vscode.workspace.applyEdit(add);
    const bad = notebook.cellAt(errorIndex).document;
    await until(() => vscode.languages.getDiagnostics(bad.uri).some(d => d.source === 'Engineering DSL'), 'real syntax diagnostic');
    const diagnostic = vscode.languages.getDiagnostics(bad.uri).find(d => d.source === 'Engineering DSL');
    assert.equal(diagnostic.range.start.line, 1);
    log({phase: 'real syntax diagnostic', message: diagnostic.message, line: diagnostic.range.start.line});

    const edit = new vscode.WorkspaceEdit();
    edit.replace(bad.uri, new vscode.Range(0, 0, bad.lineCount, 0), '_ok = 5 kΩ\n_fixed = 2 ≟ 2');
    await vscode.workspace.applyEdit(edit);
    await pause(1200);
    await api.check(notebook);
    await until(() => vscode.languages.getDiagnostics(bad.uri).length === 0, 'diagnostic clears after edit');
    log({phase: 'diagnostic clears'});

    // Kernel metadata takes precedence over stale language_info when switching
    // from DSL back to Python. No global Pylance configuration is changed.
    const originalMetadata = notebook.metadata;
    const asPython = new vscode.WorkspaceEdit();
    asPython.set(notebook.uri, [vscode.NotebookEdit.updateNotebookMetadata({
      ...originalMetadata, metadata: {...originalMetadata.metadata,
        kernelspec: {name: 'python3', display_name: 'Python 3', language: 'python'},
      },
    })]);
    await vscode.workspace.applyEdit(asPython);
    await api.syncLanguages(notebook);
    assert.ok(notebook.getCells().every(cell => cell.document.languageId === 'python'));
    const restore = new vscode.WorkspaceEdit();
    restore.set(notebook.uri, [vscode.NotebookEdit.updateNotebookMetadata(originalMetadata)]);
    await vscode.workspace.applyEdit(restore);
    await api.syncLanguages(notebook);
    log({phase: 'Python kernel language restored correctly'});

    const normal = await vscode.workspace.openTextDocument({language: 'python', content: '_ordinary = )\n'});
    await vscode.window.showTextDocument(normal);
    await until(() => vscode.languages.getDiagnostics(normal.uri).some(d => d.severity === 0), 'ordinary Python syntax error', 30000);
    log({phase: 'normal Python diagnostics retained', sources: vscode.languages.getDiagnostics(normal.uri).map(d => d.source)});

    await vscode.window.showNotebookDocument(notebook);
    await pause(1200);
    await api.check(notebook);
    for (const cell of notebook.getCells()) {
      const issues = vscode.languages.getDiagnostics(cell.document.uri);
      assert.equal(issues.length, 0, JSON.stringify(issues));
    }
    log({phase: 'valid DSL has no diagnostics'});
    const completions = await vscode.commands.executeCommand('vscode.executeCompletionItemProvider', bad.uri, new vscode.Position(0, 0));
    assert.ok(completions.items.some(item => item.insertText === '≡'));
    assert.ok(completions.items.some(item => item.insertText === '≟'));
    log({phase: 'glyph completions'});

    // Exercise the real Microsoft Jupyter controller, including its supported-
    // languages gate, rich outputs and execution order.
    await vscode.extensions.getExtension('ms-toolsai.jupyter').activate();
    let selected = false;
    for (let retry = 0; retry < 30 && !selected; retry++) {
      selected = await vscode.commands.executeCommand('notebook.selectKernel', {
        notebookEditor: vscode.window.activeNotebookEditor,
        id: process.env.EDSL_TEST_KERNEL_ID, extension: 'ms-toolsai.jupyter',
      });
      if (!selected) await pause(1000);
    }
    assert.ok(selected, 'test DSL kernel is discovered');
    await vscode.commands.executeCommand('notebook.execute', notebook.uri);
    await until(() => notebook.getCells().every(cell => cell.executionSummary?.success !== undefined), 'Jupyter execution', 75000);
    assert.ok(notebook.getCells().every(cell => cell.executionSummary.success === true));
    const mimes = notebook.getCells().flatMap(cell => cell.outputs.flatMap(output => output.items.map(item => item.mime)));
    assert.ok(mimes.includes('image/png'), 'matplotlib produces a rich plot');
    assert.equal(notebook.metadata.metadata.language_info.name, 'engineering-dsl');
    log({phase: 'Jupyter execution', cells: notebook.cellCount, mimes});

    await notebook.save();
    const saved = JSON.parse(fs.readFileSync(process.env.EDSL_TEST_NOTEBOOK, 'utf8'));
    assert.equal(saved.metadata.language_info.name, 'engineering-dsl');
    assert.equal(saved.metadata.kernelspec.language, 'engineering-dsl');
    log({phase: 'notebook serialization'});
    log({success: true});
  } catch (error) {
    log({error: String(error), stack: error.stack});
    throw error;
  }
};
