# KLineChart（vendored，研究侧图集专用）

- 版本：klinecharts **10.1.0**（npm 发行包 `dist/umd/klinecharts.min.js`），Apache-2.0，见同目录 LICENSE / NOTICE（含 TradingView Lightweight Charts 声明）。
- npm tarball integrity：`sha512-sExME3Tv1Ey1zHzTjEc203xd5/B8e6WI6sEB2wg6SQjYwuQRB/sGDIZ6IKEfKEDhaBUXVgVXXO1OX5Giasrbew==`
- 本文件 sha256：`428b7c3bff6a74e121b4da8093bd0187d1c81b9fae8826f1143097280667a8a1`（`tests/test_calib_probe.py` 钉住，换版本须同时改钉测与本说明）。
- 用途：`research/calib_probe.py` 生成 gallery.html 时**内联**进页面（离线可开，不联网）。只负责画图——K 线以外的指标（J / MACD）一律喂 `core/indicators.py` 算好的值，不用库内置公式。
- owner 2026-11-10 同意引入（非 Python 依赖，不进 pyproject；live 链路不使用）。
