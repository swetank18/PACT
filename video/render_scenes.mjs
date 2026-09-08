/**
 * Films the motion scenes in the same browser that films the product.
 *
 * Each scene page holds every animation at its first frame until this script
 * plays them, and the moment it does is written to scene-marks.json. The
 * compositor cuts from there, so a scene's cues sit exactly where the timeline
 * says regardless of how long Chromium took to start.
 *
 *   node render_scenes.mjs ../video
 */
import { createRequire } from "node:module";

// Resolved against console/package.json rather than by bare specifier, because
// Node resolves a bare import from the *importing file's* directory: video/ has
// no node_modules and the repository root's is empty, so
// `node ../video/record_walkthrough.mjs` — the command README_VIDEO.md
// documents — died with ERR_MODULE_NOT_FOUND no matter which directory it was
// run from. This way the three recorder scripts run from anywhere.
const { chromium } = createRequire(new URL("../console/package.json", import.meta.url))(
  "@playwright/test",
);
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(process.argv[2] ?? ".");
const timeline = JSON.parse(readFileSync(`${root}/script/timeline.json`, "utf8"));
const out = `${root}/recording/scenes`;
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({
  args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--force-device-scale-factor=1"],
});

// Optional scene ids, so one scene can be re-filmed after a change to it
// without spending five minutes on the ten that did not move. Existing marks
// are read back and merged rather than replaced, which is the whole point —
// compose.py needs a mark for every motion scene, not just the ones filmed most
// recently.
const only = new Set(process.argv.slice(3));
const marksFile = `${out}/scene-marks.json`;
let marks = {};
if (only.size) {
  try {
    marks = JSON.parse(readFileSync(marksFile, "utf8"));
  } catch {
    console.warn("no existing scene-marks.json; filming every motion scene instead");
    only.clear();
  }
}

for (const scene of timeline.scenes.filter(
  (s) => s.kind === "motion" && (!only.size || only.has(s.id)),
)) {
  const dir = `${out}/${scene.id}`;
  mkdirSync(dir, { recursive: true });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    recordVideo: { dir, size: { width: 1920, height: 1080 } },
    deviceScaleFactor: 1,
  });
  await context.addInitScript(() => (window.__HOLD__ = true));
  const page = await context.newPage();

  const t0 = Date.now();
  await page.goto(`file://${root}/assets/scenes/${scene.id}.html`, { waitUntil: "load" });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(700);                       // a beat of the rest state

  const startedAt = (Date.now() - t0) / 1000;
  await page.evaluate(() => document.getAnimations().forEach((a) => a.play()));
  await page.waitForTimeout(scene.duration * 1000 + 500);

  await context.close();
  marks[scene.id] = { started: startedAt, duration: scene.duration };
  console.log(`${scene.id}  played at ${startedAt.toFixed(2)}s, held ${scene.duration.toFixed(2)}s`);
}

writeFileSync(marksFile, JSON.stringify(marks, null, 2));
await browser.close();
