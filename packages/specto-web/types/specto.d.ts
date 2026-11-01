// Hand-written types for the web-view bridge (bridge.js, docs/contracts.md 6 and 8; ports
// per ADR 0014) on top of the generated SpectoApi namespace (specto-api.d.ts, from
// api-info.json). Pages opt in with `// @ts-check` and
// `/// <reference path="../types/specto.d.ts" />`; `npm run typecheck` in
// packages/specto-web runs tsc over them.
/// <reference path="./specto-api.d.ts" />

/** One link feeding an input port of this view (WebView.inputs). */
interface SpectoInputLink {
  link: string;
  kind: string;
  from: string;
}

/** What Python sends once after `ready()` (WebView.config). */
interface SpectoConfig {
  page: string;
  view: string | null;
  type: SpectoApi.ViewType | string;
  label: string;
  params: Record<string, unknown>;
  inputs: Record<string, SpectoInputLink>;
  capabilities: string[];
  theme: SpectoApi.ThemeTokens;
  /** The platform's drag distance (CSS px): a press that moves this far is a drag (#185). */
  dragDistance: number;
}

/** A value on an input port (`port` signal): null when the link feeding it went away. */
interface SpectoPortMessage {
  port: string;
  value: unknown;
}

/** The `selection` port value (specto_core.links.Selection). */
interface SpectoSelection {
  keys: string[];
  last: string | null;
}

/** One parsed file of a `content` port value (specto_core.contents.ContentEntry). */
interface SpectoContentEntry {
  key: string;
  path: string;
  format: "geojson" | "json" | "text";
  data: unknown;
  meta: Record<string, unknown>;
  error: string | null;
}

interface SpectoContentValue {
  root: string;
  entries: SpectoContentEntry[];
  link: string;
}

/** A refused or failed bridge call (SpectoError.to_dict()). */
interface SpectoErrorInfo {
  code: string;
  message: string;
  fix?: string;
  data?: Record<string, unknown>;
}

/** `emit` / `setParams` reply. */
type SpectoReply<T = unknown> = { ok: true; result: T } | { ok: false; error: SpectoErrorInfo };

/** `item` notification of the json / markdown hosts. */
interface SpectoItemNotify {
  key: string | null;
  url: string | null;
  roles?: string[];
  role?: string;
  name?: string;
  file?: string;
  reason?: string;
  /** json: a content value linked in (#140) comes inline, with no url */
  text?: string;
}

/** `notes` notification of the markdown host. */
interface SpectoNotesNotify {
  notes: { key: string; name: string; url: string; role: string }[];
}

/** `step` notification of a viewer (specto_app.hosts.stepping, #107): where the shown
 * item is in the collection the view steps through (-1: nothing shown). */
interface SpectoStepNotify {
  index: number;
  count: number;
}

/** One GeoJSON layer of the map host (`layers` notification). */
interface SpectoMapLayer {
  id: string;
  key: string;
  label: string;
  url: string;
  visible: boolean;
  color: string;
  features: number | null;
  bbox: number[] | null;
  error: string | null;
}

/** a basemap provider (specto_app/web/basemaps.py, #157) */
interface SpectoBasemap {
  name: string;
  label: string;
  tiles: string[];
  overlays: string[];
  /** the hosts its templates name */
  hosts: string[];
  attribution: string;
  max_zoom: number;
  /** every host of its templates answered the probe; null until the probe is back */
  reachable: boolean | null;
}

interface SpectoMapLayersNotify {
  layers: SpectoMapLayer[];
  /** the provider shown, or "none" */
  basemap: string;
  basemaps: SpectoBasemap[];
  online: boolean | null;
  net_tiles: boolean;
  link: string | null;
  /** every GeoJSON file on the features input; `layers` holds the first `limit` (#181) */
  total: number;
  /** how many layers load (params.limit, else batch) */
  limit: number;
  /** how many Load more adds (params.batch, 200 by default) */
  batch: number;
  /** the features input's delivery count: a new one is a data reload (framed) */
  revision: number;
}

interface SpectoNotifications {
  config: SpectoConfig;
  params: Record<string, unknown>;
  port: SpectoPortMessage;
  label: string;
  item: SpectoItemNotify;
  notes: SpectoNotesNotify;
  step: SpectoStepNotify;
  layers: SpectoMapLayersNotify;
}

/** What a page's context menu is about (#154): sent as `contextmenu {x, y, target}`. */
interface SpectoContextTarget {
  kind: "items" | "view" | "background";
  [key: string]: unknown;
}

interface SpectoBridge {
  /** Connect the QWebChannel; resolves once `ready()` was sent. */
  connect(): Promise<unknown>;
  on<K extends keyof SpectoNotifications>(kind: K, fn: (data: SpectoNotifications[K]) => void): () => void;
  on(kind: `port:${string}`, fn: (value: any) => void): () => void;
  on(kind: string, fn: (data: any) => void): () => void;
  /** Run a command within the view's `cmd:` capabilities. */
  call<K extends SpectoApi.CommandName>(
    name: K,
    args?: Partial<SpectoApi.CommandMap[K]["args"]>
  ): Promise<SpectoApi.CommandMap[K]["result"]>;
  call(name: string, args?: Record<string, unknown>): Promise<Record<string, unknown>>;
  /** Emit a value on one of this view's output ports (capability `emit:<port>`). */
  emit(port: "selection", value: SpectoSelection): Promise<SpectoReply<{ links: string[] }>>;
  emit(port: string, value: unknown): Promise<SpectoReply<{ links: string[] }>>;
  /** Write keys of this view's own params as one user submit (capability `params:<key>`). */
  setParams(patch: Record<string, unknown>): Promise<SpectoReply<Record<string, unknown>>>;
  /** A page event for the host (no reply): `navigate`, `hover`, ... */
  send(kind: string, payload?: unknown): void;
  log(level: "info" | "warn" | "error", msg: string): void;
  setHealth(status: "ok" | "error" | "paused" | "loading", msg?: string): void;
  applyTheme(theme: Partial<SpectoApi.ThemeTokens>): void;
  /** Report what the page shows (and CSS px rects of its controls) for `snap`. */
  report(state: Record<string, unknown>): void;
  /** [left, top, width, height] of an element in CSS px, or null. */
  rectOf(el: Element | null): [number, number, number, number] | null;
  /** What the shell's context menu is about at a right-click (#154); null: the background. */
  contextTarget: ((ev: MouseEvent) => SpectoContextTarget | null) | null;
  /** Hand a drag of text the page detected itself to the shell (#185; a selection's own
   *  dragstart is handled by the bridge). */
  startDrag(d: { text: string; html?: string | null }): void;
  readonly config: SpectoConfig | null;
}

interface Window {
  Specto: SpectoBridge;
  SpectoMarkdown: {
    render(text: string, ctx: { noteUrl?: string; resolve?: (target: string) => string | null }): {
      html: string;
      headings: { level: number; text: string; id: string }[];
      frontmatter: Record<string, string> | null;
      lineOffset: number;
    };
    runMermaid(root: HTMLElement): Promise<number>;
    parseWikilink(inner: string): { target: string; anchor: string | null; alias: string | null };
    splitFrontmatter(text: string): { body: string; frontmatter: Record<string, string> | null };
    slugify(text: string): string;
    relUrl(ctx: { noteUrl?: string }, src: string): string;
  };
  /** step.js: previous / next through the collection (#107), in the footer (#130). */
  SpectoStep: {
    mount(footer: HTMLElement, keys: string[]): void;
    go(delta: number): void;
    rects(): { prev: [number, number, number, number] | null; next: [number, number, number, number] | null };
    readonly state: SpectoStepNotify;
  };
  /** icons.js: the footer and side panel glyphs (#130), stroked in currentColor. */
  SpectoIcons: {
    svg(name: string): SVGSVGElement;
    button(name: string, tip: string, id?: string): HTMLButtonElement;
    setIcon(button: HTMLButtonElement, name: string): void;
    readonly names: string[];
  };
  /** footer.js: a viewer's footer, step / position / name left, tools right (#130). */
  SpectoFooter: {
    mount(el: HTMLElement, opts: { step?: string[] | null }): SpectoFooterApi;
  };
  /** sidepanel.js: the side panel standard (#130). */
  SpectoSidePanel: {
    mount(row: HTMLElement, opts: SpectoSidePanelOptions): SpectoSidePanelApi;
    /** the view's `panels` param: every kept panel takes its entry, nothing is written */
    restore(panels: Record<string, SpectoPanelState> | null | undefined): void;
    row(opts: {
      checked?: boolean;
      name: string;
      sub?: string;
      title?: string;
      onToggle?: (checked: boolean) => void;
      onClick?: () => void;
    }): HTMLElement;
    bytes(n: number): string;
    readonly STRIP: number;
  };
  /** wheel.js: one wheel notch on any display scale (#194). */
  SpectoWheel: {
    notches(ev: { deltaY?: number; deltaMode?: number }): number;
    counter(): (ev: { deltaY?: number; deltaMode?: number }) => number;
    readonly NOTCH_MIN: number;
    readonly TRACKPAD_NOTCH: number;
  };
  /** zoom.js: no page zoom; a view may zoom its content alone (#189). */
  SpectoZoom: {
    content(el: HTMLElement, opts?: { onChange?: (level: number) => void }): SpectoZoomApi;
    readonly MIN: number;
    readonly MAX: number;
  };
  /** monaco-env.js: load the vendored Monaco; define the "specto-calm" theme. */
  loadMonaco(): Promise<any>;
  defineSpectoTheme(monaco: any): void;
  monaco: any;
}
type SpectoRect = [number, number, number, number];

interface SpectoFooterApi {
  el: HTMLElement;
  setName(text: string, tip?: string): void;
  tool(icon: string, tip: string, onClick: () => void, id?: string): HTMLButtonElement;
  setOn(button: HTMLElement, on: boolean): void;
  meter(onMore: () => void, opts?: { id?: string; noun?: string }): SpectoFooterMeter;
}

/** footer.js meter (#181): loaded / total, a slim bar and Load more */
interface SpectoFooterMeter {
  el: HTMLElement;
  button: HTMLButtonElement;
  state: { loaded: number; total: number; shown: boolean };
  set(loaded: number, total: number, opts?: { shown?: boolean; next?: number }): void;
}

interface SpectoZoomApi {
  readonly level: number;
  set(level: number): void;
  reset(): void;
}

interface SpectoPanelState {
  collapsed?: boolean;
  width?: number;
}

interface SpectoSidePanelOptions {
  id?: string;
  /** the panel's name in the view's `panels` param: user changes are written there */
  keep?: string;
  title: string;
  side?: "left" | "right";
  allNone?: boolean;
  width?: number;
  minWidth?: number;
  maxWidth?: number;
  collapsed?: boolean;
  onAll?: () => void;
  onNone?: () => void;
  onCollapse?: (collapsed: boolean) => void;
  onResize?: (width: number) => void;
}

interface SpectoSidePanelApi {
  el: HTMLElement;
  body: HTMLElement;
  readonly collapsed: boolean;
  readonly width: number;
  /** set the state without calling back or writing */
  apply(state: SpectoPanelState | null | undefined): void;
  setCollapsed(collapsed: boolean): void;
  /** the user's collapse or expand from outside the panel: written and called back */
  toggle(): void;
  setWidth(width: number): void;
  rects(): {
    panel: SpectoRect | null;
    collapsed: boolean;
    width: number;
    toggle: SpectoRect | null;
    grip: SpectoRect | null;
    all: SpectoRect | null;
    none: SpectoRect | null;
  };
}

declare const Specto: SpectoBridge;
declare const SpectoIcons: Window["SpectoIcons"];
declare const SpectoFooter: Window["SpectoFooter"];
declare const SpectoSidePanel: Window["SpectoSidePanel"];
declare const SpectoStep: Window["SpectoStep"];
declare const SpectoZoom: Window["SpectoZoom"];
declare const SpectoWheel: Window["SpectoWheel"];
declare const SpectoMarkdown: Window["SpectoMarkdown"];
declare function loadMonaco(): Promise<any>;
declare function defineSpectoTheme(monaco: any): void;
declare const monaco: any;
declare const maplibregl: any;
declare const katex: any;
declare const mermaid: any;
declare const markdownit: any;
