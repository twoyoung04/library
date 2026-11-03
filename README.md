# 我的藏书

一个供个人使用的图书管理系统，适合整理数千册实体书。手机页面可扫描 ISBN 条码或上传照片；照片中没有可读条码时，会尝试识别印刷的 ISBN 数字。图书资料从国内外多个来源查询，查不到时可以手动填写。

## 已实现

- ISBN-10 / ISBN-13 校验与统一、摄像头扫码、照片条码识别、照片文字识别
- 扫码识别音效与提示；可选自动保存、连续扫描，缺少书目资料时仍保存 ISBN
- 图书版本与实体副本分开管理；重复 ISBN 会新增副本，每册显示录入时间
- 书名、作者、ISBN、出版社、标签、位置搜索；标签及阅读状态筛选；按录入时间、书名、出版年排序
- 一本书可添加任意数量的标签；标签管理、阅读状态、入藏日期、备注
- UTF-8 CSV 导入导出、账号登录
- 页面快速切换和返回缓存；慢请求显示顶部进度条

## 本机启动

需要 Python 3.10+ 和 Node.js。首次安装：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm install
npm run build
```

`npm run build` 会下载英文 OCR 数据并生成静态资源。开发环境继续直接读写线上数据库，通过 SSH 隧道连接：

```bash
ssh -N -L 15432:127.0.0.1:15432 library
```

在不纳入 Git 的 `.env.local` 中设置（密码由部署时配置）：

```dotenv
DATABASE_URL=postgresql://library:密码@127.0.0.1:15432/library
DATABASE_SSLMODE=disable
```

隧道已加密；数据库只监听服务器回环地址。然后运行 `.venv/bin/python manage.py runserver 127.0.0.1:8000`。
本地操作会直接修改生产数据。迁移完成前可继续使用原有 Supabase 连接配置；不设置 `DATABASE_SSLMODE` 时默认要求数据库 TLS。
缺少连接串时启动会报错，不会使用旧 SQLite；`manage.py test` 使用临时 SQLite。
本地只从 `.env.local` 读取数据库连接和 SSL 模式，其他密钥通过环境变量设置。

## 国内图书数据源

系统会自动查询[无名图书公开接口](https://www.book345.com/mcp)、[NeoDB 公开书目](https://neodb.social/developer/)、[豆瓣读书条目](https://help.douban.com/tiaomu?app=4)与[成都图书馆馆藏目录](https://opac.cdclib.cn/opac/)；商务印书馆 ISBN 段还会查询[出版社官网书目](https://www.cp.com.cn/AdvancedSearch.html)。这些来源无需密钥。所有 ISBN 都优先查询国内书目，未查到时再查 Google Books 和 Open Library。系统只读取书名、作者、出版社、出版年和封面，不使用电子书链接。每次查询都核对返回的 ISBN。出版社官网和图书馆馆藏目录只覆盖各自收录的图书，部分字段可能需要手动补齐。

还可以按需接入两个来源，提高未命中书籍的覆盖率：

- [万维易源 ISBN 图书查询](https://www.showapi.com/apis/isbn-book-1626)：注册后获取 AppKey；官方说明有免费档位和调用限制。
- [聚合数据 ISBN 书号查询](https://www.juhe.cn/docs/api/id/726)：申请该接口的 Key；具体开通条件和费用以服务商页面为准。

在启动命令前，把已申请的密钥放到**服务器环境变量**中，按需设置一个或两个：

```bash
export SHOWAPI_APP_KEY='你的万维易源AppKey'
export JUHE_ISBN_KEY='你的聚合数据Key'
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

不要把密钥写入前端文件或提交到 Git。国内来源按“无名图书 → NeoDB → 商务印书馆（适用时）→ 豆瓣读书 → 成都图书馆 → 万维易源 → 聚合数据”依次尝试；前一来源命中后不再调用后续来源。查询结果会缓存 24 小时，未命中结果缓存 1 小时。设置新密钥并重启服务后，旧的未命中缓存不会阻挡新来源。每个来源的覆盖率取决于它自己的书目数据，建议用你手中的一批 ISBN 实测。

## 手机使用

手机访问时需要把服务部署到手机可访问的地址。**实时摄像头扫码需要 HTTPS**；直接通过局域网 HTTP 地址访问时，可以使用“拍照 / 上传图片”或手动输入 ISBN。建议先在本机验证录入流程，再配置带 HTTPS 的私人服务。不要把 Django 开发服务器直接暴露到公网。

“录入新书”的扫码区有两个默认关闭的选项：开启“扫描结果自动保存到书库”后，每次识别到有效 ISBN 会立即新增一册，并使用下方当前填写的位置、阅读状态和日期；书目源未命中时只保存 ISBN，书架显示“待补充书名”，可随后编辑。开启自动保存后才能选择“连续扫描”，摄像头会保持开启，条码需移出画面再扫同一 ISBN，以免重复新增。识别到 ISBN 会播放短音效，并显示识别及保存结果；浏览器或设备限制音效时仍有文字提示。未开启自动保存时，扫码只填充表单，仍需手动保存。

## 自有服务器 Docker 部署

部署结构：Nginx（HTTPS、静态文件）→ Gunicorn / Django → PostgreSQL。
操作步骤见 [deploy/README.md](deploy/README.md)。生产环境使用 `DJANGO_ENV=production`，必须显式提供密钥；不会自动执行数据库迁移。

## 数据与备份

生产 PostgreSQL 是唯一数据源，开发环境通过 SSH 隧道直接访问它。旧 `db.sqlite3` 不作为迁移源。
执行 `deploy/backup.sh` 可生成 PostgreSQL 自定义格式备份；还需把备份加密复制到服务器以外的位置，并定期验证恢复。
旧 `backup_library` 命令仅适用于 SQLite。
CSV 可用于表格查看及辅助导入导出，不替代完整数据库备份。

## 验证

```bash
.venv/bin/python manage.py test catalog
.venv/bin/python manage.py check
```

书目资料和封面来自外部服务，覆盖率和可访问性取决于网络及数据源；保存前应核对自动填入的资料。生产环境会将封面复制到腾讯云 COS，由服务器拼接公开 COS 地址，浏览器直接加载。来源链接仅保留用于迁移和重试；不可下载的封面显示占位符。部署与同步方法见 [部署说明](deploy/README.md#cos-封面)。
