# My Bio Tools

“我的 Python 小工具合集”的独立桌面 App。macOS 与 Windows 版共用 Python、Streamlit、第三方依赖与实际使用的水稻注释数据，接收者无需安装 Python 或 Conda。

当前 macOS 功能基线为 **1.9.8（build 27）**，Windows 功能基线仍为 **1.9.7（build 26）**。macOS 目标平台为 Apple Silicon（arm64），最低支持 macOS 13；Windows 目标平台为 Windows 10/11 x64，并通过原生 Windows GitHub Actions 构建和验收。

v1.9.8 重做普通 Word 与 AI 深度解读 Word：多组学按“差异统计、仅定量观察、论文证据”分层完整展示；热图只使用来源数据已有 log2FC，缺失统计量显示为 NA；病毒与昆虫响应图保留每个项目的生物学重复、均值 ± SD、原始单位和独立尺度。图形标签改为简短的“处理 + 品种/背景 + 时间”，完整 accession 和数据集名称移入图注及表格。AI 报告改用中文科研综述体，程序依据已核验证据生成作者—年份引文，模型失败时仍输出同版式的证据整理报告。

v1.9.7 在“大模型增强解读”中新增 DeepSeek、豆包（火山方舟）、智谱 GLM、通义千问（阿里云百炼）和 ChatAnywhere API 预设，并继续保留 ChatGPT/Codex、本机 Ollama 与自定义 OpenAI-compatible API。云端地址和模型名称可编辑；每个提供方使用独立 API Key 输入框和发送确认。API Key 仅存在于当前 APP 会话内存，退出后需要重新输入，不写入偏好文件、报告、日志或发布物。连接测试只发送固定文本；正式解读只发送去标识化结构化证据，失败时自动回退离线科研规则。

v1.9.6 将实验室多组学扩展为可统计的主分析与独立论文证据层，统一处理处理/对照方向、重复、显著性、PTM 与总蛋白边界，并把机制证据、AI 综合解读及追溯信息同步进入网页、Word、Excel 和 ZIP。加密数据包不进入 Git 源码，只随受控安装包分发。

v1.9.5 新增账号授权期限、到期状态和续期管理，并允许未登录用户直接使用不依赖授权数据的基础生信小工具；水稻专用与加密组学工作区仍需登录授权。

v1.9.1 在“大模型增强解读”中新增“ChatGPT 账号（Codex，免 API Key）”：macOS 优先复用新版 ChatGPT App 内置 Codex，Windows 检测用户按 OpenAI 官方方式安装的 Codex CLI。现在可选择自动推荐或 GPT-5.6 Sol/Terra/Luna、GPT-5.5、GPT-5.2，并按模型选择推理档位和标准/快速响应；界面会自动排除模型不支持的组合。快速模式约提升 1.5 倍响应速度，并消耗更多 ChatGPT/Codex 额度。每次任务均需确认把去标识化结构化摘要发送给 OpenAI；调用采用临时只读会话、严格 JSON Schema，并禁用插件、记忆、浏览器、终端、Computer Use 和多代理能力。该模式不创建持久 Codex 任务，不读取项目文件，不自动改用 API；未安装、未登录、额度不足、超时或返回格式异常时保留离线规则解读。使用量计入 ChatGPT/Codex 共享额度。

v1.9.0 新增账号页“一键更新并重启”：登录后检查 Ed25519 签名更新清单，由授权服务从 GitHub Release 代取 DMG，客户端不接触 GitHub 凭据；随后校验 SHA-256、文件大小、bundle ID、版本/build、arm64 架构与代码签名完整性，替换前保留可回滚备份。水稻基因一站式分析同时新增“离线科研规则”与“大模型增强”两种可选结果解读，重点解释多组学方向/跨层一致性/PTM 边界和单倍型频率/群体分层/性状关联缺口。解读同步进入网页、Word、Excel 和 ZIP；大模型失败时自动回退到离线规则。

v1.8.0 在原“水稻基因一站式分析”内增加实验室已分析多组学区，统一以去 model 后缀的 MSU locus 检索，同时保留 MSU model、RAP gene/model 和原始 ID。首版整合野生型/感性背景内的病毒、褐飞虱、白背飞虱与电光叶蝉处理数据，覆盖 mRNA、总蛋白、磷酸化、泛素化和历史芯片。支持单基因明细、批量热图、项目内定量图以及 Word/Excel/ZIP/SVG/PDF/600 dpi PNG 导出；加密只读数据库在登录授权后才解锁。数据库密钥不嵌入 APP，只通过服务端 Ed25519 签名离线授权向已批准设备下发。build 18 修复了线上 v1.7.0 授权数据与 v1.8.0 客户端不兼容时的模糊解码提示，并新增“记住账号和密码”；密码仅保存于当前 Mac 的系统 Keychain，关闭选项后立即清除。

v1.7.2 在 APP 内补齐可追溯的数据源与模块解释：新增可搜索的“方法与数据说明中心”，统一汇总 7 个独立工具、16 个一站式内部分析、4 个工作流/报告模块和 12 个 Rice eFP 数据源，共 39 项；逐项给出输入、数据性质、来源、APP 处理方法、获得的数据与解读边界，并可导出 CSV。Rice eFP 的 12 个数据源另逐项说明组织/处理、实验设计、数值尺度、提交 ID、官方 GEO/论文来源和重复/汇总结构。每个独立工具页面均保留统一的“输入—APP 怎么做—获得的数据—解读与限制”说明区；eFP 词典同步进入设置页、结果页、Word 附录、Excel 与 ZIP。官网原始表完整保留，Top 汇总和图形对完全重复记录去重，并明确 SD=0 不代表没有生物学变异。

v1.7.1 按科研证据链重构一站式报告与网页：正文统一为“基因身份 → 已有证据 → 表达 → 序列与结构 → 调控与变异 → 综合判断”，网页收敛为 6 个标签；新增 RiceData 遗传证据—关联论文映射、Single-cell eFP 的 RAP ID 路由，以及序列关系图的 SVG/PDF/600 dpi PNG 和绘图数据 CSV。无结果模块只显示状态卡，完整明细进入 Excel/ZIP/附录。

v1.7.0 新增课题组账号门禁：邮箱注册与验证、管理员审核、账号停用/恢复、最多 2 台设备、7 天 Ed25519 签名离线授权，并在 macOS/Windows 原生外壳与 Python 后端同时执行授权校验。账号服务不上传科研输入或分析结果。

v1.6.1 将新版 Word 报告设为默认导出版式，并统一升级报告内全部科研图：Rice eFP 表达谱、蛋白定位/结构域、基因结构与转录本、启动子 TFBS、自然变异与单倍型均采用一致的论文级字体、色板、图例和页面宽度适配；内部文件名不再直接作为 Word 图题。

v1.6.0 在原有 RAP/MSU 映射、IRGSP-1.0 序列、RiceData、Rice eFP 和蛋白定位预测基础上，新增六个可独立选择的深度模块：蛋白结构域/功能位点、基因结构/转录本、启动子/候选上游 TF、自然变异/单倍型、miRNA/RNAi、文献/已知遗传证据。外部服务失败会保留状态并转为警告，不中断其他模块与报告生成。

## 功能

- DNA 组成与质量检查：多序列 FASTA 的长度、GC、N、模糊碱基、非法字符与反向互补。
- Primer3 引物设计：输入规范化、参数校验、成对引物及质量指标导出。
- FASTA 提取与重命名：支持普通/压缩 FASTA、映射检查与缺失项报告。
- RiceData 信息检索：默认快速模式、可选完整功能信息、有限并发、请求重试、成功结果短时缓存和失败原因保留。
- RAP ↔ MSU 转换：支持混合输入、版本号和一对多映射。
- 水稻基因一站式分析：支持 RAP/MSU ID、CDS FASTA 和 Protein FASTA；单独选择 gene genomic、CDS、protein、5′UTR、3′UTR、500–4000 bp promoter，并保留一对多映射与 transcript/model 边界。
- 后台分析队列：每次提交形成独立项目和进度条；APP 保持打开时，切换工具、刷新页面或最小化不会中断任务。多个项目按单任务队列运行。
- RiceData + Rice eFP：一站式报告内直接整合 RiceData 注释与 BAR Rice eFP Absolute 定量表达谱；默认使用 `rice_rma` 和 `ricestress_rma`，生成带 SD 的柱状图、批量热图、SVG 和 600 dpi PNG。
- 实验室多组学：单基因查询按病毒/昆虫、组学、时间和数据集展示已有结果；批量查询生成基因×处理热图。跨项目仅使用来源表已有 log2FC，项目内仅使用来源表已有 FPKM/TPM/count/归一化蛋白定量，不把不同组学原始数值混合比较。每次分析自动生成证据依据、置信度、解读边界与建议实验。
- 结果解读：用户可选默认离线科研规则、ChatGPT 账号登录的 Codex、本机 Ollama、DeepSeek、豆包、智谱 GLM、通义千问、ChatAnywhere 或自定义 OpenAI-compatible API。API Key 仅在当前会话使用；云端模式需每次明确同意，只发送去标识化结构化证据，不发送原始序列、图片、样本名、密码、令牌、密钥或源文件路径。AI 内容始终标记为“辅助推断·待人工核验”。
- 软件更新：账号页可检查新版本、查看发布说明，并一键下载、安全校验、备份、替换和重启。
- 数据与模块解释：概览区提供“方法与数据说明中心”，可按类别/关键词检索 39 项说明并导出 CSV；每个独立工具页面同时说明输入、处理方法、所得数据和解读限制。一站式分析设置区逐项说明 eFP 12 个数据源的组织/处理、实验设计、尺度、ID、官方来源、重复结构、适用问题、所得数据和解读边界，同时解释序列/RiceData、6 个蛋白定位工具、6 项深度模块与 4 项工作流/报告模块。eFP 词典同步进入结果页、Word 附录、Excel 与 ZIP。
- 蛋白定位预测：可独立选择 SignalP 6.0、TMHMM 2.0、DeepTMHMM 1.0、TargetP 2.0、cNLS Mapper 和本地 NLStradamus 1.8。SignalP/DeepTMHMM 优先访问 DTU，失败或超时后自动降级到 BioLib；综合图使用“工具—序列轨道—结果解释”三栏布局，以状态徽标区分未检出、已检出、服务失败和未解析，不在图中铺陈原始错误文本；输出 SVG 与 600 dpi PNG。
- 报告可视化：蛋白结构域按数据库绘制整合轨道；基因结构按 5′→3′ 比较 exon/CDS/UTR；启动子同时显示 TSS 相对位置和 TF family 丰度；变异/单倍型同时显示位点分布、区域统计、单倍型频率及可选群体热图。Word 正文只保留图形、摘要和 Top 记录，完整明细留在 Excel/ZIP。
- BioLib 默认匿名运行；需要关联账户时可在启动 APP 前设置 `BIOLIB_TOKEN`。Token 不会进入界面、日志、报告或 manifest。
- 分析交付：APP 内“总览、已知证据、表达、序列与结构、调控与变异、结论与来源”6 个结果标签，同时生成 Word、Excel 与完整 ZIP；ZIP 包含注释、eFP 原始表/图、六类 FASTA、序列关系图、文献映射、预测原始结果、参数和版本清单。

统一水稻分析工具保留三套内置 IRGSP FASTA 与 RAP–MSU 对照表作为后端，并采用 `IRGSP-1.0` 兼容坐标。CDS/蛋白只做精确反查，不用近似 BLAST 猜测来源；注释版本不一致时停止混用坐标并给出警告。

Word 报告正文默认 10.5 pt、一级标题 16 pt、二级标题 13 pt、报告标题 18 pt；中文在 macOS 写入华文仿宋（STFangsong）、Windows 写入仿宋，西文、数字、ID 和序列写入 Times New Roman，不嵌入字体文件。所有图片写入可访问性替代文本，所有数据表使用固定 Word 几何并重复表头。

APP 首页列出 7 个功能模块。5 个“生信小工具/内置数据”功能可本地运行，2 个联网模块显示可点击来源网址：

- RiceData 信息检索：<https://www.ricedata.cn/gene/>
- 水稻基因一站式分析：<https://services.healthtech.dtu.dk/>（另调用 RiceData、Rice eFP、Rice Genome Annotation Project、Ensembl REST 与 cNLS Mapper）

## 交付物

### macOS

- `dist/My Bio Tools.app`
- `dist/My-Bio-Tools-1.9.8-arm64.dmg`
- `dist/My-Bio-Tools-1.9.8-arm64.dmg.sha256`
- `Resources/AppIcon-1024.png` 与 `Resources/AppIcon.icns`

### Windows

- `dist/windows/My-Bio-Tools-1.9.7-build26-win-x64-setup.exe`
- `dist/windows/My-Bio-Tools-1.9.7-build26-win-x64-portable.zip`
- `dist/windows/SHA256SUMS.txt`
- `dist/windows/version-manifest.json`
- `dist/windows/windows-build-validation.md`
- 完整构建、运行和验收说明见 `windows/README_Windows.md`

## 开发验证

首次构建需要创建项目内隔离环境：

    ./script/bootstrap_build_env.sh

快速验证源码、核心逻辑和所有页面：

    (cd auth-service && npm test)
    swift test
    .build-venv/bin/python script/verify_source.py
    .build-venv/bin/python script/test_backend_license_gate.py
    .build-venv/bin/python script/test_core_functions.py
    .build-venv/bin/python script/test_report_interpretation.py
    .build-venv/bin/python script/test_codex_chatgpt.py
    .build-venv/bin/python script/test_multi_provider_api.py
    .build-venv/bin/python script/test_prediction_adapters.py
    .build-venv/bin/python script/test_streamlit_pages.py
    .build-venv/bin/python script/test_streamlit_workflows.py
    .build-venv/bin/python script/benchmark_core.py
    .build-venv/bin/python script/validate_rgap_live.py
    .build-venv/bin/python script/validate_utr_promoter_live.py
    .build-venv/bin/python script/validate_efp_live.py

完整构建并验证 App 与内置服务：

    ./script/build_and_run.sh --verify

生成并检查 DMG：

    MY_BIO_TOOLS_LICENSE_PUBLIC_JWK='{"kty":"OKP","crv":"Ed25519","x":"..."}' ./script/package_dmg.sh
    ./script/validate_distribution.sh

已经完成整包构建时，可避免重复构建后端：

    SKIP_APP_BUILD=1 ./script/package_dmg.sh

Windows 版必须在 Windows 10/11 x64 环境原生构建：

    powershell -NoProfile -ExecutionPolicy Bypass -File .\script\build_windows.ps1

也可以在 GitHub Actions 中手动运行 `Build and accept Windows 1.9.7 Build 26`。工作流生成安装包和便携版，验证安装、登录界面启动、进程清理和 SHA-256；仅当仓库配置了私有审核账号 secrets 时才执行真实登录与授权多组学运行验收。

## 分发签名

没有 Developer ID Application 证书时，工程使用临时签名，可在本机运行，但其他 Mac 首次打开时会触发 Gatekeeper 警告。拥有证书后：

    SIGN_IDENTITY="Developer ID Application: ..." ./script/package_dmg.sh
    NOTARY_PROFILE="notary-profile" ./script/notarize.sh

## 结构

- `Sources/BioToolsApp/`：SwiftUI 原生窗口、状态栏和 WebKit 容器。
- `auth-service/`：Cloudflare Worker、D1 migrations、邮件、审核后台与授权集成测试。
- `windows/MyBioTools.Windows/`：WPF 原生窗口、WebView2 容器与 Windows 后端生命周期管理。
- `backend/launcher.py`：内置 Streamlit 服务启动器。
- `app_source/`：工具源码、共享 UI 与内置水稻数据。
- `packaging/`：macOS/Windows PyInstaller、App 元数据、许可证和首次打开说明。
- `script/`：macOS/Windows 构建、测试、基准、签名、公证与分发验证入口。
- `backup/`：仅用于本地升级前快照，不纳入 Git 仓库。

## 维护与致谢

- 软件开发：Wu Lab 团队
- 维护：ZhangS
- 致谢：GanP
