# Vendored frontend dependencies

These production builds are served from the same origin as Steam-KaKaBase.

| File | Version | SHA-256 |
| --- | --- | --- |
| `vue-3.5.13.global.prod.js` | Vue 3.5.13 | `c459ba7cc8db65c982589fa5d64c7ff478877e8e5b0fd75683207cec6a4e89e8` |
| `echarts-5.6.0.min.js` | Apache ECharts 5.6.0 | `bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1` |

The corresponding upstream licenses are stored beside the builds. When upgrading, pin an exact version, replace the license if necessary, update the hashes above, and run `node --check` on both JavaScript files.
