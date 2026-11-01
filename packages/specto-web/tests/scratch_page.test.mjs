// The scratch pad's page (#184): one page of markdown, rendered like the markdown view
// (the OUTLINE panel) or edited as source in Monaco, autosaved to the host. Boots from
// its own index.html under node (fakes/page.mjs) and is driven by the `page`
// notification Python sends. Run by pytest through
// packages/specto-app/tests/test_web_pages.py, or directly:
//     node --test packages/specto-web/tests/scratch_page.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

import { bootPage } from "./fakes/page.mjs";

const SHARED = ["icons.js", "step.js", "footer.js", "sidepanel.js"];

/* the renderer's contract (render.js): html with heading ids; links stay links */
const fakeMarkdown = {
  render(text) {
    const headings = [];
    const html = text.split("\n").filter((l) => l.trim()).map((line) => {
      const h = line.match(/^(#+) (.*)$/);
      if (h) {
        const id = h[2].toLowerCase().replace(/ /g, "-");
        headings.push({ level: h[1].length, text: h[2], id });
        return `<h${h[1].length} id="${id}">${h[2]}</h${h[1].length}>`;
      }
      return "<p>" + line.replace(/\[([^\]]*)\]\(([^)]*)\)/g, (_, t, href) => `<a href="${href}">${t}</a>`) + "</p>";
    }).join("");
    return { html, headings, frontmatter: null };
  },
  runMermaid: () => Promise.resolve(0),
};

function fakeMonaco() {
  const rec = { created: null, options: [], focused: 0, changes: [], cursors: [], blurs: [] };
  const model = {
    value: "",
    getValue() { return this.value; },
    setValue(v) { this.value = v; },
    getOffsetAt(pos) { return pos.offset; },
    onDidChangeContent(fn) { rec.changes.push(fn); },
  };
  let position = { lineNumber: 1, column: 1, offset: 0 };
  const editor = {
    getPosition: () => position,
    setPosition(p) { position = p; },
    onDidChangeCursorPosition(fn) { rec.cursors.push(fn); },
    onDidBlurEditorText(fn) { rec.blurs.push(fn); },
    addCommand() {},
    getOption: () => "on",
    updateOptions: (o) => rec.options.push(o),
    layout() {},
    focus() { rec.focused++; },
  };
  const monaco = {
    KeyCode: { Escape: 9 },
    editor: {
      EditorOption: { wordWrap: 1 },
      create(_el, opts) { rec.created = opts; return editor; },
      createModel() { return model; },
    },
  };
  /** the user types: the model changes, the caret moves */
  rec.type = (text, offset) => {
    model.value = text;
    position = { lineNumber: 1, column: 1, offset };
    rec.changes.forEach((fn) => fn());
    rec.cursors.forEach((fn) => fn());
  };
  return { monaco, rec, model };
}

const PAGE = {
  id: "p1",
  name: "lidar",
  url: "specto://items/scratch-sapo/p1/md",
  text: "# Drift\nthe lidar drifts\n\n[00042.jpg](../../runs/00042.jpg)\n\n[web](https://example.com)\n",
  rev: 1,
};

async function scratchPage() {
  const { monaco, rec, model } = fakeMonaco();
  const page = bootPage("scratch", [...SHARED, "scratch/main.js"], {
    SpectoMarkdown: fakeMarkdown,
    monaco,
    loadMonaco: () => Promise.resolve(monaco),
    defineSpectoTheme() {},
  });
  await new Promise((r) => setImmediate(r));
  page.fire("config", { params: {} });
  return { page, rec, model };
}

test("scratch: a page renders like a markdown note, its outline on the right", async () => {
  const { page } = await scratchPage();
  assert.ok(!page.$("empty").classList.contains("hidden"), "nothing yet: the drop hint");
  page.fire("page", PAGE);
  assert.ok(page.$("empty").classList.contains("hidden"));
  assert.ok(!page.$("doc").classList.contains("hidden"));
  assert.equal(page.$("footerName").textContent, "lidar");
  const rows = page.$("outline").querySelectorAll(".outline-row");
  assert.deepEqual(rows.map((r) => r.textContent), ["Drift"]);
  assert.equal(page.$("toggleSource").getAttribute("data-icon"), "edit");
  assert.ok(page.$("editor").classList.contains("hidden"));
  assert.equal(page.lastReport.page, "p1");
  assert.equal(page.lastReport.source, false);
});

test("scratch: a file link asks the host to open it; a web link does nothing", async () => {
  const { page } = await scratchPage();
  page.fire("page", PAGE);
  const links = page.$("doc").querySelectorAll("a");
  links[0].click();
  links[1].click();
  assert.deepEqual(page.sent.filter((s) => s[0] === "open"), [["open", { href: "../../runs/00042.jpg" }]]);
});

test("scratch: the source editor autosaves, reports its caret, and the toggle goes back", async () => {
  const { page, rec, model } = await scratchPage();
  page.fire("page", PAGE);
  page.$("toggleSource").click();
  assert.ok(!page.$("editor").classList.contains("hidden"));
  assert.ok(page.$("doc").classList.contains("hidden"));
  assert.equal(model.value, PAGE.text, "the editor holds the page's markdown");
  assert.ok(rec.focused > 0);
  assert.equal(page.$("toggleSource").getAttribute("data-icon"), "eye");
  rec.type("# Drift\nedited\n", 9);
  assert.deepEqual(page.sent.filter((s) => s[0] === "caret").pop(), ["caret", { page: "p1", offset: 9 }]);
  assert.equal(page.sent.filter((s) => s[0] === "save").length, 0, "debounced");
  page.flush();
  assert.deepEqual(page.sent.filter((s) => s[0] === "save"), [["save", { page: "p1", text: "# Drift\nedited\n", rev: 1 }]]);
  // the host echoes the saved text: the editor keeps it, nothing more is sent
  page.fire("page", { ...PAGE, text: "# Drift\nedited\n", rev: 2 });
  assert.equal(model.value, "# Drift\nedited\n");
  // back to the rendered view: no caret (drops append at the end)
  page.$("toggleSource").click();
  assert.deepEqual(page.sent.filter((s) => s[0] === "caret").pop(), ["caret", { page: "p1", offset: null }]);
  assert.ok(page.$("editor").classList.contains("hidden"));
});

test("scratch: unsaved typing is saved before the page switches", async () => {
  const { page, rec } = await scratchPage();
  page.fire("page", PAGE);
  page.$("toggleSource").click();
  rec.type("half a thought", 4);
  page.fire("page", { ...PAGE, id: "p2", name: "other", text: "other page\n" });
  assert.deepEqual(page.sent.filter((s) => s[0] === "save").pop(), ["save", { page: "p1", text: "half a thought", rev: 1 }]);
});

test("scratch: Ctrl+V in the rendered view pastes through the host", async () => {
  const { page } = await scratchPage();
  page.fire("page", PAGE);
  const key = (k, ctrlKey) => {
    const ev = { type: "keydown", key: k, ctrlKey, altKey: false, metaKey: false, defaultPrevented: false };
    ev.preventDefault = () => { ev.defaultPrevented = true; };
    (page.document._listeners().keydown || []).forEach((fn) => fn(ev));
    return ev;
  };
  assert.ok(key("v", true).defaultPrevented);
  assert.deepEqual(page.sent.filter((s) => s[0] === "paste"), [["paste", {}]]);
  // text pasted in the source editor is Monaco's own; a picture goes through the host
  assert.ok(key("e", false).defaultPrevented, "E opens the source");
  assert.ok(!page.$("editor").classList.contains("hidden"));
  const paste = (kind) => {
    const ev = { type: "paste", clipboardData: { items: [{ kind }] }, defaultPrevented: false };
    ev.preventDefault = () => { ev.defaultPrevented = true; };
    (page.document._listeners().paste || []).forEach((fn) => fn(ev));
    return ev;
  };
  assert.ok(!paste("string").defaultPrevented);
  assert.ok(paste("file").defaultPrevented);
  assert.equal(page.sent.filter((s) => s[0] === "paste").length, 2);
});
