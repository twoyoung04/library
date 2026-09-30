# 藏书簿

一个供个人使用的图书管理系统，适合整理数千册实体书。手机页面可扫描 ISBN 条码或上传照片；照片中没有可读条码时，会尝试识别印刷的 ISBN 数字。图书资料从国内外多个来源查询，查不到时可以手动填写。

## 已实现

- ISBN-10 / ISBN-13 校验与统一、摄像头扫码、照片条码识别、照片文字识别
- 图书版本与实体副本分开管理；重复 ISBN 会新增副本
- 书名、作者、ISBN、出版社、标签、位置搜索；分类及阅读状态筛选
- 多级分类、标签、阅读状态、入藏日期、备注
- UTF-8 CSV 导入导出、本机 SQLite 备份、账号登录

## 本机启动

需要 Python 3.10+ 和 Node.js。首次安装：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm install
npm run build
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
```

`npm run build` 会下载约 2 MB 的英文 OCR 数据，并把扫码及 OCR 资源复制到本地静态目录。之后运行：

```bash
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

打开 <http://127.0.0.1:8000/>，使用刚创建的账号登录。未配置书目数据源的密钥也能使用；查询失败时可以手动录入。

## 国内图书数据源

系统会自动查询[无名图书公开接口](https://www.book345.com/mcp)、[NeoDB 公开书目](https://neodb.social/developer/)；商务印书馆 ISBN 段还会查询[出版社官网书目](https://www.cp.com.cn/AdvancedSearch.html)。这些来源无需密钥。所有 ISBN 都优先查询国内书目，未查到时再查 Google Books 和 Open Library。系统只读取书名、作者、出版社、出版年和封面，不使用电子书链接。每次查询都核对返回的 ISBN。商务印书馆官网书目只覆盖该社图书，部分字段可能需要手动补齐。

还可以按需接入两个来源，提高未命中书籍的覆盖率：

- [万维易源 ISBN 图书查询](https://www.showapi.com/apis/isbn-book-1626)：注册后获取 AppKey；官方说明有免费档位和调用限制。
- [聚合数据 ISBN 书号查询](https://www.juhe.cn/docs/api/id/726)：申请该接口的 Key；具体开通条件和费用以服务商页面为准。

在启动命令前，把已申请的密钥放到**服务器环境变量**中，按需设置一个或两个：

```bash
export SHOWAPI_APP_KEY='你的万维易源AppKey'
export JUHE_ISBN_KEY='你的聚合数据Key'
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

不要把密钥写入前端文件或提交到 Git。国内来源按“无名图书 → NeoDB → 商务印书馆（适用时）→ 万维易源 → 聚合数据”依次尝试；前一来源命中后不再调用后续来源。查询结果会缓存 24 小时，未命中结果缓存 1 小时。设置新密钥并重启服务后，旧的未命中缓存不会阻挡新来源。每个来源的覆盖率取决于它自己的书目数据，建议用你手中的一批 ISBN 实测。

## 手机使用

手机访问时需要把服务部署到手机可访问的地址。**实时摄像头扫码需要 HTTPS**；直接通过局域网 HTTP 地址访问时，可以使用“拍照 / 上传图片”或手动输入 ISBN。建议先在本机验证录入流程，再配置带 HTTPS 的私人服务。不要把 Django 开发服务器直接暴露到公网。

## Vercel + Supabase 部署

本机继续使用 SQLite；只要设置 `DATABASE_URL`，Django 就改用 PostgreSQL。Vercel 环境要求同时设置 `DATABASE_URL` 和 `DJANGO_SECRET_KEY`，缺失时会直接报错，避免误用临时 SQLite 或临时密钥。Vercel 自动收集 Django 静态文件；项目的构建命令会先安装 Node 依赖并生成扫码、OCR 资源。无需迁移本机 `db.sqlite3`。

1. 在 Supabase 创建一个**空白项目**。本项目只由 Django 直连 PostgreSQL，不使用 Supabase 客户端或自动生成的 REST/GraphQL 接口，因此在 Supabase 控制台的 **Integrations → Data API** 中关闭 **Enable Data API**。从项目的 **Connect** 面板复制 **Transaction pooler** 连接串（端口 `6543`），供 Vercel 的 `DATABASE_URL` 环境变量使用。密码若含有 `@`、`#` 等特殊字符，须在 URL 中进行百分号编码。连接串只放在服务器环境变量中，切勿提交到 Git；无需把 Supabase API Key 配到本项目。
2. 在项目目录执行 `npx vercel@latest link` 创建并关联 Vercel 项目。为项目的 **Production** 环境设置 `DATABASE_URL` 和 `DJANGO_SECRET_KEY`；后者可用 `python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'` 生成，后续部署中保持不变。按需添加 `SHOWAPI_APP_KEY` 和 `JUHE_ISBN_KEY`。
3. 在项目目录执行 `npx vercel@latest --prod`。Production 构建会自动执行 Django 数据库迁移，Preview 构建不会修改数据库。项目根目录的 `manage.py` 会被自动识别。确认 Vercel 项目已启用系统环境变量；`VERCEL_URL` 和 `VERCEL_PROJECT_PRODUCTION_URL` 会自动加入 Django 允许的域名与 CSRF 来源。若使用其他自定义域名，另设 `DJANGO_ALLOWED_HOSTS` 与 `DJANGO_CSRF_TRUSTED_ORIGINS`（逗号分隔，后者需写完整 `https://` 来源）。`.vercelignore` 会阻止本机数据库、密钥和备份文件通过 CLI 上传。
4. 首次部署成功后，在本机用 Supabase **Direct connection** 连接串创建管理员；也可以先手动执行迁移：

   ```bash
   DATABASE_URL='Supabase Direct connection 连接串' .venv/bin/python manage.py migrate
   DATABASE_URL='Supabase Direct connection 连接串' .venv/bin/python manage.py createsuperuser
   ```

   如果本机网络不支持 Supabase 直连所需的 IPv6，可改用 **Session pooler** 连接串（端口 `5432`）。迁移命令不要使用 Transaction pooler；构建脚本会自动将 Supabase 的 Transaction pooler 端口 `6543` 转成 Session pooler 端口 `5432` 用于迁移。若使用其他 PostgreSQL 服务，可单独配置 Vercel 的 `MIGRATION_DATABASE_URL`。首次运行前先按“本机启动”安装 Python 依赖。
5. 打开部署后的 HTTPS 地址，用第 4 步创建的管理员账号登录，检查书目录入、静态文件、扫码与照片识别。

Supabase 免费项目可能因长期低活跃度暂停，且需要自行定期导出数据库备份。项目现有的 `backup_library` 命令只适用于本机 SQLite；线上请使用 Supabase 的数据库导出或 `pg_dump`。

如果通过其他可信反向代理提供 HTTPS，需设置 `DJANGO_ALLOWED_HOSTS`（逗号分隔的域名）与 `DJANGO_CSRF_TRUSTED_ORIGINS`（完整的 `https://` 来源），并由代理提供静态文件。正式部署时设置 `DJANGO_DEBUG=0` 和 `DJANGO_SECRET_KEY`，执行 `collectstatic`。

## 数据与备份

本机数据保存在项目目录的 `db.sqlite3`，登录密钥保存在 `.secret_key`；这两个文件都不纳入 Git。本机请定期备份：

```bash
.venv/bin/python manage.py backup_library
```

备份默认写入 `backups/`。网页底部的“导出全部 CSV”方便迁移或在表格中查看。CSV 导入至少需要“书名”和“ISBN”两列；无 ISBN 的书可留空。建议先导出一份作为模板。相同 ISBN 的每一行代表一册实体书。

## 验证

```bash
.venv/bin/python manage.py test catalog
.venv/bin/python manage.py check
```

书目资料和封面来自外部服务，覆盖率和可访问性取决于网络及数据源；保存前应核对自动填入的资料。封面目前保存为外部链接。
