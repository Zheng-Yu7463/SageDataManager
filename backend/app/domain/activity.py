from enum import StrEnum


class ActivityAction(StrEnum):
    CREATED = "created"
    UPDATED_METADATA = "updated_metadata"
    PREPARED_UPLOAD = "prepared_upload"
    ARCHIVED = "archived"
    RESTORED = "restored"
    ADDED_VERSION = "added_version"
    LINKED_ASSET = "linked_asset"
    UNLINKED_ASSET = "unlinked_asset"
    CLAIMED_FILE = "claimed_file"
    PREVIEWED_FILE = "previewed_file"
    DOWNLOADED_FILE = "downloaded_file"
    UPDATED_BRANDING = "updated_branding"
    IMPORTED_PUBLICATION = "imported_publication"


ACTIVITY_LABELS = {
    ActivityAction.CREATED: "登记资产",
    ActivityAction.UPDATED_METADATA: "更新元数据",
    ActivityAction.PREPARED_UPLOAD: "生成上传指令",
    ActivityAction.ARCHIVED: "归档资产",
    ActivityAction.RESTORED: "恢复资产",
    ActivityAction.ADDED_VERSION: "登记版本",
    ActivityAction.LINKED_ASSET: "建立关联",
    ActivityAction.UNLINKED_ASSET: "解除关联",
    ActivityAction.CLAIMED_FILE: "认领文件",
    ActivityAction.PREVIEWED_FILE: "预览文件",
    ActivityAction.DOWNLOADED_FILE: "下载文件",
    ActivityAction.UPDATED_BRANDING: "更新品牌设置",
    ActivityAction.IMPORTED_PUBLICATION: "收录论文",
}


def activity_label(action: str) -> str:
    try:
        return ACTIVITY_LABELS[ActivityAction(action)]
    except ValueError:
        return f"其他操作（{action.replace('_', ' ')}）"
