import { build } from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');

const result = await build({
  entryPoints: [path.join(__dirname, 'src/main.ts')],
  bundle: true,
  minify: true,
  format: 'iife',
  target: ['es2020'],
  write: false,
});

const jsCode = result.outputFiles[0].text;
const template = fs.readFileSync(path.join(__dirname, 'src/template.html'), 'utf8');
const finalHtml = template.replace(
  '<!-- INLINE_SCRIPT -->',
  () => `<script>\n${jsCode}\n</script>`
);

const outPath = path.join(repoRoot, 'core', 'dashboard.html');
fs.writeFileSync(outPath, finalHtml, 'utf8');
console.log(`Built ${outPath} (${(Buffer.byteLength(finalHtml, 'utf8') / 1024).toFixed(1)} KiB)`);
