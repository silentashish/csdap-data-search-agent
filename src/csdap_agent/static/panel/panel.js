/* CSDA Explore Panel — map + filters + results, synced to the backend
 * ExploreState over WebSocket. The agent and the user both drive this state;
 * the panel renders whatever the server broadcasts and sends user actions back. */
(function () {
  "use strict";

  var THREAD = new URLSearchParams(location.search).get("thread") || "default";
  var CFG = null;         // /panel/config
  var MAP = null;
  var DRAW = null;
  var WS = null;
  var applyingState = false;
  var lastRevision = -1;
  var lastHeatmapKey = "";
  var lastFilters = {};
  var expanded = {};      // itemId -> bool
  var isDark = false;
  var onThemeChange = null; // set by initMap() once MAP exists

  var $ = function (id) { return document.getElementById(id); };

  // ------------------------------------------------------------ theme sync
  // Same-origin embed (see app.py: /panel/app and chainlit both mounted on
  // one FastAPI app), so we can read the chainlit shell's theme class
  // directly instead of relying on the OS-level prefers-color-scheme, which
  // doesn't track the in-app light/dark toggle.
  (function syncThemeWithParent() {
    if (window.parent === window) return; // opened standalone, not embedded
    function apply() {
      try {
        var nowDark = window.parent.document.documentElement.classList.contains("dark");
        document.documentElement.dataset.theme = nowDark ? "dark" : "light";
        if (nowDark !== isDark) {
          isDark = nowDark;
          if (onThemeChange) onThemeChange(isDark);
        }
      } catch (e) { /* cross-origin parent; fall back to prefers-color-scheme */ }
    }
    apply();
    try {
      new MutationObserver(apply).observe(window.parent.document.documentElement, {
        attributes: true,
        attributeFilter: ["class"],
      });
    } catch (e) { /* cross-origin parent */ }
  })();

  // ---------------------------------------------------------------- utils
  function bytesToSize(bytes) {
    if (!bytes) return "n/a";
    var sizes = ["B", "KB", "MB", "GB", "TB"];
    var i = Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024));
    if (i === 0) return bytes + " B";
    return (bytes / Math.pow(1024, i)).toFixed(1) + " " + sizes[i];
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // ---------------------------------------------------------------- websocket
  function connect() {
    var proto = location.protocol === "https:" ? "wss" : "ws";
    WS = new WebSocket(proto + "://" + location.host + "/panel/ws/" + THREAD);
    WS.onmessage = function (e) {
      var msg = JSON.parse(e.data);
      if (msg.type === "state") applyState(msg.state);
    };
    WS.onclose = function () { setTimeout(connect, 1500); };
  }
  function send(type, payload) {
    if (WS && WS.readyState === 1) WS.send(JSON.stringify({ type: type, payload: payload || {} }));
  }

  // ---------------------------------------------------------------- render
  function applyState(state) {
    if (state.revision === lastRevision) return;
    lastRevision = state.revision;
    applyingState = true;

    var f = state.filters || {};
    lastFilters = f;
    if (f.collection_slug != null) $("f-collection").value = f.collection_slug;
    $("f-start").value = f.date_start || "";
    $("f-end").value = f.date_end || "";
    $("f-cloud").value = f.cloud_cover_max == null ? 100 : f.cloud_cover_max;
    $("cc-val").textContent = $("f-cloud").value;

    renderStatus(state);
    renderResults(state);
    updateAoi(f);
    updateHeatmap(f);
    applyingState = false;
  }

  function renderStatus(state) {
    var s = $("status");
    if (state.error) { s.textContent = "⚠ " + state.error; s.className = "status error"; }
    else { s.textContent = state.status || ""; s.className = "status"; }
  }

  function renderResults(state) {
    var results = state.results || [];
    $("results-count").textContent = results.length
      ? results.length + " of " + state.matched + " items"
      : "No results";
    $("btn-next").disabled = !state.next_token;
    $("btn-prev").disabled = !state.prev_token;

    var list = $("results-list");
    if (!results.length) { list.innerHTML = '<div class="empty">No results. Set filters and Search.</div>'; return; }

    var selected = state.selected || {};
    list.innerHTML = results.map(function (it) { return renderItem(it, selected); }).join("");

    // wire per-item handlers
    Array.prototype.forEach.call(list.querySelectorAll(".item"), function (el) {
      var id = el.getAttribute("data-id");
      el.querySelector(".chev").onclick = function () {
        expanded[id] = !expanded[id]; el.classList.toggle("expanded");
      };
      el.querySelector('input[type=checkbox]').onchange = function (ev) {
        var item = results.find(function (r) { return r.id === id; });
        var keys = ev.target.checked ? item.assets.map(function (a) { return a.key; }) : [];
        send("select", { item_id: id, asset_keys: keys });
      };
    });
  }

  // Reusable single-result component.
  function renderItem(it, selected) {
    var checked = selected[it.id] && selected[it.id].length ? "checked" : "";
    var cc = it.cloud_cover == null ? "" : " · ☁ " + it.cloud_cover + "%";
    var dt = it.datetime ? it.datetime.slice(0, 10) : "";
    var thumb = it.thumbnail
      ? '<img class="thumb" src="' + esc(it.thumbnail) + '" alt="" onerror="this.style.visibility=\'hidden\'"/>'
      : '<div class="thumb"></div>';
    var assets = (it.assets || []).map(function (a) {
      var url = CFG.orders_url + "/v2/download/" + encodeURIComponent(it.collection) +
        "/" + encodeURIComponent(it.id) + "/" + encodeURIComponent(a.key);
      return '<div class="asset-row"><span>' + esc(a.key) + " · " + bytesToSize(a.size) +
        '</span><a href="' + esc(url) + '" target="_blank" rel="noopener">download</a></div>';
    }).join("");
    return '' +
      '<div class="item' + (expanded[it.id] ? " expanded" : "") + '" data-id="' + esc(it.id) + '">' +
        '<input type="checkbox" ' + checked + ' />' +
        thumb +
        '<div class="meta">' +
          '<div class="id">' + esc(it.id) + '</div>' +
          '<div class="sub">' + esc(dt) + cc + " · " + bytesToSize(it.total_size) +
            " · " + (it.assets ? it.assets.length : 0) + " assets " +
            '<span class="chev">' + (expanded[it.id] ? "▾ hide" : "▸ assets") + '</span></div>' +
          '<div class="assets">' + assets + '</div>' +
        '</div>' +
      '</div>';
  }

  // ---------------------------------------------------------------- map
  function mapStyleForTheme(dark) {
    return "mapbox://styles/mapbox/" + (dark ? "dark-v11" : "light-v11");
  }

  function initMap() {
    mapboxgl.accessToken = CFG.mapbox_token;
    MAP = new mapboxgl.Map({
      container: "map",
      style: mapStyleForTheme(isDark),
      center: [0, 20], zoom: 1, dragRotate: false, pitchWithRotate: false,
    });
    DRAW = new MapboxDraw({ displayControlsDefault: false });
    MAP.addControl(DRAW);
    MAP.on("draw.create", onDraw);
    MAP.on("draw.update", onDraw);
    MAP.on("zoomend", refreshHeatmapPaint);

    onThemeChange = function (dark) {
      MAP.setStyle(mapStyleForTheme(dark));
      // setStyle drops custom sources/layers/draw layers — re-add once the
      // new style has finished loading.
      MAP.once("style.load", function () {
        lastHeatmapKey = "";
        updateAoi(lastFilters);
        updateHeatmap(lastFilters);
      });
    };
  }

  function onDraw() {
    var data = DRAW.getAll();
    if (!data.features.length) return;
    var coords = [];
    data.features.forEach(function (ft) {
      (ft.geometry.coordinates.flat(Infinity)).forEach(function (v, i) { coords.push(v); });
    });
    // coords is [lng,lat,lng,lat,...]
    var lngs = coords.filter(function (_, i) { return i % 2 === 0; });
    var lats = coords.filter(function (_, i) { return i % 2 === 1; });
    var bbox = [Math.min.apply(null, lngs), Math.min.apply(null, lats),
                Math.max.apply(null, lngs), Math.max.apply(null, lats)];
    $("btn-draw").classList.remove("active");
    send("set_filters", { bbox: bbox });
  }

  function updateAoi(f) {
    if (!MAP || !DRAW) return;
    DRAW.deleteAll();
    if (f.bbox && f.bbox.length === 4) {
      var w = f.bbox[0], s = f.bbox[1], e = f.bbox[2], n = f.bbox[3];
      DRAW.add({
        type: "Feature", properties: {},
        geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
      });
      try { MAP.fitBounds([[w, s], [e, n]], { padding: 40, maxZoom: 8, duration: 500 }); } catch (_) {}
    }
  }

  // Heatmap grid vector tiles (proxied same-origin) coloured by cell count.
  function heatmapKey(f) {
    return [f.collection_slug, f.date_start, f.date_end, (f.item_types || []).join(",")].join("|");
  }
  function updateHeatmap(f) {
    if (!MAP || !MAP.isStyleLoaded()) { MAP && MAP.once("load", function () { updateHeatmap(f); }); return; }
    var key = heatmapKey(f);
    if (key === lastHeatmapKey) return;
    lastHeatmapKey = key;

    ["cell-fill", "cell-count"].forEach(function (l) { if (MAP.getLayer(l)) MAP.removeLayer(l); });
    if (MAP.getSource("cells")) MAP.removeSource("cells");
    if (!f.collection_slug) return;

    var qs = "collection=" + encodeURIComponent(f.collection_slug) +
      "&start=" + (f.date_start || "2000-01-01") + "&end=" + (f.date_end || "2100-01-01") +
      (f.item_types || []).map(function (t) { return "&item_types=" + encodeURIComponent(t); }).join("");
    var tile = location.origin + "/panel/heatmap/{z}/{x}/{y}.mvt?" + qs;

    MAP.addSource("cells", { type: "vector", tiles: [tile], minzoom: 0, maxzoom: 4, promoteId: "id" });
    MAP.addLayer({
      id: "cell-fill", type: "fill", source: "cells", "source-layer": "default",
      paint: { "fill-color": "#1f6feb", "fill-opacity": 0.4, "fill-outline-color": "#1f6feb" },
    });
    MAP.addLayer({
      id: "cell-count", type: "symbol", source: "cells", "source-layer": "default",
      layout: { "text-field": ["to-string", ["get", "count"]], "text-size": 10 },
      paint: {
        "text-color": isDark ? "#eee" : "#111",
        "text-halo-color": isDark ? "#000" : "#fff",
        "text-halo-width": 1,
      },
    });
    refreshHeatmapPaint();
  }

  function refreshHeatmapPaint() {
    if (!MAP || !MAP.getLayer("cell-fill")) return;
    var f = { collection_slug: (lastHeatmapKey.split("|")[0] || "") };
    if (!f.collection_slug) return;
    var zoom = Math.min(4, Math.round(MAP.getZoom()));
    var parts = lastHeatmapKey.split("|");
    var qs = "zoom=" + zoom + "&collection=" + encodeURIComponent(parts[0]) +
      "&start=" + (parts[1] || "2000-01-01") + "&end=" + (parts[2] || "2100-01-01");
    fetch(location.origin + "/panel/context?" + qs).then(function (r) { return r.json(); })
      .then(function (d) {
        var max = Math.max(1, d.max_count || 1);
        MAP.setPaintProperty("cell-fill", "fill-opacity", [
          "interpolate", ["linear"], ["coalesce", ["get", "count"], 0], 0, 0.1, max, 0.85,
        ]);
      }).catch(function () {});
  }

  // ---------------------------------------------------------------- filter UI
  function wireFilters() {
    $("f-collection").onchange = function () {
      if (!applyingState) send("set_filters", { collection_slug: this.value });
    };
    $("f-start").onchange = function () { if (!applyingState) send("set_filters", { date_start: this.value || null }); };
    $("f-end").onchange = function () { if (!applyingState) send("set_filters", { date_end: this.value || null }); };
    $("f-cloud").oninput = function () { $("cc-val").textContent = this.value; };
    $("f-cloud").onchange = function () { if (!applyingState) send("set_filters", { cloud_cover_max: Number(this.value) }); };
    $("btn-search").onclick = function () { send("search"); };
    $("btn-next").onclick = function () { send("paginate", { direction: "next" }); };
    $("btn-prev").onclick = function () { send("paginate", { direction: "previous" }); };
    $("btn-draw").onclick = function () { this.classList.add("active"); DRAW.changeMode("draw_polygon"); };
    $("btn-clear-aoi").onclick = function () { send("clear_aoi"); };
  }

  function loadCollections() {
    fetch("/panel/collections").then(function (r) { return r.json(); }).then(function (cols) {
      var sel = $("f-collection");
      cols.forEach(function (c) {
        var o = document.createElement("option");
        o.value = c.id; o.textContent = c.title || c.id;
        sel.appendChild(o);
      });
    }).catch(function () {});
  }

  // ---------------------------------------------------------------- boot
  fetch("/panel/config").then(function (r) { return r.json(); }).then(function (cfg) {
    CFG = cfg;
    initMap();
    wireFilters();
    loadCollections();
    connect();
  });
})();
