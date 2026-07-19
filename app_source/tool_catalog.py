"""Central catalog for the visible My Bio Tools modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    module: str
    icon: str
    description: str
    method: str = ""
    outputs: str = ""
    website_name: str | None = None
    website_url: str | None = None
    inputs: str = ""
    cautions: str = ""

    @property
    def requires_internet(self) -> bool:
        return self.website_url is not None


TOOL_GROUPS: dict[str, list[ToolDefinition]] = {
    "概览": [
        ToolDefinition("工作台首页", "welcome", "⌂", "查看全部模块的用途、联网要求与快速使用指南"),
        ToolDefinition(
            "方法与数据说明中心",
            "methods_guide",
            "≡",
            "搜索所有工具、一站式内部模块和 12 个 eFP 数据源的方法与解读边界",
            "汇总 APP 内已维护的方法字典，按类别和关键词筛选并展示统一字段",
            "输入、数据来源、APP 处理步骤、获得的数据、证据性质、解读限制与可下载 CSV",
            inputs="可选类别和搜索关键词",
            cautions="本页是方法与解读指南，不代替原始数据、数据库原页、论文全文或实验验证。",
        ),
    ],
    "生信小工具": [
        ToolDefinition("DNA 组成与质量检查", "tool_a", "◒", "统计序列长度、GC、N、模糊碱基和非法字符", "解析单条 DNA 或多序列 FASTA，将 U 统一为 T，并按 IUPAC DNA 规则逐条质控", "汇总指标、逐序列表、规范化 FASTA、通过字符检查序列的反向互补 FASTA", inputs="纯 DNA 文本，或包含一条/多条记录的 FASTA 文本", cautions="GC% 以 A/C/G/T 有效碱基为分母；含非法字符的记录不会生成反向互补，模糊碱基与非法字符需要结合原始测序质量判断。"),
        ToolDefinition("Primer3 引物设计", "primer_design", "⌁", "为 DNA 模板设计 PCR 引物，并输出 Tm、GC 和产物长度", "清理 FASTA 标题与空白字符，校验模板后按 Tm、GC、产物区间、候选区域等约束运行 Primer3", "成对引物、模板位置、Tm、GC、产物长度、penalty 与 CSV", inputs="单条 DNA 模板，可直接粘贴纯序列或单条 FASTA；可设置目标区与排除区", cautions="候选位置采用 0-based 坐标。Primer3 只评价序列与热力学约束，不替代全基因组特异性比对、二聚体复核和湿实验验证。"),
        ToolDefinition("FASTA 序列提取", "extract_fasta", "⌕", "按 ID 从普通或 gzip 压缩 FASTA 中提取指定序列", "逐条流式扫描 FASTA，以标题第一个 ID 字段做精确匹配，不把整库常驻内存", "提取后的 FASTA、匹配/扫描统计及未匹配 ID 清单", inputs="FA/FASTA/FNA/FAA 或 GZ 文件，以及直接粘贴或上传的 ID 列表", cautions="默认精确匹配标题的第一个 ID；“忽略点号版本”只处理 .1/.2 等版本后缀，不会去掉水稻 -01 转录本后缀。"),
        ToolDefinition("FASTA ID 重命名", "fasta_rename", "✎", "根据两列对应表批量替换 FASTA 序列 ID，并报告未匹配项", "读取 old ID–new ID 两列表，仅替换 FASTA 标题首个 ID，并原样保留其后的描述", "新 FASTA、成功/未匹配统计、无效行与重复映射警告", inputs="FASTA/FA/FNA/FAA/TXT 或 GZ 文件，加两列 TXT/TSV/CSV 对应表", cautions="对应表中的重复旧 ID 使用最后一次映射；未匹配记录会保留原 ID，不会被删除，下载前应先复核警告。"),
        ToolDefinition("RAP ↔ MSU ID 转换", "RAP_MSU_convert", "⇄", "在 RAP 与 MSU 两套水稻基因 ID 之间转换，并保留一对多映射", "识别混合 RAP/MSU 输入并查询内置 IRGSP-1.0 对照表；MSU 模型版本后缀单独记录", "input、input_type、converted、mapping_count、status、note 与 CSV；一对多关系不丢失", inputs="RAP gene ID 或 MSU locus/model ID，可换行、空格、逗号或分号混合输入", cautions="ID 转换表示注释体系映射，不等同于功能同源推断；Unknown 与 Unmapped 不会被强制猜测，一对多结果需结合转录本版本复核。"),
    ],
    "RiceData 基因信息批量检索": [
        ToolDefinition(
            "RiceData 信息检索",
            "RiceData_crawler",
            "◎",
            "联网批量整理水稻基因名称、数据库 ID 与功能描述",
            "按输入 ID 查询 RiceData；完整模式继续解析功能分栏与关联文献",
            "基因名称/符号、多数据库 ID、功能证据、reference、status/error 与 CSV",
            "国家水稻数据中心 · 基因数据库",
            "https://www.ricedata.cn/gene/",
            inputs="RAP 或 MSU 水稻基因 ID；支持换行批量输入或上传 TXT",
            cautions="网页结构、网络和限流可能导致部分字段为空；空白不能直接解释为“无注释”。功能描述和关联文献应回到原始记录或全文复核。",
        ),
        ToolDefinition(
            "水稻基因一站式分析",
            "rice_gene_analysis",
            "⌬",
            "后台队列整合 RiceData、Rice eFP、六类序列与蛋白定位预测",
            "先统一 ID/assembly，再按用户勾选并行调用数据源与预测服务，最后汇总证据链",
            "交互结果、Word、Excel、ZIP、FASTA、原始表、SVG/600 dpi PNG 和来源/警告",
            "RiceData / Rice eFP / IRGSP / RGAP / Ensembl / DTU",
            "https://services.healthtech.dtu.dk/",
            inputs="单个或批量 RAP/MSU ID、CDS FASTA 或蛋白 FASTA，并可上传 VCF、样本分组和人工证据表",
            cautions="数据库记录、表达定量、计算预测和人工证据属于不同证据等级；外站失败会保留 status/error。跨 eFP 数据源的绝对值不可直接比较，关键结论需实验验证。",
        ),
    ],
}


TOOLS_BY_MODULE = {
    tool.module: tool
    for definitions in TOOL_GROUPS.values()
    for tool in definitions
}


def functional_tools() -> list[ToolDefinition]:
    """Return the real tools, excluding the dashboard page."""
    return [
        tool
        for tool in TOOLS_BY_MODULE.values()
        if tool.module not in {"welcome", "methods_guide"}
    ]
