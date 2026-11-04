// dependency-cruiser. Replace the layer paths. Researched 2026-11-04.
module.exports = {
  forbidden: [
    { name: "no-circular", severity: "error", from: {}, to: { circular: true } },
    { name: "domain-not-to-interface", severity: "error", from: { path: "^src/domain" }, to: { path: "^src/interface" } },
    { name: "domain-no-external", severity: "error", from: { path: "^src/domain" }, to: { dependencyTypes: ["npm"] } },
  ],
  options: { doNotFollow: { path: "node_modules" } },
};
