"""User-facing explanations for the unified rice-gene workbench."""

from __future__ import annotations


SEQUENCE_AND_RESOURCE_EXPLANATIONS = (
    {
        "module": "RAP/MSU ID 解析与映射",
        "inputs": "RAP gene/transcript ID 或 MSU locus/model ID",
        "data_nature": "注释体系映射",
        "source": "内置 RAP–MSU 对照表",
        "method": "识别 RAP gene/transcript 与 MSU locus/model，去除可安全处理的版本后缀；一对多映射逐条保留，不静默合并。",
        "outputs": "输入 ID 类型、RAP/MSU 对应关系、候选转录本、mapping_count、匹配状态与警告。",
        "boundary": "RAP 与 MSU 是不同注释体系；不能把编号相似当作同一模型。",
    },
    {
        "module": "Gene genomic / CDS / Protein",
        "inputs": "已解析的 RAP/MSU gene 或 transcript ID",
        "data_nature": "参考序列与注释记录",
        "source": "内置 RAP-DB/IRGSP-1.0 FASTA；MSU 输入可补充查询 RGAP",
        "method": "按精确 gene/transcript ID 提取 genomic 与 CDS；Protein 可直接取得，或由通过校验的 CDS 翻译。",
        "outputs": "FASTA、序列长度、来源、assembly、转录本 ID、CDS→protein 一致性与异常说明。",
        "boundary": "CDS/蛋白只做精确反查，不用近似 BLAST 猜测来源。",
    },
    {
        "module": "5′UTR / 3′UTR / Promoter",
        "inputs": "gene/transcript ID、所选 transcript 与启动子长度",
        "data_nature": "坐标提取的参考序列",
        "source": "Ensembl REST，Oryza sativa，IRGSP-1.0",
        "method": "先确定 gene、strand 与选定 transcript，再按注释提取 UTR；启动子按 TSS 上游方向截取 500–4000 bp，并检查染色体边界。",
        "outputs": "UTR/启动子 FASTA、坐标、方向、实际长度、transcript、assembly 与截断说明。",
        "boundary": "不同转录本的 UTR/TSS 可不同；启动子是操作性区间，不等于已实验证实的调控区。",
    },
    {
        "module": "RiceData 基因信息",
        "inputs": "RAP 或 MSU 水稻基因 ID",
        "data_nature": "数据库整理的已有知识",
        "source": "国家水稻数据中心 RiceData 基因数据库",
        "method": "按 ID 查询基础标识；完整模式进一步解析突变体表型、定位与克隆、时空表达、亚细胞定位、生物学功能及关联文献。",
        "outputs": "基因名/符号、多数据库 ID、功能字段、reference ID/DOI、status/error 与来源链接。",
        "boundary": "数据库文字属于已有记录；空字段也可能是页面未返回，须结合 status/error 判断。",
    },
)


PREDICTOR_EXPLANATIONS = (
    {
        "module": "SignalP 6.0",
        "inputs": "蛋白质 FASTA",
        "data_nature": "计算预测",
        "source": "DTU；失败时可降级至 BioLib",
        "method": "分析蛋白 N 端是否具有经典分泌信号肽，并解析类别、切割位点及其坐标。",
        "outputs": "SP/非 SP 分类、切割位点、预测区段、原始结果和服务状态。",
        "boundary": "支持经典分泌候选判断，不证明蛋白已分泌，也不覆盖所有非经典分泌途径。",
    },
    {
        "module": "TMHMM 2.0",
        "inputs": "蛋白质 FASTA",
        "data_nature": "计算预测",
        "source": "DTU Health Tech",
        "method": "根据蛋白序列预测跨膜螺旋及 inside/outside 拓扑区段。",
        "outputs": "跨膜螺旋数、起止坐标、拓扑分类与原始结果。",
        "boundary": "疏水信号肽与跨膜区可能混淆，应与 SignalP/DeepTMHMM 联合判断。",
    },
    {
        "module": "DeepTMHMM 1.0",
        "inputs": "蛋白质 FASTA",
        "data_nature": "计算预测",
        "source": "DTU；失败时可降级至 BioLib",
        "method": "用深度学习模型联合预测跨膜拓扑、signal peptide 及蛋白区段标签。",
        "outputs": "蛋白类别、逐区段拓扑、跨膜/信号肽坐标与服务状态。",
        "boundary": "属于计算预测；与 TMHMM 不一致时应检查 N 端信号肽和序列质量。",
    },
    {
        "module": "TargetP 2.0",
        "inputs": "蛋白质 FASTA（Plant 模式）",
        "data_nature": "计算预测",
        "source": "DTU Health Tech，Plant 模式",
        "method": "分析 N 端靶向肽，区分叶绿体、线粒体、分泌途径或其他类别。",
        "outputs": "定位类别、概率/分值、候选切割位点与原始结果。",
        "boundary": "反映 N 端靶向倾向，不等同于细胞内实验证据。",
    },
    {
        "module": "cNLS Mapper",
        "inputs": "蛋白质 FASTA 与 cutoff",
        "data_nature": "计算预测",
        "source": "Keio University cNLS Mapper",
        "method": "按设定 cutoff 搜索经典 importin-α/β 型核定位信号。",
        "outputs": "是否检出 cNLS、最高分值、候选 NLS 区段与序列。",
        "boundary": "未检出不排除非经典 NLS、伴侣蛋白介导入核或条件依赖定位。",
    },
    {
        "module": "NLStradamus 1.8",
        "inputs": "蛋白质 FASTA、HMM 模型与 cutoff",
        "data_nature": "计算预测",
        "source": "APP 内置本地程序",
        "method": "用 two-state 或 four-state HMM 计算逐位点 NLS posterior，并按 cutoff 合并候选区段。",
        "outputs": "NLS 区段、坐标、posterior 分值与使用的模型/阈值。",
        "boundary": "与 cNLS Mapper 算法不同；两者一致可增强计算支持，仍需定位实验验证。",
    },
)


DEEP_ANALYSIS_EXPLANATIONS = (
    {
        "module": "蛋白结构域与功能位点",
        "inputs": "蛋白质序列或可精确匹配的蛋白记录",
        "data_nature": "数据库匹配与功能推断",
        "source": "InterPro / InterProScan matches API",
        "method": "将蛋白序列或可匹配的蛋白记录提交/查询 InterPro，整合成员数据库结构域、家族与功能位点并去重绘图。",
        "outputs": "结构域名称、数据库、accession、起止坐标、功能位点、原始结果与整合轨道图。",
        "boundary": "结构域支持功能推断，不自动证明具体底物、酶活或互作对象。",
    },
    {
        "module": "基因结构与转录本可视化",
        "inputs": "精确 RAP gene ID",
        "data_nature": "参考注释模型",
        "source": "Ensembl REST / IRGSP-1.0",
        "method": "按精确 RAP gene 获取 transcript、exon、CDS、UTR 和 strand，统一转换为 5′→3′ 视图。",
        "outputs": "转录本模型、exon/CDS/UTR 坐标、长度、canonical 状态与结构比较图。",
        "boundary": "只展示当前注释模型；不同数据库或版本可能给出不同转录本边界。",
    },
    {
        "module": "启动子与候选上游调控",
        "inputs": "所选启动子序列、p-value 阈值与物种参数",
        "data_nature": "motif 扫描预测",
        "source": "本工具提取的 promoter + PlantRegMap",
        "method": "对所选启动子进行 motif-based TFBS 扫描，按 p-value 过滤并汇总候选 TF/TF family。",
        "outputs": "motif/TF、相对 TSS 坐标、链方向、p-value、候选上游 TF 与 TFBS 图。",
        "boundary": "motif 命中是计算预测；不能替代 TF 表达共现、ChIP、Y1H、EMSA 或遗传验证。",
    },
    {
        "module": "自然变异与单倍型",
        "inputs": "gene 坐标；可选 VCF 与样本分组表",
        "data_nature": "变异注释与样本统计",
        "source": "用户 VCF/样本分组表；无上传时尝试 RiceVarMap v3",
        "method": "将变异定位到 promoter/UTR/CDS/intron 等区域；上传 VCF 时校验 REF、过滤缺失率和 MAF，并按样本基因型组合汇总单倍型。",
        "outputs": "位点、REF/ALT、区域/影响注释、过滤状态、单倍型、频率、样本和可选亚群统计。",
        "boundary": "数据库网页不可解析或 VCF 不合格时保留警告，不伪造变异/单倍型；关联不等于因果。",
    },
    {
        "module": "miRNA/RNAi 分析",
        "inputs": "目标 transcript；可选自定义 miRNA/siRNA FASTA",
        "data_nature": "small RNA–target 计算预测",
        "source": "psRNATarget；用户自定义 small-RNA FASTA 可选",
        "method": "把目标 transcript 与已知水稻 miRNA 或自定义 miRNA/siRNA 提交 psRNATarget，解析互补区、expectation、UPE 和抑制方式。",
        "outputs": "small RNA–target 配对、靶位点、alignment、expectation、UPE、任务 URL 和可用脱靶状态。",
        "boundary": "均为计算预测；需小 RNA 表达、降解组/RLM-RACE、报告基因或沉默实验验证。",
    },
    {
        "module": "文献与已知遗传证据",
        "inputs": "gene ID/符号；可选人工 CSV/XLSX 证据表",
        "data_nature": "文献元数据与已知证据索引",
        "source": "RiceData、RAP-DB、Europe PMC；可导入人工 CSV/XLSX",
        "method": "用精确 gene ID/符号检索元数据，提取 knockout、mutation、QTL/GWAS、interaction 等标签，并把 RiceData 证据文字与关联引用分开记录。",
        "outputs": "证据描述、论文题目、PMID/DOI、匹配字段、证据标签、来源、核验状态与人工证据。",
        "boundary": "在线检索只是初筛；标题/摘要命中和数据库关联文献都必须核对全文后才能判定直接证据。",
    },
)


WORKFLOW_EXPLANATIONS = (
    {
        "module": "后台任务队列与失败隔离",
        "inputs": "已校验的分析请求、模块选择与参数快照",
        "data_nature": "本地工作流状态",
        "source": "APP 内置 AnalysisJobManager / ProgressReporter",
        "method": "每次提交生成独立 job ID，按阶段记录进度、成功、警告与失败；单个外部服务失败只结束该模块，其他模块继续。",
        "outputs": "job ID、排队/运行/完成状态、分阶段进度、status/error、警告、取消与重试入口。",
        "boundary": "任务在本机 APP 进程中运行；切换页面不中断，但强制退出 APP 或系统终止进程时不应假定任务仍会继续。",
    },
    {
        "module": "序列关系与 CDS→Protein 一致性检查",
        "inputs": "输入 ID/序列、RAP/MSU 映射、genomic/CDS/protein/UTR/promoter 记录",
        "data_nature": "精确匹配与本地校验",
        "source": "内置 IRGSP-1.0 序列、RGAP/Ensembl 结果与用户输入",
        "method": "把输入、RAP/MSU、gene/transcript 和六类序列串成可追溯关系；对通过质控的 CDS 翻译后与已选蛋白做精确一致性检查。",
        "outputs": "映射/序列关系图、CDS→protein 一致/不一致状态、SVG/PDF/600 dpi PNG、绘图数据 CSV 与警告。",
        "boundary": "精确一致性检查不是 BLAST/同源性推断；不同注释版本、转录本或起始位点会导致不一致，需回到来源复核。",
    },
    {
        "module": "证据分层与综合判断",
        "inputs": "RiceData/文献记录、eFP 表达、序列/结构、计算预测、变异与人工证据",
        "data_nature": "多来源证据整合",
        "source": "本次任务各模块的可追溯结果",
        "method": "把数据库已有记录、表达定量、计算预测和人工证据分开展示，保留来源、status/error 和可能改变解释的警告。",
        "outputs": "基因概览、已知证据、表达、序列/结构、调控/变异、结论/来源六个视图，以及风险提示和后续验证线索。",
        "boundary": "APP 整合证据并提示优先级，但不会把相关性、motif 或定位预测自动升格为因果机制。",
    },
    {
        "module": "Word / Excel / ZIP 报告与复现包",
        "inputs": "完整 AnalysisBundle、图形、原始表、参数、来源与警告",
        "data_nature": "可追溯交付物",
        "source": "APP 内置 report_builder",
        "method": "将关键结论、方法、图形和风险写入 Word，将完整明细分 sheet 写入 Excel，再把 FASTA、原始结果、SVG/PDF/PNG、CSV 和 manifest 打包为 ZIP。",
        "outputs": "中文华文仿宋/西文 Times New Roman 的 Word、多 sheet Excel、完整 ZIP、参数与版本 manifest、原始绘图数据。",
        "boundary": "Word 是摘要层，不代替 Excel/原始表；定稿前仍应核对失败服务、缺失值、参数、注释版本与全文证据。",
    },
)


def explanation_rows(rows: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    """Return Chinese-column rows ready for Streamlit or spreadsheet display."""
    return [
        {
            "模块": row["module"],
            "数据/来源": row["source"],
            "APP 怎么做": row["method"],
            "获得的数据": row["outputs"],
            "解读边界": row["boundary"],
        }
        for row in rows
    ]


__all__ = [
    "DEEP_ANALYSIS_EXPLANATIONS",
    "PREDICTOR_EXPLANATIONS",
    "SEQUENCE_AND_RESOURCE_EXPLANATIONS",
    "WORKFLOW_EXPLANATIONS",
    "explanation_rows",
]
