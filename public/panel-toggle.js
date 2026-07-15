// Finds the chainlit Action button for "toggle_panel" (rendered inline under
// the welcome message) and tags it with a class so panel-toggle.css can pin
// it as a static top-right button. The tooltip text ("Toggle explore panel")
// only exists in a Radix portal shown on hover, so it can't be matched
// up-front — instead we anchor on the action's lucide "map" icon (set via
// icon="map" in chainlit_app.py), which is unique to this button. Runs on a
// MutationObserver since the button is mounted asynchronously and chainlit
// re-renders the message tree on navigation/resume.
(function () {
  const CLASS = "csda-map-toggle-btn";

  function tag() {
    document.querySelectorAll("svg.lucide-map").forEach((svg) => {
      const btn = svg.closest("button");
      if (btn) btn.classList.add(CLASS);
    });
  }

  tag();
  new MutationObserver(tag).observe(document.body, {
    childList: true,
    subtree: true,
  });
})();
