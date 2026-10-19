import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, join } from 'node:path';
import { ApplicationError, commandMain, invokeCLI, privateDirectory } from './adapter-runtime.mjs';
const root = fileURLToPath(new URL('../', import.meta.url));
const cases = new Set(JSON.parse(readFileSync(new URL('../data/manifest.json', import.meta.url))).cases.map(item => item.id));
export async function runApplication(input, parameters, signal, invocation) {
  if (Object.keys(input).some(key => !['caseId', 'action'].includes(key)) || !cases.has(input.caseId) || !['process', 'inspect'].includes(input.action)) throw new Error('Select a reviewed case and process/inspect action; approval and posting are operator actions.');
  const directory = await privateDirectory(root, invocation), db = join(directory, 'payables.db');
  const cli = ['uv', 'run', '--locked', '--no-sync', 'payables', '--db', db];
  try {
    await invokeCLI([...cli, 'init', '--case', input.caseId], { cwd: root, signal });
    const argv = input.action === 'inspect' ? [...cli, 'snapshot'] : [...cli, 'process', '--case', input.caseId, '--output-root', join(directory, 'receipts')];
    if (input.action === 'process' && parameters.model) argv.push('--model', parameters.model);
    return { executionKind: input.action === 'inspect' ? 'application_state_inspection' : 'native_source_extraction',
      application: await invokeCLI(argv, { cwd: root, signal }), localState: { directory, databaseFile: db } };
  } catch (error) { if (error instanceof ApplicationError) error.evidence.runDirectory = directory; throw error; }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await commandMain(runApplication);
