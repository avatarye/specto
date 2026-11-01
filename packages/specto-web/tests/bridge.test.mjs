// The bridge's drag out (#185), with the real bridge.js on a fake QWebChannel: a drag
// of the page's text selection (the browser's dragstart) goes to Python as `drag {text,
// html}` and the browser's own drag is cancelled (the shell starts one with the drag
// contract's mime data); an element dragged on its own, or a dragstart the page handled,
// stays the browser's. Pages that see a drag themselves (the Monaco json view) hand it over
// with Specto.startDrag. Run by pytest through packages/specto-app/tests/test_web_pages.py,
// or directly:  node --test packages/specto-web/tests/bridge.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

import { blankPage } from "./fakes/page.mjs";

const signal = () => ({ connect() {} });

async function bridged(selection) {
  const sent = [];
  const api = {
    configured: signal(), port: signal(), params: signal(), notify: signal(), callResult: signal(),
    ready() {},
    send(kind, json) { sent.push([kind, JSON.parse(json)]); },
    log() {}, jsError(msg) { sent.push(["jsError", msg]); },
  };
  const page = blankPage(["bridge.js"], {
    qt: { webChannelTransport: {} },
    QWebChannel: function (_t, cb) { cb({ objects: { specto: api } }); },
    getSelection: () => selection.current,
  });
  await page.window.Specto.connect();
  return { page, sent };
}

/** a selection of `text` holding the nodes in `inside` */
function selectionOf(text, inside) {
  return {
    toString: () => text,
    rangeCount: 1,
    getRangeAt: () => ({ cloneContents: () => ({ nodeType: 11, children: [] }) }),
    containsNode: (n) => inside.includes(n),
  };
}

test("bridge: dragging the text selection hands it to the shell and cancels the browser's drag", async () => {
  const words = { nodeType: 3 };
  const selection = { current: selectionOf("the selected words", [words]) };
  const { page, sent } = await bridged(selection);
  const ev = page.fireWindow("dragstart", { target: words });
  assert.equal(ev.defaultPrevented, true);
  const drags = sent.filter(([k]) => k === "drag");
  assert.equal(drags.length, 1);
  assert.equal(drags[0][1].text, "the selected words");
  assert.ok("html" in drags[0][1]);
});

test("bridge: an element dragged on its own, an empty selection or a handled dragstart stay the browser's", async () => {
  const words = { nodeType: 3 };
  const image = { nodeType: 1, tagName: "IMG" };
  const selection = { current: selectionOf("the selected words", [words]) };
  const { page, sent } = await bridged(selection);
  assert.equal(page.fireWindow("dragstart", { target: image }).defaultPrevented, false);
  const handled = page.fireWindow("dragstart", { target: words, defaultPrevented: true });
  assert.equal(handled.defaultPrevented, true);  // the page's own, untouched
  selection.current = selectionOf("   ", [words]);
  assert.equal(page.fireWindow("dragstart", { target: words }).defaultPrevented, false);
  selection.current = null;
  assert.equal(page.fireWindow("dragstart", { target: words }).defaultPrevented, false);
  assert.deepEqual(sent.filter(([k]) => k === "drag" || k === "jsError"), []);
});

test("bridge: Specto.startDrag hands a page's own drag over; no text, no drag", async () => {
  const { page, sent } = await bridged({ current: null });
  page.window.Specto.startDrag({ text: '"frame": 3' });
  page.window.Specto.startDrag({ text: "" });
  assert.deepEqual(sent.filter(([k]) => k === "drag"), [["drag", { text: '"frame": 3', html: null }]]);
});
