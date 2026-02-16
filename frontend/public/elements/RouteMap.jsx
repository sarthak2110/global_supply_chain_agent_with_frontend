export default function RouteMap() {
  const src = props?.src;                 // signed URL
  const height = props?.height || 420;
  const title = props?.title || "Route Map";

  if (!src) {
    return (
      <div className="cl-map-sticky">
        <div className="cl-map-header">
          <div className="cl-map-title">{title}</div>
        </div>
        <div style={{ padding: "12px" }}>No map URL provided.</div>
      </div>
    );
  }

  return (
    <div className="cl-map-sticky">
      <div className="cl-map-header">
        <div className="cl-map-title">{title}</div>
        <a className="cl-map-open" href={src} target="_blank" rel="noreferrer">
          Open fullscreen ↗
        </a>
      </div>

      <iframe
        src={src}
        title={title}
        className="cl-map-iframe"
        style={{ height: `${height}px` }}
      />
    </div>
  );
}




// export default function RouteMap() {
//   const src = props?.src || "/public/maps/route_map.html";
//   const height = props?.height || 420;
//   const title = props?.title || "Route Map";

//   return (
//     <div className="cl-map-sticky">
//       <div className="cl-map-header">
//         <div className="cl-map-title">{title}</div>
//         <a className="cl-map-open" href={src} target="_blank" rel="noreferrer">
//           Open fullscreen ↗
//         </a>
//       </div>

//       <iframe
//         src={src}
//         title={title}
//         className="cl-map-iframe"
//         style={{ height: `${height}px` }}
//       />
//     </div>
//   );
// }