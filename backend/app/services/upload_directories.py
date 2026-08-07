from app.domain.enums import AssetType

UPLOAD_DIRECTORY_OPTIONS: dict[AssetType, tuple[tuple[str, str], ...]] = {
    AssetType.PAPER: (
        ("manuscript", "正文"),
        ("supplementary", "补充材料"),
        ("source", "源文件"),
        ("reviews", "审稿材料"),
    ),
    AssetType.DATASET: (
        ("raw", "原始数据"),
        ("processed", "处理后数据"),
        ("documentation", "说明文档"),
        ("scripts", "处理脚本"),
    ),
    AssetType.LITERATURE: (
        ("original", "原文"),
        ("annotations", "批注版"),
        ("notes", "阅读笔记"),
    ),
    AssetType.PROJECT: (
        ("documentation", "项目文档"),
        ("code", "源代码"),
        ("data", "项目数据"),
        ("outputs", "产出结果"),
    ),
    AssetType.MODEL: (
        ("weights", "模型权重"),
        ("checkpoints", "训练检查点"),
        ("configs", "配置文件"),
        ("evaluation", "评测结果"),
    ),
}


def upload_directory_names(asset_type: AssetType) -> frozenset[str]:
    return frozenset(name for name, _ in UPLOAD_DIRECTORY_OPTIONS[asset_type])
