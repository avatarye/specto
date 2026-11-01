/* Markdown rendering for the markdown view: markdown-it plus small inline plugins for
 * what Obsidian notes use (frontmatter, [[wikilinks]] and ![[embeds]], > [!callouts],
 * $math$ via KaTeX, ```mermaid fences, task lists, heading ids for the outline) and the
 * rewrite of relative image paths onto the note's specto://items URL.
 *
 *   SpectoMarkdown.render(text, ctx) -> {html, headings: [{level, text, id}], frontmatter}
 *   ctx = {noteUrl: "specto://items/c1/key/md", resolve: function (target) -> key | null}
 *
 * No network, no eval; everything comes from /vendor. The host page runs
 * SpectoMarkdown.runMermaid(root) after inserting the HTML. */
(function () {
  "use strict";

  const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|bmp)$/i;

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function slugify(text, seen) {
    let base = String(text).toLowerCase().trim().replace(/[^\wÀ-￿\s-]/g, "").replace(/\s+/g, "-") || "section";
    let id = base, n = 2;
    while (seen[id]) { id = base + "-" + n++; }
    seen[id] = true;
    return id;
  }

  function isRelative(src) {
    return !/^[a-z][a-z0-9+.-]*:/i.test(src) && src.charAt(0) !== "/" && src.charAt(0) !== "#";
  }

  function relUrl(ctx, src) {
    if (!ctx.noteUrl || !isRelative(src)) return src;
    const clean = src.split("/").filter(function (s) { return s && s !== "."; }).map(encodeURIComponent).join("/");
    return ctx.noteUrl + "/" + clean;
  }

  /* ---- frontmatter ------------------------------------------------------------------ */
  function splitFrontmatter(text) {
    if (!/^---[ \t]*\r?\n/.test(text)) return { body: text, frontmatter: null };
    const m = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(text);
    if (!m) return { body: text, frontmatter: null };
    const fm = {};
    m[1].split(/\r?\n/).forEach(function (line) {
      const kv = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
      if (kv) fm[kv[1]] = kv[2];
    });
    return { body: text.slice(m[0].length), frontmatter: fm };
  }

  /* ---- wikilinks ---------------------------------------------------------------------- */
  function parseWikilink(inner) {
    let alias = null, anchor = null, target = inner;
    const bar = inner.indexOf("|");
    if (bar >= 0) { alias = inner.slice(bar + 1).trim(); target = inner.slice(0, bar); }
    const hash = target.indexOf("#");
    if (hash >= 0) { anchor = target.slice(hash + 1).trim(); target = target.slice(0, hash); }
    return { target: target.trim(), anchor: anchor, alias: alias };
  }

  function wikilinkPlugin(md, ctx) {
    md.inline.ruler.before("link", "wikilink", function (state, silent) {
      const src = state.src, pos = state.pos;
      let start = pos, embed = false;
      if (src.charCodeAt(pos) === 0x21 && src.startsWith("[[", pos + 1)) { embed = true; start = pos + 1; }
      else if (!src.startsWith("[[", pos)) return false;
      const end = src.indexOf("]]", start + 2);
      if (end < 0) return false;
      const inner = src.slice(start + 2, end);
      if (!inner || inner.indexOf("\n") >= 0 || inner.indexOf("[[") >= 0) return false;
      if (!silent) {
        const token = state.push("wikilink", "", 0);
        token.content = inner;
        token.meta = { embed: embed };
      }
      state.pos = end + 2;
      return true;
    });
    md.renderer.rules.wikilink = function (tokens, idx) {
      const t = tokens[idx];
      const link = parseWikilink(t.content);
      if (t.meta.embed && IMAGE_EXT.test(link.target)) {
        return '<img src="' + escapeHtml(relUrl(ctx, link.target)) + '" alt="' + escapeHtml(link.alias || link.target) + '">';
      }
      const label = link.alias || (link.target ? link.target + (link.anchor ? " > " + link.anchor : "") : "#" + link.anchor);
      const key = link.target ? ctx.resolve(link.target) : "";
      if (key === null || key === undefined) {
        return '<span class="wikilink missing" title="no note named ' + escapeHtml(link.target) + ' in this collection">' + escapeHtml(label) + "</span>";
      }
      const anchorId = link.anchor ? slugify(link.anchor, {}) : "";
      return '<a class="wikilink" href="#" data-key="' + escapeHtml(key) + '" data-anchor="' + escapeHtml(anchorId) + '" title="' + escapeHtml(t.content) + '">' + escapeHtml(label) + "</a>";
    };
  }

  /* ---- callouts: > [!type]±? Title ----------------------------------------------------- */
  function calloutPlugin(md) {
    const RE = /^\[!([A-Za-z0-9_-]+)\]([+-]?)[ \t]*(.*)$/;
    md.core.ruler.after("block", "callouts", function (state) {
      const toks = state.tokens;
      for (let i = 0; i < toks.length; i++) {
        if (toks[i].type !== "blockquote_open") continue;
        const p = toks[i + 1], inl = toks[i + 2];
        if (!p || p.type !== "paragraph_open" || !inl || inl.type !== "inline") continue;
        const lines = inl.content.split("\n");
        const m = RE.exec(lines[0]);
        if (!m) continue;
        const type = m[1].toLowerCase(), fold = m[2], title = m[3] || (type.charAt(0).toUpperCase() + type.slice(1));
        const open = toks[i];
        open.tag = "div";
        open.attrSet("class", "callout callout-" + type + (fold ? " foldable" : "") + (fold === "-" ? " folded" : ""));
        // the matching close
        let depth = 0, j = i + 1;
        for (; j < toks.length; j++) {
          if (toks[j].type === "blockquote_open") depth++;
          if (toks[j].type === "blockquote_close") { if (depth === 0) break; depth--; }
        }
        if (j < toks.length) toks[j].tag = "div";
        const head = new state.Token("html_block", "", 0);
        head.block = true;
        head.content = '<div class="callout-title">' + md.renderInline(title) + '</div><div class="callout-body">\n';
        const tail = new state.Token("html_block", "", 0);
        tail.block = true;
        tail.content = "</div>\n";
        toks.splice(j, 0, tail);
        if (lines.length > 1) {
          inl.content = lines.slice(1).join("\n");
          toks.splice(i + 1, 0, head);
        } else {
          toks.splice(i + 1, 3, head); // title-only paragraph goes away
        }
      }
    });
  }

  /* ---- math: $inline$ and $$display$$ -------------------------------------------------- */
  function katexRender(tex, display) {
    if (typeof katex === "undefined") return '<code class="math">' + escapeHtml(tex) + "</code>";
    try {
      return katex.renderToString(tex, { displayMode: display, throwOnError: false, output: "html" });
    } catch (e) {
      return '<span class="mermaid-error">' + escapeHtml(e.message) + "</span>";
    }
  }

  function mathPlugin(md) {
    md.inline.ruler.after("escape", "math_inline", function (state, silent) {
      const src = state.src, pos = state.pos;
      if (src.charCodeAt(pos) !== 0x24 /* $ */) return false;
      if (src.charCodeAt(pos + 1) === 0x24) return false; // $$ is block-level
      const next = src.charCodeAt(pos + 1);
      if (next === 0x20 || next === 0x09 || next === 0x0A) return false;
      let end = pos + 1;
      for (;;) {
        end = src.indexOf("$", end);
        if (end < 0 || end >= state.posMax) return false;
        if (src.charCodeAt(end - 1) === 0x5C) { end++; continue; } // escaped
        break;
      }
      const prev = src.charCodeAt(end - 1);
      if (prev === 0x20 || prev === 0x09) return false;
      const after = src.charCodeAt(end + 1);
      if (after >= 0x30 && after <= 0x39) return false; // $5 and $10: money, not math
      if (!silent) {
        const token = state.push("math_inline", "", 0);
        token.content = src.slice(pos + 1, end);
      }
      state.pos = end + 1;
      return true;
    });
    md.block.ruler.before("fence", "math_block", function (state, startLine, endLine, silent) {
      let pos = state.bMarks[startLine] + state.tShift[startLine];
      let max = state.eMarks[startLine];
      if (pos + 2 > max || state.src.slice(pos, pos + 2) !== "$$") return false;
      if (silent) return true;
      pos += 2;
      let first = state.src.slice(pos, max);
      let content = "", found = false, nextLine = startLine;
      const closeIdx = first.indexOf("$$");
      if (closeIdx >= 0) { content = first.slice(0, closeIdx); found = true; nextLine = startLine + 1; }
      else {
        content = first ? first + "\n" : "";
        for (nextLine = startLine + 1; nextLine < endLine; nextLine++) {
          const s = state.bMarks[nextLine] + state.tShift[nextLine], e = state.eMarks[nextLine];
          const line = state.src.slice(s, e);
          const ci = line.indexOf("$$");
          if (ci >= 0) { content += line.slice(0, ci); found = true; nextLine++; break; }
          content += line + "\n";
        }
      }
      if (!found) return false;
      const token = state.push("math_block", "", 0);
      token.block = true;
      token.content = content.trim();
      token.map = [startLine, nextLine];
      state.line = nextLine;
      return true;
    }, { alt: ["paragraph", "reference", "blockquote", "list"] });
    md.renderer.rules.math_inline = function (tokens, idx) { return katexRender(tokens[idx].content, false); };
    md.renderer.rules.math_block = function (tokens, idx) { return katexRender(tokens[idx].content, true) + "\n"; };
  }

  /* ---- fences: mermaid --------------------------------------------------------------- */
  function fencePlugin(md) {
    const base = md.renderer.rules.fence;
    md.renderer.rules.fence = function (tokens, idx, options, env, self) {
      const info = (tokens[idx].info || "").trim().split(/\s+/)[0];
      if (info === "mermaid") {
        return '<pre class="mermaid">' + escapeHtml(tokens[idx].content) + "</pre>\n";
      }
      return base(tokens, idx, options, env, self);
    };
  }

  /* ---- task lists --------------------------------------------------------------------- */
  function taskListPlugin(md) {
    md.core.ruler.after("inline", "task_lists", function (state) {
      const toks = state.tokens;
      for (let i = 2; i < toks.length; i++) {
        const t = toks[i];
        if (t.type !== "inline" || !t.children || !t.children.length) continue;
        if (toks[i - 1].type !== "paragraph_open" || toks[i - 2].type !== "list_item_open") continue;
        const first = t.children[0];
        if (first.type !== "text") continue;
        const m = /^\[([ xX])\]\s+/.exec(first.content);
        if (!m) continue;
        first.content = first.content.slice(m[0].length);
        const box = new state.Token("html_inline", "", 0);
        box.content = '<input type="checkbox" disabled' + (m[1] !== " " ? " checked" : "") + ">";
        t.children.unshift(box);
        toks[i - 2].attrJoin("class", "task-list-item");
      }
    });
  }

  /* ---- heading ids + outline ----------------------------------------------------------- */
  function headingPlugin(md, out) {
    md.core.ruler.push("heading_ids", function (state) {
      const seen = {};
      const toks = state.tokens;
      for (let i = 0; i < toks.length; i++) {
        if (toks[i].type !== "heading_open") continue;
        const inl = toks[i + 1];
        const text = inl && inl.children ? inl.children.filter(function (c) { return c.type === "text" || c.type === "code_inline"; }).map(function (c) { return c.content; }).join("") : "";
        const id = slugify(text, seen);
        toks[i].attrSet("id", id);
        out.push({ level: Number(toks[i].tag.slice(1)), text: text, id: id });
      }
    });
  }

  /* ---- source lines: data-line on every top-level block (scroll sync) ----------------- */
  function sourceLinePlugin(md, offset) {
    md.core.ruler.push("source_lines", function (state) {
      state.tokens.forEach(function (t) {
        if (t.map && t.level === 0 && t.nesting >= 0 && t.type !== "inline") {
          t.attrSet("data-line", String(t.map[0] + 1 + offset));
          if (t.type === "math_block") t.meta = Object.assign({}, t.meta, { line: t.map[0] + 1 + offset });
        }
      });
    });
    const fence = md.renderer.rules.fence;
    md.renderer.rules.fence = function (tokens, idx, options, env, self) {
      const t = tokens[idx];
      const line = t.attrGet("data-line");
      const html = fence(tokens, idx, options, env, self);
      return line && html.indexOf("data-line") < 0 ? html.replace(/^<pre/, '<pre data-line="' + line + '"') : html;
    };
    const math = md.renderer.rules.math_block;
    md.renderer.rules.math_block = function (tokens, idx, options, env, self) {
      const line = tokens[idx].attrGet("data-line");
      return '<div class="math-block"' + (line ? ' data-line="' + line + '"' : "") + ">" + math(tokens, idx, options, env, self) + "</div>\n";
    };
  }

  /* ---- images and links ----------------------------------------------------------------- */
  function linkPlugin(md, ctx) {
    const baseImg = md.renderer.rules.image;
    md.renderer.rules.image = function (tokens, idx, options, env, self) {
      const t = tokens[idx];
      const src = t.attrGet("src") || "";
      t.attrSet("src", relUrl(ctx, src));
      return baseImg(tokens, idx, options, env, self);
    };
    const baseLink = md.renderer.rules.link_open || function (tokens, idx, options, env, self) { return self.renderToken(tokens, idx, options); };
    md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
      const t = tokens[idx];
      const href = t.attrGet("href") || "";
      if (/^https?:/i.test(href)) {
        t.attrSet("title", href + " (external links do not open inside the page)");
        t.attrJoin("class", "external");
      } else if (/\.md$/i.test(href) && isRelative(href)) {
        // a relative link to another note behaves like a wikilink
        const key = ctx.resolve(href.replace(/\.md$/i, ""));
        if (key !== null && key !== undefined) {
          t.attrSet("href", "#");
          t.attrSet("data-key", key);
          t.attrJoin("class", "wikilink");
        }
      }
      return baseLink(tokens, idx, options, env, self);
    };
  }

  function frontmatterHtml(fm) {
    if (!fm) return "";
    const keys = Object.keys(fm);
    if (!keys.length) return "";
    return '<div class="frontmatter">' + keys.map(function (k) { return "<b>" + escapeHtml(k) + "</b>: " + escapeHtml(fm[k]); }).join(" &middot; ") + "</div>";
  }

  function render(text, ctx) {
    ctx = ctx || {};
    ctx.resolve = ctx.resolve || function () { return null; };
    const headings = [];
    const md = window.markdownit({ html: false, linkify: true, typographer: false, breaks: false });
    wikilinkPlugin(md, ctx);
    calloutPlugin(md);
    mathPlugin(md);
    fencePlugin(md);
    taskListPlugin(md);
    headingPlugin(md, headings);
    linkPlugin(md, ctx);
    const split = splitFrontmatter(text);
    // block -> source line, counting the stripped frontmatter so a source view agrees
    const offset = split.frontmatter ? text.slice(0, text.length - split.body.length).split("\n").length - 1 : 0;
    sourceLinePlugin(md, offset);
    const html = frontmatterHtml(split.frontmatter) + md.render(split.body);
    return { html: html, headings: headings, frontmatter: split.frontmatter, lineOffset: offset };
  }

  let mermaidReady = false;
  function runMermaid(root) {
    const nodes = root.querySelectorAll("pre.mermaid");
    if (!nodes.length || typeof mermaid === "undefined") return Promise.resolve(0);
    if (!mermaidReady) {
      const css = getComputedStyle(document.documentElement);
      const v = function (n, f) { return (css.getPropertyValue(n) || f).trim(); };
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "base",
        fontFamily: v("--font-ui", "Segoe UI") + ", sans-serif",
        themeVariables: {
          primaryColor: v("--color-accent-soft", "#dce8f5"),
          primaryTextColor: v("--color-text", "#3a3630"),
          primaryBorderColor: v("--color-accent", "#2f6fab"),
          lineColor: v("--color-text-muted", "#7a7266"),
          secondaryColor: v("--color-surface-alt", "#faf7f1"),
          tertiaryColor: v("--color-bg", "#f4efe6"),
          background: v("--color-surface", "#ffffff"),
        },
      });
      mermaidReady = true;
    }
    return mermaid.run({ nodes: nodes, suppressErrors: true }).then(function () { return nodes.length; }).catch(function (e) {
      nodes.forEach(function (n) {
        if (!n.querySelector("svg")) {
          const err = document.createElement("div");
          err.className = "mermaid-error";
          err.textContent = "mermaid: " + (e && e.message ? e.message : String(e));
          n.appendChild(err);
        }
      });
      return nodes.length;
    });
  }

  window.SpectoMarkdown = {
    render: render,
    runMermaid: runMermaid,
    parseWikilink: parseWikilink,
    splitFrontmatter: splitFrontmatter,
    slugify: function (t) { return slugify(t, {}); },
    relUrl: relUrl,
  };
})();
