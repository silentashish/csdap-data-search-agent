// Chainlit custom element: embeds the standalone explore panel (map + filters +
// results) as a same-origin iframe. The heavy UI lives in /panel/app; this is
// just the sidebar host. `props.url` is set from Python (carries the thread id).
export default function ExplorePanel() {
  return (
    <iframe
      src={props.url}
      title="CSDA Explore Panel"
      style={{
        width: "100%",
        height: "88vh",
        border: "none",
        borderRadius: "8px",
      }}
    />
  );
}
