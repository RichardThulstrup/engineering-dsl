'use strict';
const vscode = require('vscode');
const fs = require('node:fs');
const path = require('node:path');
const {execFile} = require('node:child_process');
const {promisify} = require('node:util');
const {Checker} = require('./client');
const {registeredKernel} = require('./kernel-environment');
const exec = promisify(execFile);
const LANGUAGE = 'engineering-dsl';
const metadata = notebook => notebook.metadata.metadata || {};
const isDSL = notebook => {
  const meta = metadata(notebook);
  return (meta.kernelspec?.language || meta.language_info?.name) === LANGUAGE;
};
const codeCells = notebook => notebook.getCells().filter(cell =>
  cell.kind === vscode.NotebookCellKind.Code && cell.document.languageId !== 'raw');

function activate(context) {
  const diagnostics = vscode.languages.createDiagnosticCollection(LANGUAGE);
  const output = vscode.window.createOutputChannel('Engineering DSL');
  const states = new Map(), workers = new Map();
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 40);
  status.command = 'engineeringDsl.check';
  context.subscriptions.push(diagnostics, output, status);

  function showStatus() {
    const notebook = vscode.window.activeNotebookEditor?.notebook;
    if (!notebook || !isDSL(notebook)) { status.hide(); return; }
    const state = states.get(notebook);
    if (!vscode.workspace.isTrusted) {
      status.text = '$(lock) DSL';
      status.tooltip = 'Trust the workspace to enable DSL syntax checking.';
      status.show();
      return;
    }
    if (!vscode.workspace.getConfiguration('engineeringDsl', notebook.uri).get('diagnostics', true)) {
      status.text = 'DSL';
      status.tooltip = 'DSL syntax checking is disabled in settings.';
      status.show();
      return;
    }
    status.text = state?.error ? '$(warning) DSL checker' : state?.busy ? '$(sync~spin) DSL' : '$(check) DSL';
    status.tooltip = state?.error
      ? `${state.error}\nSet Engineering DSL: Python Path to the kernel's interpreter, then check again.`
      : 'Engineering DSL syntax checking. Click to recheck; cells are not executed.';
    status.show();
  }

  async function environment(notebook, preferKernel = true) {
    const config = vscode.workspace.getConfiguration('engineeringDsl', notebook.uri);
    const registered = preferKernel && isDSL(notebook) ? registeredKernel() : undefined;
    let python = config.get('pythonPath') || registered?.python;
    if (!python) {
      const extension = vscode.extensions.getExtension('ms-python.python');
      const api = extension && await extension.activate();
      const selected = api?.environments?.getActiveEnvironmentPath(notebook.uri);
      const resolved = selected && await api.environments.resolveEnvironment(selected);
      python = resolved?.executable?.uri?.fsPath || selected?.path || 'python';
    }
    let root = config.get('runtimePath');
    if (!root) {
      let directory = notebook.uri.scheme === 'file' ? path.dirname(notebook.uri.fsPath)
        : vscode.workspace.getWorkspaceFolder(notebook.uri)?.uri.fsPath;
      while (directory) {
        if (fs.existsSync(path.join(directory, 'utils', 'circuit_dsl.py'))) { root = directory; break; }
        const parent = path.dirname(directory);
        if (parent === directory) break;
        directory = parent;
      }
    }
    return {python, root: root || registered?.root || '', config};
  }

  function getWorker(python, root) {
    const key = JSON.stringify([python, root]);
    if (!workers.has(key) || workers.get(key).dead) {
      workers.set(key, new Checker(python, context.asAbsolutePath('python/checker.py'), root,
        message => output.append(message)));
    }
    return workers.get(key);
  }

  async function check(notebook) {
    if (!vscode.workspace.isTrusted || notebook.isClosed || !isDSL(notebook)) return;
    let state = states.get(notebook);
    if (!state) { state = {generation: 0}; states.set(notebook, state); }
    if (state.busy) {
      await state.done;
      if (state.results && state.completedGeneration === state.generation) return;
      return check(notebook);
    }
    let finished;
    state.done = new Promise(resolve => { finished = resolve; });
    const generation = state.generation;
    state.busy = true; state.error = undefined;
    showStatus();
    const cells = codeCells(notebook);
    const versions = cells.map(cell => cell.document.version);
    try {
      const {python, root, config} = await environment(notebook);
      if (!config.get('diagnostics', true)) {
        for (const cell of cells) diagnostics.delete(cell.document.uri);
        return;
      }
      const sources = cells.map(cell => cell.document.getText());
      if (sources.reduce((size, source) => size + source.length, 0) > 1000000) {
        throw new Error('Notebook exceeds the 1 MB live syntax-check limit');
      }
      const result = await getWorker(python, root).check(sources, config.get('syntaxMode', 'python'));
      if (notebook.isClosed || !isDSL(notebook) || generation !== state.generation
        || cells.some((cell, index) => cell.document.version !== versions[index])) return;
      state.results = result;
      state.completedGeneration = generation;
      state.cells = cells;
      for (const [index, cell] of cells.entries()) {
        diagnostics.set(cell.document.uri, result[index].diagnostics.map(issue => {
          const {start, end} = issue.range;
          const range = cell.document.validateRange(new vscode.Range(start.line, start.character, end.line, end.character));
          const diagnostic = new vscode.Diagnostic(range, issue.message, vscode.DiagnosticSeverity.Error);
          diagnostic.source = 'Engineering DSL';
          return diagnostic;
        }));
      }
    } catch (error) {
      state.error = String(error.message || error);
      output.appendLine(state.error);
      for (const cell of cells) diagnostics.delete(cell.document.uri);
    } finally {
      state.busy = false;
      finished();
      showStatus();
    }
  }

  function schedule(notebook) {
    if (notebook.notebookType !== 'jupyter-notebook') return;
    let state = states.get(notebook);
    if (!state) { state = {generation: 0}; states.set(notebook, state); }
    state.generation++;
    state.results = undefined;
    clearTimeout(state.timer);
    for (const cell of codeCells(notebook)) diagnostics.delete(cell.document.uri);
    state.timer = setTimeout(() => check(notebook), 400);
  }

  async function syncLanguages(notebook) {
    if (notebook.notebookType !== 'jupyter-notebook' || notebook.isClosed) return;
    const dsl = isDSL(notebook);
    // Only a DSL kernel opts into this language. A Python kernel's supported
    // languages do not include engineering-dsl, so never relabel it on import.
    for (const cell of codeCells(notebook)) {
      if (dsl && cell.document.languageId !== LANGUAGE) {
        await vscode.languages.setTextDocumentLanguage(cell.document, LANGUAGE);
      } else if (!dsl && cell.document.languageId === LANGUAGE) {
        await vscode.languages.setTextDocumentLanguage(cell.document, metadata(notebook).kernelspec?.language || 'python');
        diagnostics.delete(cell.document.uri);
      }
    }
    schedule(notebook);
    showStatus();
  }
  const sync = notebook => syncLanguages(notebook).catch(error => output.appendLine(String(error)));
  const active = () => vscode.window.activeNotebookEditor?.notebook;
  const requireTrust = () => {
    if (!vscode.workspace.isTrusted) throw new Error('Trust this workspace before starting the DSL checker or installing a kernel.');
  };
  function command(name, handler) {
    context.subscriptions.push(vscode.commands.registerCommand(name, async () => {
      try { return await handler(); }
      catch (error) { vscode.window.showErrorMessage(`Engineering DSL: ${error.message || error}`); }
    }));
  }
  command('engineeringDsl.selectKernel', async () => {
    if (active()) return vscode.commands.executeCommand('notebook.selectKernel', {notebookEditor: vscode.window.activeNotebookEditor});
    vscode.window.showInformationMessage('Open a notebook, then select Engineering DSL in its kernel picker.');
  });
  command('engineeringDsl.installKernel', async () => {
    requireTrust();
    const notebook = active();
    if (!notebook) throw new Error('Open a notebook first so its Python environment can be selected.');
    const {python, root} = await environment(notebook, false);
    await vscode.window.withProgress({location: vscode.ProgressLocation.Notification,
      title: 'Registering the Engineering DSL kernel'}, () => exec(python,
      ['-B', '-m', 'utils.vscode_kernel', '--install', '--user'], {
        cwd: root || undefined, windowsHide: true, timeout: 30000,
        env: {...process.env, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1'},
      }));
    const choice = await vscode.window.showInformationMessage(
      'Engineering DSL kernel installed. Select it under Select Another Kernel → Jupyter Kernel. Keep the Engineer import in your first cell.',
      'Select Kernel');
    if (choice) return vscode.commands.executeCommand('engineeringDsl.selectKernel');
  });
  command('engineeringDsl.check', async () => {
    requireTrust();
    const notebook = active();
    if (!notebook || !isDSL(notebook)) throw new Error('Select the Engineering DSL notebook kernel first.');
    await check(notebook);
    const error = states.get(notebook)?.error;
    if (error) throw new Error(error);
  });
  command('engineeringDsl.showPython', async () => {
    requireTrust();
    const notebook = active();
    if (!notebook || !isDSL(notebook)) throw new Error('Select the Engineering DSL notebook kernel first.');
    await check(notebook);
    const state = states.get(notebook);
    const cell = notebook.cellAt(vscode.window.activeNotebookEditor.selection.start);
    const index = state?.cells?.indexOf(cell);
    const source = state?.results?.[index]?.python;
    if (source === undefined) throw new Error(state?.error || 'Select a code cell and wait for its syntax check.');
    // A read-only virtual document avoids saving generated Python accidentally.
    const uri = vscode.Uri.parse(`engineering-dsl-preview:/${encodeURIComponent(notebook.uri.toString())}/cell-${cell.index + 1}.py`);
    previews.set(uri.toString(), source);
    previewChanged.fire(uri);
    await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(uri), {preview: true, viewColumn: vscode.ViewColumn.Beside});
  });

  const previews = new Map();
  const previewChanged = new vscode.EventEmitter();
  context.subscriptions.push(previewChanged, vscode.workspace.registerTextDocumentContentProvider('engineering-dsl-preview', {
    onDidChange: previewChanged.event, provideTextDocumentContent: uri => previews.get(uri.toString()) || '',
  }));
  const symbols = require('../symbols.json');
  context.subscriptions.push(vscode.languages.registerCompletionItemProvider({language: LANGUAGE, scheme: 'vscode-notebook-cell'}, {
    provideCompletionItems: () => symbols.map(symbol => {
      const item = new vscode.CompletionItem(symbol.name, vscode.CompletionItemKind.Operator);
      item.insertText = symbol.text; item.detail = symbol.description;
      item.filterText = `${symbol.name} ${symbol.text}`;
      return item;
    }),
  }));

  context.subscriptions.push(
    vscode.workspace.onDidOpenNotebookDocument(sync),
    vscode.workspace.onDidCloseTextDocument(document => {
      if (document.uri.scheme === 'vscode-notebook-cell') diagnostics.delete(document.uri);
      if (document.uri.scheme === 'engineering-dsl-preview') previews.delete(document.uri.toString());
    }),
    vscode.workspace.onDidChangeNotebookDocument(event => {
      if (event.metadata || event.contentChanges.length) sync(event.notebook);
      else if (event.cellChanges.some(change => change.document)) schedule(event.notebook);
    }),
    vscode.workspace.onDidCloseNotebookDocument(notebook => {
      clearTimeout(states.get(notebook)?.timer);
      states.delete(notebook);
      for (const cell of notebook.getCells()) diagnostics.delete(cell.document.uri);
      if (![...states.keys()].some(isDSL)) {
        for (const worker of workers.values()) worker.dispose();
        workers.clear();
      }
      showStatus();
    }),
    vscode.workspace.onDidChangeConfiguration(event => {
      if (event.affectsConfiguration('engineeringDsl') || event.affectsConfiguration('python')) {
        for (const worker of workers.values()) worker.dispose();
        workers.clear();
        for (const notebook of vscode.workspace.notebookDocuments) schedule(notebook);
      }
    }),
    vscode.workspace.onDidGrantWorkspaceTrust(() => vscode.workspace.notebookDocuments.forEach(schedule)),
    vscode.window.onDidChangeActiveNotebookEditor(showStatus),
    {dispose() {
      for (const state of states.values()) clearTimeout(state.timer);
      for (const worker of workers.values()) worker.dispose();
      states.clear(); workers.clear(); previews.clear();
    }},
  );
  vscode.workspace.notebookDocuments.forEach(sync);
  // Public API used by extension-host tests and other editor integrations.
  return {check, syncLanguages, isDSL};
}
module.exports = {activate};
