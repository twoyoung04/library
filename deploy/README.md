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
