# 数据库 ER 图

图中使用 Django 模型名。实体和关系以 `catalog/models.py` 和数据库迁移为准。新增字段或改变关系时，在同一个改动中更新这张图。

```mermaid
erDiagram
    USER ||..o{ EDITION : "拥有"
    PUBLIC_EDITION |o..o{ EDITION : "提供出版资料"
    EDITION ||..o{ COPY : "包含实体册"
    USER ||..o{ TAG : "拥有"
    EDITION ||..o{ EDITION_TAG : "使用标签"
    TAG ||..o{ EDITION_TAG : "标记图书"
    USER ||..o{ LOGIN_IDENTITY : "绑定登录方式"
    USER ||..o{ MOBILE_TOKEN : "登录授权"
    USER ||..o{ MOBILE_SAVE_REQUEST : "提交录入"
    COPY |o..o{ MOBILE_SAVE_REQUEST : "录入结果"

    USER {
        int id PK
        string username UK
    }
    PUBLIC_EDITION {
        bigint id PK
        string isbn UK "规范化的 ISBN-13"
        string title
        string author
        string publisher
        int published_year
        string cover_url
        string source
        datetime created_at
    }
    EDITION {
        bigint id PK
        int owner_id FK
        bigint public_edition_id FK "可空"
        string isbn "与 owner_id 联合唯一，可空"
        string title
        string author
        string publisher
        int published_year
        string cover_url
        string cover_object_key
        datetime created_at
        datetime updated_at
    }
    COPY {
        bigint id PK
        bigint edition_id FK
        string location
        string status
        date acquired_on
        text notes
        datetime created_at
    }
    TAG {
        bigint id PK
        int owner_id FK
        string name "与 owner_id 联合唯一"
    }
    EDITION_TAG {
        bigint id PK
        bigint edition_id FK
        bigint tag_id FK "与 edition_id 联合唯一"
    }
    LOGIN_IDENTITY {
        bigint id PK
        int user_id FK
        string provider "与 user_id 联合唯一"
        string subject "与 provider 联合唯一"
        text encrypted_refresh_token
    }
    EMAIL_LOGIN_CHALLENGE {
        string key PK "邮箱哈希"
        string digest "验证码摘要"
        datetime sent_at
        int attempts
    }
    AUTH_RATE_WINDOW {
        string key PK
        datetime started_at
        int count
    }
    MOBILE_TOKEN {
        bigint id PK
        int user_id FK
        string digest UK
        datetime created_at
        datetime expires_at
    }
    MOBILE_SAVE_REQUEST {
        bigint id PK
        int user_id FK
        uuid request_id "与 user_id 联合唯一"
        bigint copy_id FK "可空"
        datetime created_at
    }
```

`PUBLIC_EDITION` 保存外部来源提供的出版资料。网页版和 iOS 扫码在当前书库没有可用资料时，按 ISBN 查询这张表，再尝试外部来源。只收录有书名和来源的 ISBN 图书。无 ISBN 图书和 `977` 期刊条码继续沿用现有录入方式。

`EDITION` 保留账号内使用的资料和用户修改。扫码保存时把补全的书目填进个人记录，现有 iOS 同步、封面处理、排序和导出继续使用这份数据。外键记录对应的公共书目，个人版本的字段修改只影响该账号。删除公共书目会解除关联，个人藏书仍可使用。

迁移 `0010` 添加公共表和可空外键。已有账号的书目、标签等内容仍留在原书库。公共表随查询积累，可以在生产环境迁移后按需执行 `python manage.py link_public_editions`，将已存在的同 ISBN 公共书目关联到旧记录。命令可以重复执行，只更新外键。
