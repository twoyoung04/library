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

## 当前验证记录

- 网页现有 42 项 Django 测试通过。
- Compose 配置校验通过；生产环境缺少密钥会拒绝启动。
- `check --deploy` 仅剩 HSTS 提示；正式域名 HTTPS 验证稳定后再决定启用期限，初期不强制长期缓存。
- 本机完整镜像构建被 Docker Hub 拉取连接重置阻断，尚未验证容器启动和实际 Nginx 配置加载。
- 尚未在服务器安装或启动服务，未导入数据、申请证书、修改 DNS 或停用旧平台。
