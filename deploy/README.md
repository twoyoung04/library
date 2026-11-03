# 自有服务器部署与迁移

本次只部署网页。iOS 工作保存在 `codex/ios-client`，不要合并该分支。
以下是待执行流程；配置文件存在不代表服务器已部署或已切换。

## 准备

服务器需要 Docker Engine 和 Compose 插件；项目建议放 `/opt/library`。
镜像包含 Node 构建的扫码/OCR 资源，运行时无需 Node。
在项目根目录把 `deploy/env.example` 复制成 `.env`，权限设为 600。
密码和密钥使用独立随机十六进制字符串，避免 URL 和 Compose 转义问题。
`DOMAIN` 填裸域名；`POSTGRES_MAJOR` 根据源 dump 确认后填写，不要猜测后初始化。
当前数据卷布局按 PostgreSQL 14–17 编写，若源为 18+，先调整卷布局再初始化。
不要把本地 `.env.local`、旧 SQLite、`.secret_key` 或 `.vercel` 上传服务器。

## 导入用户提供的 dump

1. 先确认导出格式、源版本、完整性和是否包含 Django 表、关联表、序列、`django_migrations`、账号及权限。
2. 完整 Supabase dump 需先检查平台 schema、扩展及权限；不要把平台系统对象直接恢复进目标库。
3. `docker compose up -d db`，再 `docker compose build`。备份工具主版本须兼容源库；目标尽量与源同主版本。
4. 对自定义格式归档先用 `pg_restore --list` 检查，在空目标库中用 `--no-owner --no-acl --exit-on-error --single-transaction` 恢复；plain SQL 需检查内容后用 `psql -v ON_ERROR_STOP=1` 应用，不能当作归档恢复。Supabase 平台对象需单独筛除。
5. 恢复完成后执行：

```bash
docker compose run --rm web python manage.py showmigrations
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py check --deploy
```

没有自动清库或自动导入脚本，避免覆盖错误目标。若导入中断，先检查目标状态，不要盲目重复执行。
核对 Edition、Copy、Tag、标签关联、用户数量，抽查内容并检查序列；迁移前后记录核对结果。

## 域名与证书

先确认 DNS A/AAAA 正确且公网能访问 80/443；云防火墙也需开放。清除指向其他机器的旧 AAAA。
`.env` 中先保留 `NGINX_MODE=http`，申请前 HTTP 仅提供 ACME 验证，其他请求返回维护状态。

```bash
docker compose up -d web nginx
docker compose run --rm certbot certonly --webroot -w /var/www/acme --domain 实际域名 --email 实际邮箱 --agree-tos --no-eff-email
```

申请成功后将 `.env` 改为 `NGINX_MODE=https`：

```bash
docker compose up -d --force-recreate nginx
docker compose exec -T nginx nginx -t
```

Nginx 从共享卷加载证书并提供前端静态资源；应用和数据库无公网端口。
配置主机每日定时执行 `deploy/renew-cert.sh`；脚本检查续期并重新加载 Nginx，需监控失败结果。
正式开放前测试登录、CSRF、新增/编辑、CSV、手机 HTTPS 扫码及照片 OCR。

## 备份与后续更新

`deploy/backup.sh` 生成 `deploy/backups/*.dump`；每日运行并异地加密复制。
保留策略建议最近 7 天和最近 4 周，初始脚本不自动删除备份。
定期在独立数据库演练恢复，并监控磁盘和备份任务结果。
更新先备份，构建镜像，按变更需要安排停写并运行 migrate，最后 `docker compose up -d --build`。
不要运行 `docker compose down -v`，它会删除数据库和证书卷。
应用迁移如不兼容旧代码，必须同时规划数据恢复；仅回退镜像不足以回退 schema。

## 开发环境及切换

开发继续直连生产库，详见根目录 README 的 SSH 隧道说明。
服务器 PostgreSQL 映射为 `127.0.0.1:15432`，不开放公网 5432/15432。
最终 dump 必须在旧服务停止写入后产生。用户准备好文件后再执行最终导入。
切换时停止旧网页及其他旧库写入，核对最终数据后开放新 HTTPS 入口，并更新本地数据库连接。
已有自定义域名可降低 TTL 后切换；若旧入口是 vercel.app，则使用新域名。
新库开放写入后不能直接回切旧库，必须先处理新产生的数据。
观察稳定并确认异地备份可恢复后，再停用 Vercel / Supabase；此前保留旧平台用于回退。

## 实际部署记录（2026-11-02）

- 服务器：SSH `library`，项目 `/opt/library`，域名 `lovaime.com`。
- 已安装官方 Docker Engine / Compose；服务器访问 Docker Hub 超时，采用本机构建 linux/amd64 官方基础镜像后 `docker save --platform linux/amd64 | gzip | ssh ... docker load` 部署。
- 前端构建使用 Node 24，满足 ZXing 依赖要求；生产强制 HTTPS，测试使用临时 SQLite 且不启用生产重定向。
- PostgreSQL 17，恢复 14 张 Django 表。1,855 个版本、2,345 册、29 个标签、2 个账号。
- 最终切换时对源应用表加写锁，并逐表对比完整行内容；一致后安装 `library_migration_readonly` 触发器，拒绝旧库应用表的新增、修改、删除和清空操作。源数据未删除。
- 原 Vercel / Supabase 项目保留，旧网页写入将报数据库只读错误；统一使用新域名。后续确认稳定再关闭旧项目。
- 源库只读保护回退：逐表 `DROP TRIGGER library_migration_readonly ON public.<表名>`，最后 `DROP FUNCTION public.library_migration_readonly()`。只能在新服务停止写入、已处理切换后的新增数据后回退。
- 本地 `.env.local` 已切换到新生产库；SSH 隧道已启动，重启电脑后按根目录 README 重建。
- 服务器 `.env` 权限 600；独立随机生产密钥。生产环境配置检查仅有初期未启用长期 HSTS 的提示。
- 容器内 42 项网页测试通过；生产 HTTPS 头识别、登录页、登录重定向和安全 Cookie 检查通过。
- 已申请 Let's Encrypt 证书（2027-01-31 到期），Nginx HTTPS 配置校验通过。
- `/etc/cron.d/library-backup`：每日运行备份；`/etc/cron.d/library-cert-renew`：每日检查续期并重载 Nginx。时间使用服务器时区。
- 初始源快照、新库备份和旧连接配置保存在本机 `backups/server-migration`（不纳入 Git，受限权限）；服务器备份在 `/opt/library/deploy/backups`。已取回一份新库备份。
- 长期异地备份尚需配置持续复制；当前异地副本为本机手动取回的备份。

- 已在独立临时数据库完成一次恢复演练，实体书数量为 2,345，临时库已删除。
- Certbot `renew --dry-run` 成功，正式续期 cron 已启用。
- 用户放行公网 TCP 443 后，公网按域名/SNI及目标 IP 验证登录页与扫码 JS 均返回 200，证书校验通过；HTTP 返回 301 到 https://lovaime.com/。本机系统 DNS 暂仍缓存旧 IP，公共解析已是新 IP。
- DNS 权威记录及公共解析已指向 `115.159.95.169`，部分客户端仍可能受旧 DNS 缓存影响。

## COS 封面

目标桶 `cover-1307828558`。在服务器 `.env` 配置 `COS_REGION`、`COS_SECRET_ID`、`COS_SECRET_KEY`；权限至少覆盖该桶 `covers/` 前缀对象的 PutObject / GetObject。无需开放整个桶或提供公开访问权限。
构建并备份后应用 `0005_edition_cover_object_key` 迁移；先保持 `COS_ENABLED=0`，执行一次 `python manage.py sync_covers`，记录成功和失败数量。真实凭据未配置前不运行批量迁移。
命令把图片校验并转为 JPEG（最长 1600×2400，下载最大 8 MB），上传成功后才保存 COS key；来源 URL 保留在数据库用于重试，但开启 COS 后不会用于页面图片加载。
HTTP 下载拒绝非公网目标及不安全协议，每次重定向重新检查；不能下载的来源需重试或人工替换，绝不将错误页当作图片保存。

迁移确认后在 `.env` 设置 `COS_ENABLED=1`，重建 web 容器，并启动持续同步 worker：

```bash
docker compose --profile covers up -d --build web covers nginx
```

页面使用本站受登录保护的 `/books/<id>/cover/` 地址，服务端从 COS 读取图片，允许私有桶且无需配置 COS 自定义域名。浏览器不再请求第三方封面，未迁移或来源已修改的封面暂显示占位符。
worker 自动扫描新增、修改及 CSV 导入产生的封面，失败在下一轮重试；日志只记录图书 ID 和错误类型，不记录密钥和签名。
图片对象按来源和内容哈希命名，可重试且不会覆盖其他图书；当前不自动删除旧对象，避免误删共享封面。数据库备份只含对象 key，COS 对象需单独保留／备份。
本地开发若启用 COS，还需以环境变量配置相同的 COS 参数。


### COS 上线记录（2026-11-03）

- 目标桶 `cover-1307828558`，地域 `ap-shanghai`，沿用用户设置的桶权限；凭据只在服务器环境中使用。
- 上传／读取验证通过，临时测试对象已清理。迁移前后均已备份数据库，生产已应用 `0005_edition_cover_object_key`。
- 历史封面 1,855 张：1,382 张已上传（含从本机补取及按精确 ISBN 补取）；473 张未取得。原来源主要返回 403 或网络不可达，未伪造替代封面。
- 未成功项清单在本机 `backups/cos-migration/unmigrated-covers.csv`，不纳入 Git。来源 URL 继续保留用于重试。
- 生产 `COS_ENABLED=1`；`covers` 容器持续处理新增／编辑／CSV 导入封面并重试失败项。每轮 8 路并发，同一不可达主机累计 3 次连接错误后本轮暂缓，下一轮重新尝试。
- 容器内 49 项测试通过；登录后封面返回 JPEG，书库页面所有图片均使用本站地址，无第三方图片 URL。
- 用户指定域名接入问题自行处理，此记录不表示域名公网拦截已经解除。


### 原始封面恢复（2026-11-03）

- 发现 book345 `/covers/<ISBN>.jpg` 对 1,249 本书返回同一占位图；另有灰色默认图。原上传成功数包含这些图片，本次改按有效图片统计。
- 提取 book345 `dbImg` 与 `doubanExtra.coverUrl`，仅接受接口 ISBN 与记录一致的数据；补取豆瓣原始图片时提供浏览器 User-Agent 与公开书目来源 Referer。
- 先后提取 1,819 条待补记录的候选地址（含 95 条原灰色默认图记录）；正常封面不覆盖，保留原来源和旧 COS 对象。
- 原 book345 来源 1,249 本中已恢复 1,206 本，43 本待补。全库 1,855 本中有效封面 1,332 本，523 本待补；两张已知占位图被排除。
- 同时核查出版社、有赞官方店、当当、三民、MegBook、Google Books、Open Library 等来源。部分来源不可达／拒绝请求；出版社精确 ISBN 商品页另补回 2 本（商品主图可能含宣传边框）。未仅凭相似书名替换。
- `lookup_wuming` 优先大图／原图字段；后台同步旧 book345 URL 时重新取原始封面地址；下载和展示都拒绝已知占位图。对超大像素图片增加显式限制。
- 本机与容器内 52 项测试通过；《1984》《崩盘》《100个句子记完7000个托福单词》登录后 COS 图片读取通过。图库只使用本站地址。
- 本机恢复记录位于 `backups/cover-recovery/`：候选 JSONL、恢复来源记录、`remaining-covers.csv` 待补清单；不纳入 Git。
- 用户要求暂不处理域名问题。持续同步恢复后按原方式运行；未找到的候选不伪造、不使用错误封面顶替。

### 临时 IP HTTPS 入口（2026-11-03）

- 地址 `https://115.159.95.169`，HTTP 自动跳转到该 IP 的 HTTPS。域名 Nginx 配置继续保留。
- `compose.override.yaml` 自动合并，给 web 增加精确 IP Host / CSRF 来源，给 Nginx 增加 `deploy/nginx.ip.conf.template`。无 DEBUG、Cookie 或 HTTPS 安全设置降级。
- 已使用 Certbot 5.9 为 IP 申请 Let's Encrypt `shortlived` 证书，证书名 `library-ip`，路径 `/etc/letsencrypt/live/library-ip/`。首次证书到期日为 2026-11-09。
- 已配置每 6 小时检查续期；`renew-cert.sh` 即使另一个证书续期失败，也会检查并重新加载 Nginx，然后保留失败退出状态。IP 证书独立 dry-run 成功。
- 公网真实 TLS 校验、登录页、静态资源、HTTP 跳转、Secure Cookie 和 CSRF 正反例均通过；IP Host 下登录后的图库及 COS 图片读取通过。
- 首次在全新服务器启用时，需先让 IP 的 80 端口 server 提供 ACME webroot，再申请证书并启用 TLS server：`docker compose run --rm certbot certonly --webroot -w /var/www/acme --ip-address 115.159.95.169 --preferred-profile shortlived --cert-name library-ip --agree-tos --register-unsafely-without-email`。
- 移除入口：把 `compose.override.yaml` 移出项目或改名为不自动加载的文件，再执行 `docker compose up -d --no-build --force-recreate web nginx`，同时移除不再需要的 `library-ip` 证书续期配置。主站与数据库不受此移除影响。


### COS 对象路径与公开直连（2026-11-03）

- 采用用户确认的公开可读桶：数据库继续保存 `cover_object_key`（`covers/...jpg` 对象路径），不新增或保存完整 COS URL 字段；`cover_url` 保留原始采集来源。
- 服务器通过 `COS_PUBLIC_BASE_URL` 拼接路径，默认根据桶名与地域生成 HTTPS COS 默认域名；可替换为已配置好的 HTTPS CDN / 自定义域名，不修改数据库。
- 页面图片 src 直接是 COS 对象 URL；旧 `/books/<id>/cover/` 入口保留登录检查后跳转，不再由 Django 读取和转发图片数据。
- 来源改变后旧 key 不匹配，仍暂显示占位符，后台重新下载／入桶；已知错误占位图继续拦截。新对象设置 `public, max-age=31536000, immutable` 缓存策略，内容哈希变化会生成新路径。
- 本地开发可用同样的公开地址读取封面，无需 COS 访问密钥；上传任务仍仅在服务器使用密钥。
- 55 项测试通过，数据库模型检查无迁移；部署后验证图库全部直连 COS、匿名读取返回图片、旧入口重定向正确。

### GitHub Actions 发布

Pull Request 构建应用和 Nginx 镜像，并运行 Django 测试与迁移检查。推送到 `main` 或在该分支手动运行工作流时，检查通过后会把镜像传到服务器发布。GitHub Actions 页面可查看结果。

服务器 `/opt/library/deploy/receive-release.sh` 是专用 SSH 密钥的入口。仓库使用 `LIBRARY_DEPLOY_KEY`、`LIBRARY_DEPLOY_KNOWN_HOSTS` 和 `LIBRARY_DEPLOY_TARGET` 三个 Actions Secrets。更新入口脚本时通过服务器管理 SSH 安装新版。

每次发布保存数据库备份、执行所需迁移并检查登录页和扫码资源。发布串行执行。无数据库迁移的发布在检查失败时恢复上一版镜像和配置。执行迁移后如检查失败，结合备份和迁移记录决定回退方式。服务器 `.deployed-revision` 保存最后一次成功发布的提交。
