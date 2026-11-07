// @ts-check
/// <reference path="../../types/specto.d.ts" />
/* Map view: MapLibre GL with one layer per GeoJSON file of the view's features input,
 * geojson.io-style.
 *
 * Python sends `layers` {layers: [{id, key, label, url, visible, color, features, bbox,
 * error}], basemap, basemaps, online, net_tiles, link}; the page fetches each url (specto://items,
 * read:link:<link>), draws fill/line/circle layers per file, lists them in the LAYERS
 * side panel (#130: a row's check, in the layer's colour, shows or hides it; All / None;
 * a double click fits the layer), tells the host what is hovered (`hover`), shows a
 * clicked feature's name and properties in a popup and emits `selection {keys, last}`
 * (ids are `<layer id>#<feature index>`), highlights the selection that arrives on its
 * own `selection` port, and writes visibility/colour (`layers`), `basemap` and the
 * panel's state (`panels.layers` {collapsed, width}) as its own params through
 * setParams. The footer: the basemap switcher, frame all and the LAYERS panel's toggle
 * (on while the panel shows; the panel's own collapse and strip do the same). The
 * camera is the page's own.
 *
 * Basemap (#157): `basemaps` is the provider table (web/basemaps.py: name, label, tile
 * and overlay URL templates, attribution, max zoom); the footer's switcher offers None
 * and each provider, the shown one lit, and a click writes `basemap`. The page draws the
 * provider Python says `basemap` is (capability net:tiles, hosts reachable); the request
 * interceptor is the real gate. Otherwise a blank style.
 *
 * Reload and batches (#181): `revision` counts the features input's deliveries; a new one
 * is a data reload (a refresh, a watched arrival, a link re-resolve), so every layer is
 * fetched again and the map frames what they hold once loaded. Anything else (a colour,
 * the basemap, Load more) keeps the user's camera. Python sends the first `limit` of the
 * `total` GeoJSON files (params.limit, else params.batch, 200 by default); over a batch,
 * the footer's meter (footer.js) shows loaded / total and Load more writes
 * `limit` = loaded + batch. */
(function () {
  "use strict";
  const $ = function (id) { return /** @type {HTMLElement} */ (document.getElementById(id)); };
  /** Layer colours: the theme's link-chain tokens (CSS variables the bridge sets). */
  function palette() {
    const css = getComputedStyle(document.documentElement);
    const out = [];
    for (let i = 0; i < 6; i++) {
      const c = css.getPropertyValue("--color-chain-" + i).trim();
      if (c) out.push(c);
    }
    return out;
  }

  /** @type {any} */
  let map = null;
  let styleReady = false;
  /** @type {Record<string, SpectoMapLayer & {data?: any, loaded?: boolean, ticket?: number}>} */
  const layers = {};
  /** @type {string[]} */
  let order = [];
  let basemap = "none";
  /** the basemap providers, as Python sends them with the layers */
  /** @type {SpectoBasemap[]} */
  let providers = [];
  let online = null;
  let netTiles = false;
  /** @type {{layers?: Record<string, {visible?: boolean, color?: string}>, basemap?: string, panels?: Record<string, {collapsed?: boolean, width?: number}>}} */
  let params = {};
  /** @type {SpectoSelection | null} */
  let selected = null;
  /** @type {{id: string, properties: any} | null} */
  let hovered = null;
  let popup = null;
  let pendingFitAll = true;   // fit everything once the first (or an all-new) set has loaded
  /** the features delivery shown (#181): another one is a data reload */
  let revision = /** @type {number | null | undefined} */ (null);
  let batch = 200;
  let fetches = 0;
  let hoverTimer = 0;

  const footer = SpectoFooter.mount($("footer"), { step: null });
  // the basemap switcher (#157): None, then one segment per provider, the shown one lit
  const switcher = document.createElement("span");
  switcher.className = "seg basemaps";
  switcher.id = "basemap";
  footer.el.appendChild(switcher);
  /** @type {Record<string, HTMLButtonElement>} */
  const choices = {};
  const frameAll = footer.tool("target", "Frame every visible layer", function () { fitAll(); }, "frameAll");
  const panel = SpectoSidePanel.mount($("row"), {
    id: "layers", title: "Layers", side: "right", keep: "layers", allNone: true,
    width: 180, minWidth: 120, maxWidth: 420,
    onAll: function () { setAllVisible(true); },
    onNone: function () { setAllVisible(false); },
    onCollapse: function () { setLayersButton(); reportSoon(); },
    onResize: reportSoon,
  });
  const layersButton = footer.tool("layers", "Show or hide the layers panel", function () { panel.toggle(); }, "layersToggle");
  // over a batch (#181): loaded / total and Load more, on the footer's left
  const meter = footer.meter(function () { write({ limit: order.length + batch }); }, { id: "loadMore", noun: "layers" });
  setLayersButton();
  setBasemapButton();

  function setLayersButton() { footer.setOn(layersButton, !panel.collapsed); }

  function setError(msg) { Specto.setHealth(msg ? "error" : "ok", msg || ""); }

  function write(patch) {
    params = Object.assign({}, params, patch);
    reportSoon();
    Specto.setParams(patch).then(function (r) { if (r.ok === false) setError(r.error.message); });
  }

  function cssVar(name, fallback) {
    return (getComputedStyle(document.documentElement).getPropertyValue(name) || fallback).trim();
  }

  /* ---- style ---------------------------------------------------------------------------- */
  function styleFor(kind) {
    const bg = cssVar("--color-surface-alt", "#faf7f1");
    /** @type {any} */
    const style = { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": bg } }] };
    const p = providerOf(kind);
    if (p) {
      // the provider's tiles, then its overlays (labels) over them; the layers go on top
      style.sources.base = { type: "raster", tiles: p.tiles, tileSize: 256, maxzoom: p.max_zoom, attribution: p.attribution };
      style.layers.push({ id: "base", type: "raster", source: "base" });
      p.overlays.forEach(function (url, i) {
        style.sources["overlay" + i] = { type: "raster", tiles: [url], tileSize: 256, maxzoom: p.max_zoom };
        style.layers.push({ id: "overlay" + i, type: "raster", source: "overlay" + i });
      });
    }
    return style;
  }

  /** @param {string} name */
  function providerOf(name) {
    return providers.find(function (p) { return p.name === name; }) || null;
  }

  function choice(name, label) {
    const b = document.createElement("button");
    b.className = "quiet";
    b.textContent = label;
    b.setAttribute("data-basemap", name);
    b.addEventListener("click", function () { if (name !== basemap) write({ basemap: name }); });
    switcher.appendChild(b);
    choices[name] = b;
  }

  /* the switcher: the shown basemap lit; a provider is disabled without net:tiles or
     while its own tile hosts do not answer (`reachable` false), and its tip says why */
  function setBasemapButton() {
    if (!choices.none) choice("none", "None");
    providers.forEach(function (p) { if (!choices[p.name]) choice(p.name, p.label); });
    const noNet = !netTiles ? "this view has no net:tiles capability" : "";
    const wanted = params.basemap || "osm";
    Object.keys(choices).forEach(function (name) {
      const b = choices[name];
      const p = providerOf(name);
      b.classList.toggle("on", name === basemap);
      const why = !p ? "" : noNet || (p.reachable === false
        ? "its tile host " + p.hosts.join(", ") + " is unreachable" : "");
      b.disabled = why !== "";
      if (!p) b.title = "no basemap: the layers on a plain background";
      else if (why) b.title = p.label + " basemap: " + why;
      else if (name === wanted && name !== basemap) b.title = p.label + " basemap: probing the tile hosts...";
      else b.title = p.label + " basemap tiles (net:tiles)";
    });
  }

  function applyBasemap(kind) {
    if (!map) return;
    if (kind === basemap && styleReady) return;
    basemap = kind;
    setBasemapButton();
    styleReady = false;
    map.setStyle(styleFor(kind));
  }

  /* ---- layers ---------------------------------------------------------------------------- */
  function normalize(geojson) {
    /* a FeatureCollection with integer ids = index, as the resolver numbers them, so
     * selection ids are <layer>#<index> on both sides */
    let features;
    if (geojson.type === "FeatureCollection") features = geojson.features || [];
    else if (geojson.type === "Feature") features = [geojson];
    else features = [{ type: "Feature", properties: {}, geometry: geojson }];
    features.forEach(function (f, i) { f.id = i; if (!f.properties) f.properties = {}; });
    return { type: "FeatureCollection", features: features };
  }

  function colorExpr(color) {
    return ["case", ["boolean", ["feature-state", "selected"], false], cssVar("--color-accent", "#2f6fab"), color];
  }

  function addToMap(lay) {
    if (!map || !styleReady || !lay.data || map.getSource(lay.id)) return;
    map.addSource(lay.id, { type: "geojson", data: lay.data });
    const vis = lay.visible ? "visible" : "none";
    map.addLayer({
      id: lay.id + "-fill", type: "fill", source: lay.id,
      filter: ["any", ["==", ["geometry-type"], "Polygon"], ["==", ["geometry-type"], "MultiPolygon"]],
      layout: { visibility: vis },
      paint: { "fill-color": colorExpr(lay.color), "fill-opacity": 0.18 },
    });
    map.addLayer({
      id: lay.id + "-line", type: "line", source: lay.id,
      filter: ["any", ["==", ["geometry-type"], "LineString"], ["==", ["geometry-type"], "MultiLineString"],
               ["==", ["geometry-type"], "Polygon"], ["==", ["geometry-type"], "MultiPolygon"]],
      layout: { visibility: vis, "line-cap": "round", "line-join": "round" },
      paint: { "line-color": colorExpr(lay.color), "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 5, 2.5] },
    });
    map.addLayer({
      id: lay.id + "-point", type: "circle", source: lay.id,
      filter: ["any", ["==", ["geometry-type"], "Point"], ["==", ["geometry-type"], "MultiPoint"]],
      layout: { visibility: vis },
      paint: {
        "circle-color": colorExpr(lay.color),
        "circle-radius": ["case", ["boolean", ["feature-state", "selected"], false], 9, 6],
        "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5,
      },
    });
    applySelectionState(lay.id);
  }

  function removeFromMap(id) {
    if (!map || !styleReady) return;
    ["-fill", "-line", "-point"].forEach(function (sfx) { if (map.getLayer(id + sfx)) map.removeLayer(id + sfx); });
    if (map.getSource(id)) map.removeSource(id);
  }

  function updateOnMap(lay) {
    if (!map || !styleReady || !map.getSource(lay.id)) { addToMap(lay); return; }
    const vis = lay.visible ? "visible" : "none";
    map.setLayoutProperty(lay.id + "-fill", "visibility", vis);
    map.setLayoutProperty(lay.id + "-line", "visibility", vis);
    map.setLayoutProperty(lay.id + "-point", "visibility", vis);
    map.setPaintProperty(lay.id + "-fill", "fill-color", colorExpr(lay.color));
    map.setPaintProperty(lay.id + "-line", "line-color", colorExpr(lay.color));
    map.setPaintProperty(lay.id + "-point", "circle-color", colorExpr(lay.color));
  }

  function loadLayer(lay) {
    if (lay.error) return;  // the resolver could not parse it: listed, not drawn
    const ticket = ++fetches;
    lay.ticket = ticket;
    fetch(lay.url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status + " for " + lay.url);
      return r.json();
    }).then(function (geojson) {
      // removed meanwhile, or fetched again since (a reload)
      if (layers[lay.id] !== lay || lay.ticket !== ticket) return;
      lay.data = normalize(geojson);
      lay.loaded = true;
      addToMap(lay);
      reportSoon();
      fitWhenLoaded();
      setError("");
    }).catch(function (e) {
      setError(e.message);
    });
  }

  /* frame everything once the pending set has loaded (or failed) */
  function fitWhenLoaded() {
    if (!pendingFitAll || !order.length) return;
    if (!order.every(function (k) { return layers[k].loaded || layers[k].error; })) return;
    pendingFitAll = false;
    fitAll();
  }

  /** @param {SpectoMapLayersNotify} p */
  function onLayers(p) {
    const incoming = p.layers || [];
    // a data reload (#181) is fetched again and framed once loaded; so is a set with no
    // layer in common with the shown one (none yet, or a per-pick link that moved on to
    // another place, #176); any other change keeps the user's camera
    const reload = p.revision !== revision;
    revision = p.revision;
    if (reload || !incoming.some(function (l) { return layers[l.id]; })) pendingFitAll = true;
    netTiles = !!p.net_tiles;
    online = p.online;
    const seen = {};
    incoming.forEach(function (l) {
      seen[l.id] = true;
      const cur = layers[l.id];
      if (!cur) {
        layers[l.id] = Object.assign({ data: null, loaded: false }, l);
        loadLayer(layers[l.id]);
      } else {
        const changed = cur.visible !== l.visible || cur.color !== l.color;
        const refetch = reload || cur.url !== l.url || cur.error !== l.error;
        Object.assign(cur, l);
        if (refetch) { removeFromMap(cur.id); cur.data = null; cur.loaded = false; loadLayer(cur); }
        else if (changed) updateOnMap(cur);
      }
    });
    Object.keys(layers).forEach(function (id) { if (!seen[id]) { removeFromMap(id); delete layers[id]; } });
    order = incoming.map(function (l) { return l.id; });
    providers = p.basemaps || [];
    applyBasemap(providerOf(p.basemap) ? p.basemap : "none");
    setBasemapButton();
    batch = p.batch > 0 ? p.batch : 200;
    const total = typeof p.total === "number" ? p.total : order.length;
    // shown over a batch, or while the agent's limit leaves some out
    meter.set(order.length, total, { shown: total > batch || order.length < total, next: batch });
    renderList();
    fitWhenLoaded();
  }

  /* ---- what the page shows, with CSS px rects, for `snap` ------------------------------- */
  let reportTimer = 0;
  function reportPage() {
    const rows = Array.prototype.map.call(panel.body.querySelectorAll(".panel-row"), function (row) {
      return {
        id: row.getAttribute("data-layer"),
        rect: Specto.rectOf(row),
        checkbox: Specto.rectOf(row.querySelector(".check")),
        count: featureCount(row.getAttribute("data-layer")),
        visible: !row.classList.contains("off"),
      };
    });
    const features = [];
    if (map && styleReady) {
      const mapRect = Specto.rectOf($("map"));
      order.forEach(function (id) {
        const lay = layers[id];
        if (!lay || !lay.data || !lay.visible) return;
        lay.data.features.forEach(function (f) {
          if (!f.geometry || f.geometry.type !== "Point") return;
          const p = map.project(f.geometry.coordinates);
          if (p.x < 0 || p.y < 0 || p.x > mapRect[2] || p.y > mapRect[3]) return;
          features.push({ id: id + "#" + f.id, x: Math.round(mapRect[0] + p.x), y: Math.round(mapRect[1] + p.y), name: featureName(f) });
        });
      });
    }
    const c = map ? map.getCenter() : null;
    const pc = document.querySelector(".maplibregl-popup-content");
    Specto.report({
      layers: rows,
      basemap: basemap,
      online: online,
      center: c ? [Number(c.lng.toFixed(5)), Number(c.lat.toFixed(5))] : null,
      zoom: map ? Number(map.getZoom().toFixed(2)) : null,
      points: features,
      popup: popup && popup.isOpen() && pc ? { rect: Specto.rectOf(pc), text: pc.textContent || "" } : null,
      hovered: hovered ? hovered.id : null,
      selected: selected ? selected.keys : [],
      panel: panel.rects(),
      buttons: { frameAll: Specto.rectOf(frameAll), basemap: Specto.rectOf(switcher), layers: Specto.rectOf(layersButton), loadMore: Specto.rectOf(meter.button) },
      meter: Object.assign({}, meter.state),
      basemaps: Object.keys(choices).reduce(function (o, name) { o[name] = Specto.rectOf(choices[name]); return o; }, /** @type {Record<string, any>} */ ({})),
      map: Specto.rectOf($("map")),
      dpr: window.devicePixelRatio,
    });
  }
  function reportSoon() { clearTimeout(reportTimer); reportTimer = window.setTimeout(reportPage, 200); }

  /* ---- the LAYERS panel ---------------------------------------------------------------- */
  function featureCount(id) {
    const lay = layers[id];
    if (!lay) return "";
    return lay.error ? "!" : (lay.features === null || lay.features === undefined ? "..." : String(lay.features));
  }

  function renderList() {
    const list = panel.body;
    list.innerHTML = "";
    if (!order.length) {
      const none = document.createElement("div");
      none.className = "layers-none";
      none.textContent = "no GeoJSON on the features port";
      list.appendChild(none);
    }
    order.forEach(function (id) {
      const lay = layers[id];
      if (!lay) return;
      const row = SpectoSidePanel.row({
        checked: !!lay.visible,
        name: lay.label || lay.key,
        title: (lay.error ? lay.error : id + " (" + featureCount(id) + " features)") + "\ndouble-click to fit",
        onToggle: function (on) { setLayerParams(id, { visible: on }); },
      });
      row.classList.toggle("off", !lay.visible);
      row.classList.toggle("broken", !!lay.error);
      row.setAttribute("data-layer", id);
      /** @type {HTMLElement} */ (row.querySelector(".check")).style.setProperty("--check", lay.color);
      row.addEventListener("dblclick", function () { fitLayer(id); });
      list.appendChild(row);
    });
    reportSoon();
  }

  /* All / None: every layer in one write (one user submit, one undo step) */
  function setAllVisible(on) {
    const all = Object.assign({}, params.layers || {});
    order.forEach(function (id) {
      const lay = layers[id];
      if (!lay) return;
      lay.visible = on;
      updateOnMap(lay);
      all[id] = Object.assign({}, all[id] || {}, { visible: on, color: lay.color });
    });
    renderList();
    write({ layers: all });
  }

  function setLayerParams(id, patch) {
    const lay = layers[id];
    if (!lay) return;
    Object.assign(lay, patch);
    updateOnMap(lay);
    renderList();
    const all = Object.assign({}, params.layers || {});
    all[id] = Object.assign({}, all[id] || {}, { visible: lay.visible, color: lay.color });
    write({ layers: all });
  }

  /* ---- camera (the page's own) ---------------------------------------------------------- */
  function fitBounds(b) {
    if (!map || !b) return;
    if (b[0] === b[2] && b[1] === b[3]) map.flyTo({ center: [b[0], b[1]], zoom: Math.max(map.getZoom(), 14), duration: 300 });
    else map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 300, maxZoom: 17 });
  }

  function fitLayer(id) { const lay = layers[id]; if (lay && lay.bbox) fitBounds(lay.bbox); }

  function fitAll() {
    let b = null;
    order.forEach(function (id) {
      const lay = layers[id];
      if (!lay || !lay.visible || !lay.bbox) return;
      b = b ? [Math.min(b[0], lay.bbox[0]), Math.min(b[1], lay.bbox[1]), Math.max(b[2], lay.bbox[2]), Math.max(b[3], lay.bbox[3])] : lay.bbox.slice();
    });
    if (b) fitBounds(b);
  }

  /* ---- selection and hover ---------------------------------------------------------------- */
  /** @param {string} [only] one layer (just added), else every layer */
  function applySelectionState(only) {
    if (!map || !styleReady) return;
    const ids = selected ? selected.keys : [];
    (only ? [only] : order).forEach(function (id) {
      const lay = layers[id];
      if (!lay || !lay.data || !map.getSource(id)) return;
      lay.data.features.forEach(function (f) {
        map.setFeatureState({ source: id, id: f.id }, { selected: ids.indexOf(id + "#" + f.id) >= 0 });
      });
    });
  }

  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"); }

  function featureName(f) {
    const p = f.properties || {};
    return p.name || p.title || p.label || p.id || "";
  }

  /* the popup: the clicked feature's layer, name and properties */
  function popupHtml(layerId, f) {
    const name = featureName(f);
    const props = f.properties || {};
    const rows = Object.keys(props).map(function (k) {
      return "<tr><td class=\"k\">" + esc(k) + "</td><td class=\"v\">" + esc(props[k]) + "</td></tr>";
    });
    return "<div class=\"layer\">" + esc(layerId) + "#" + f.id + " &middot; " + esc(f.geometry ? f.geometry.type : "") + "</div>" +
      (name ? "<b>" + esc(name) + "</b>" : "") +
      (rows.length ? "<table>" + rows.join("") + "</table>" : "<i>no properties</i>");
  }

  function dataLayerIds() {
    const ids = [];
    order.forEach(function (id) {
      if (!map.getSource(id)) return;
      ["-point", "-line", "-fill"].forEach(function (sfx) { if (map.getLayer(id + sfx)) ids.push(id + sfx); });
    });
    return ids;
  }

  /* the feature under the cursor: a point beats a line beats a fill, whatever the
   * layer order (geojson.io: small things are what you aim at) */
  function featureAt(point) {
    const ids = dataLayerIds();
    if (!ids.length) return null;
    const feats = map.queryRenderedFeatures(point, { layers: ids });
    if (!feats.length) return null;
    const rank = function (f) { return /-point$/.test(f.layer.id) ? 0 : /-line$/.test(f.layer.id) ? 1 : 2; };
    let best = feats[0];
    for (let i = 1; i < feats.length; i++) if (rank(feats[i]) < rank(best)) best = feats[i];
    return best;
  }

  function layerOf(f) { return f.layer.id.replace(/-(fill|line|point)$/, ""); }

  /* geojson.io: the properties of the feature under the cursor, live */
  function onMapMove(e) {
    const f = featureAt(e.point);
    map.getCanvas().style.cursor = f ? "pointer" : "";
    const id = f ? layerOf(f) + "#" + f.id : null;
    if ((hovered ? hovered.id : null) === id) return;
    hovered = f ? { id: id, properties: f.properties || {} } : null;
    clearTimeout(hoverTimer);
    hoverTimer = window.setTimeout(function () { Specto.send("hover", hovered); reportSoon(); }, 120);
  }

  function onMapClick(e) {
    const f = featureAt(e.point);
    if (!f) return;
    const layerId = layerOf(f);
    const fid = layerId + "#" + f.id;
    if (popup) popup.remove();
    const maxW = Math.max(120, Math.min(260, Math.round($("map").clientWidth * 0.6)));
    popup = new maplibregl.Popup({ closeButton: true, maxWidth: maxW + "px" }).setLngLat(e.lngLat).setHTML(popupHtml(layerId, f)).addTo(map);
    selected = { keys: [fid], last: fid };
    applySelectionState();
    popup.on("close", reportSoon);
    reportSoon();
    Specto.emit("selection", selected).then(function (r) {
      if (r.ok === false) setError(r.error.message);
    });
  }

  /* a selection arriving on the port (another view picked): highlight, never re-emit */
  function onSelectionPort(value) {
    selected = value && Array.isArray(value.keys) ? { keys: value.keys, last: value.last || null } : null;
    applySelectionState();
    reportSoon();
  }

  /* ---- bridge ----------------------------------------------------------------------------- */
  function applyParams(p) {
    params = Object.assign({}, params, p || {});
    SpectoSidePanel.restore(params.panels);
    setLayersButton();
    setBasemapButton();
    reportSoon();
  }

  function makeMap() {
    map = new maplibregl.Map({
      container: "map",
      style: styleFor("none"),
      center: [0, 20],
      zoom: 1,
      // the provider's credit stays in full view (Esri's terms ask for it on the map)
      attributionControl: { compact: false },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    map.on("style.load", function () {
      styleReady = true;
      order.forEach(function (id) { addToMap(layers[id]); });
      applySelectionState();
    });
    map.on("idle", reportSoon);
    map.on("click", onMapClick);
    map.on("mousemove", onMapMove);
    map.on("mouseout", function () { if (hovered) { hovered = null; Specto.send("hover", null); } });
    map.on("moveend", reportSoon);
    map.on("error", function (e) {
      const msg = e && e.error ? e.error.message : String(e);
      if (/tile|Failed to fetch|NetworkError/i.test(msg)) {
        Specto.log("warn", "basemap tile failed: " + msg);
        return;  // tiles come and go; the interceptor may have blocked them
      }
      setError(msg);
    });
  }

  Specto.on("config", function (cfg) { applyParams(cfg.params); });
  Specto.on("params", function (p) { applyParams(p); });
  Specto.on("layers", onLayers);
  Specto.on("port:selection", onSelectionPort);

  try {
    makeMap();
  } catch (e) {
    setError("MapLibre could not start: " + e.message);
  }
  Specto.connect().then(function () {
    if (map) map.resize();
  }).catch(function (e) {
    setError(e.message);
  });
})();
