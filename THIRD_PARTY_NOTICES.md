# 第三方软件与模型许可

根目录 [LICENSE](LICENSE) 仅约束本项目权利人原创部分。第三方代码、模型、运行时及其派生数据继续适用其自己的许可；不能用本项目“商用需授权”的条款剥夺第三方原许可授予的权利。

## 3D 模型

- `assets/library/` 的 19 种常规模型来自 KiCad / KiCad StepUp WRL，模型头保留逐文件作者、年份、CC-BY-SA-4.0 和电子设计输出例外。JSON 为这些模型转换后的网格，部分几何/外观经过调整；同样保留上游许可及修改说明。
- `用户模型库/` 的 45 种补充模型来自 [KiCad packages3D](https://gitlab.com/kicad/libraries/kicad-packages3D)，固定版本 `8975715167d1a76f9b44be155ac02326f704cdbb`。保留原始 STEP 实体，预览网格按本体、端子、阴极条纹分组，颜色为预览/STEP 一致性做了调整，未通过缩放改造不同封装。
- KiCad 库许可为 **CC-BY-SA-4.0，附 KiCad 电子设计/设计输出例外**。模型库本身及其改编作为集合再分发时仍应保留署名、来源和相同方式共享等义务；设计输出的适用范围以许可原文为准。
- 许可原文、上游 README/CREDITS、下载地址、SHA256 和固定版本见 `用户模型库/来源与许可/KiCad/`；逐模型摘要见 `docs/model-inventory.json` 和 `models.json` 的 `source` 字段。
- 本公开版不分发本地开发版使用的 Pulse 官方 H1102NL、EasyEDA/JLCEDA 连接器、真实板专用模型或原规格书/照片。用户可自行取得相应使用权并手动导入；公开可下载并不自动等于可以再分发。

## 浏览器资源

`assets/vendor/`：Three.js r160 及附属 loaders/controls，MIT，Copyright 2010–2023 Three.js Authors。完整许可见 `thirdparty_licenses/three/LICENSE`；各 JS 头部保留上游声明。

## Python 与原生依赖

| 组件 | 原许可 / 说明 |
|---|---|
| Python | Python Software Foundation License |
| cadquery-ocp / OCP Python 包装 | Apache-2.0；随包可能包含其他原生组件，需同时看各自许可 |
| Open CASCADE Technology (OCCT) | LGPL-2.1，附 Open CASCADE exception；通过动态链接调用 |
| VTK | BSD-3-Clause；打包仅收集 OCP 所需原生 DLL 链 |
| NumPy / SciPy | BSD-3-Clause；OpenBLAS 等子组件见附带声明 |
| Pillow | HPND |
| openpyxl / et_xmlfile | MIT |
| xlrd | BSD |
| pypdf | BSD-3-Clause |
| PyInstaller | GPL-2.0-or-later，附 bootloader exception；例外允许独立许可的应用打包 |
| pefile / altgraph / pywin32-ctypes | 见 `thirdparty_licenses/` 中对应原始许可 |

依赖版本见 `requirements*.txt`，实际构建版本另记录在随发布包提供的 `build-info.json`。随安装包提供的许可原文保存在 `thirdparty_licenses/`；构建时再次收集当前安装版本许可。

OCCT 上游源码及许可：[Open-Cascade-SAS/OCCT](https://github.com/Open-Cascade-SAS/OCCT) · [licensing](https://dev.opencascade.org/resources/licensing)。分发修改过的第三方运行时还须履行对应许可的源码和修改告知义务。
