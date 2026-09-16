'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// Read the user kernelspec installed by utils.vscode_kernel. This keeps the
// checker on the same interpreter even when VS Code's Python selection differs.
// Environment kernels and remote servers can use the explicit pythonPath setting.
function registeredKernel(env = process.env, home = os.homedir(), platform = process.platform) {
  const roots = (env.JUPYTER_PATH || '').split(path.delimiter).filter(Boolean);
  if (env.JUPYTER_DATA_DIR) roots.push(env.JUPYTER_DATA_DIR);
  if (platform === 'win32' && env.APPDATA) roots.push(path.join(env.APPDATA, 'jupyter'));
  else if (platform === 'darwin') roots.push(path.join(home, 'Library', 'Jupyter'));
  else roots.push(path.join(env.XDG_DATA_HOME || path.join(home, '.local', 'share'), 'jupyter'));
  for (const root of roots) {
    try {
      const spec = JSON.parse(fs.readFileSync(path.join(root, 'kernels', 'engineering-dsl', 'kernel.json'), 'utf8'));
      const [python, script, flag, connection] = spec.argv || [];
      if (spec.language !== 'engineering-dsl' || !path.isAbsolute(python || '')
        || !path.isAbsolute(script || '') || path.basename(script) !== 'vscode_kernel.py'
        || flag !== '-f' || connection !== '{connection_file}'
        || !fs.existsSync(python) || !fs.existsSync(script)) continue;
      return {python, root: path.dirname(path.dirname(script))};
    } catch { /* Missing or unrelated kernelspec: use the Python extension. */ }
  }
  return undefined;
}
module.exports = {registeredKernel};
