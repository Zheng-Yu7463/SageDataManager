from enum import StrEnum


class ActivityAction(StrEnum):
    CREATED = "created"
    UPDATED_METADATA = "updated_metadata"
    PREPARED_UPLOAD = "prepared_upload"
    COMPLETED_UPLOAD = "completed_upload"
    CREATED_ACCESS_TOKEN = "created_access_token"
    REVOKED_ACCESS_TOKEN = "revoked_access_token"
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
    INITIALIZED_INSTANCE = "initialized_instance"
    ISSUED_ACCOUNT_INVITATION = "issued_account_invitation"
    ISSUED_ACCOUNT_RECOVERY = "issued_account_recovery"
    REGISTERED_ACCOUNT = "registered_account"
    RESET_ACCOUNT_PASSWORD = "reset_account_password"
    UPDATED_ACCOUNT = "updated_account"


class ActivityOperationRole(StrEnum):
    SINGLE = "single"
    SOURCE = "source"
    TARGET = "target"


ACTIVITY_LABELS = {
    ActivityAction.CREATED: "登记资产",
    ActivityAction.UPDATED_METADATA: "更新元数据",
    ActivityAction.PREPARED_UPLOAD: "生成上传指令",
    ActivityAction.COMPLETED_UPLOAD: "完成文件上传",
    ActivityAction.CREATED_ACCESS_TOKEN: "创建 AI 访问令牌",
    ActivityAction.REVOKED_ACCESS_TOKEN: "撤销 AI 访问令牌",
    ActivityAction.ARCHIVED: "归档资产",
    ActivityAction.RESTORED: "恢复资产",
    ActivityAction.ADDED_VERSION: "登记版本",
    ActivityAction.LINKED_ASSET: "建立关联",
    ActivityAction.UNLINKED_ASSET: "解除关联",
    ActivityAction.CLAIMED_FILE: "认领文件",
    ActivityAction.PREVIEWED_FILE: "预览文件",
    ActivityAction.DOWNLOADED_FILE: "下载文件",
    ActivityAction.UPDATED_BRANDING: "更新品牌设置",
    ActivityAction.IMPORTED_PUBLICATION: "收录文献",
    ActivityAction.INITIALIZED_INSTANCE: "初始化实例所有者",
    ActivityAction.ISSUED_ACCOUNT_INVITATION: "生成管理员注册链接",
    ActivityAction.ISSUED_ACCOUNT_RECOVERY: "生成账号恢复链接",
    ActivityAction.REGISTERED_ACCOUNT: "完成管理员注册",
    ActivityAction.RESET_ACCOUNT_PASSWORD: "重置管理员密码",
    ActivityAction.UPDATED_ACCOUNT: "更新管理员账号",
}


def activity_label(action: str) -> str:
    try:
        return ACTIVITY_LABELS[ActivityAction(action)]
    except ValueError:
        return f"其他操作（{action.replace('_', ' ')}）"
