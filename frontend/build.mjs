import { build } from 'esbuild';
import { copyFile, mkdir, readdir, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const output = 'catalog/static/catalog';
const vendor = join(output, 'vendor');
const coreOutput = join(vendor, 'core');
const langOutput = join(vendor, 'lang');
await mkdir(coreOutput, { recursive: true });
await mkdir(langOutput, { recursive: true });

await build({
  entryPoints: ['frontend/scan.js'],
  bundle: true,
  minify: true,
  format: 'iife',
  outfile: join(output, 'scan.bundle.js'),
});
await build({
  entryPoints: ['frontend/navigation.js'],
  bundle: true,
  minify: true,
  format: 'iife',
  outfile: join(output, 'navigation.bundle.js'),
});
await copyFile('node_modules/tesseract.js/dist/worker.min.js', join(vendor, 'worker.min.js'));
for (const filename of await readdir('node_modules/tesseract.js-core')) {
  if (filename.startsWith('tesseract-core') && (filename.endsWith('.js') || filename.endsWith('.wasm'))) {
    await copyFile(join('node_modules/tesseract.js-core', filename), join(coreOutput, filename));
  }
}
const languageFile = join(langOutput, 'eng.traineddata.gz');
let languageReady = false;
try { languageReady = (await stat(languageFile)).size > 1000000; } catch (_) { /* Download below. */ }
if (!languageReady) {
  const language = await fetch('https://tessdata.projectnaptha.com/4.0.0_fast/eng.traineddata.gz');
  if (!language.ok) throw new Error(`OCR language download failed: ${language.status}`);
  await writeFile(languageFile, Buffer.from(await language.arrayBuffer()));
}
console.log('Navigation, scanner, and offline OCR assets built.');
