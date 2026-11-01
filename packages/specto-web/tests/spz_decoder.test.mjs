// The vendored SPZ decoder (web/vendor/babylon/spz.js, @adobe/spz: what the 3D page hands
// Babylon's SPLAT loader as `spzLibraryUrl`) decodes a v4 (NGSP) file under node, with no
// network: the format Babylon's own fallback refuses (#66). Skipped until build.py has
// vendored the library. Run by pytest through packages/specto-app/tests/test_scene3d_page.py,
// or directly:
//     node --test packages/specto-web/tests/spz_decoder.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SPZ_JS = path.join(HERE, "..", "web", "vendor", "babylon", "spz.js");
// three splats at (0,0,0), (1,2,3), (-4,0.5,2), packed as v4 by the same library once
const FIXTURE = path.join(HERE, "fixtures", "tiny_v4.spz");

test("the vendored decoder reads a v4 NGSP splat file offline", { skip: existsSync(SPZ_JS) ? false : "run packages/specto-web/build.py" }, async () => {
  const bytes = new Uint8Array(readFileSync(FIXTURE));
  assert.equal(String.fromCharCode(...bytes.slice(0, 4)), "NGSP", "the fixture is a v4+ file");
  assert.equal(bytes[4], 4, "header version");

  const createSpzModule = (await import(pathToFileURL(SPZ_JS).href)).default;
  const spz = await createSpzModule();
  assert.equal(spz.LATEST_SPZ_HEADER_VERSION, 4);
  const cloud = spz.loadSpzFromBuffer(bytes, { to: spz.CoordinateSystem.RUB });
  assert.equal(cloud.numPoints, 3);
  assert.equal(cloud.shDegree, 0);
  const want = [0, 0, 0, 1, 2, 3, -4, 0.5, 2];
  const got = Array.from(cloud.positions);
  assert.equal(got.length, want.length);
  // positions are stored with 12 fractional bits
  got.forEach((v, i) => assert.ok(Math.abs(v - want[i]) < 1 / 2048, `position[${i}] ${v} vs ${want[i]}`));
});
