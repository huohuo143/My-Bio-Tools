# My Bio Tools Auth Service

My Bio Tools v1.9.5+ 的集中账号、审核、授权期限、设备绑定、离线授权与应用更新服务。服务只处理账号、授权和更新元数据，不接收科研输入文件或分析结果。生产环境使用 `mybiotools.aizs.top`，Staging 与 Production 必须使用独立 D1 和独立签名密钥。

## 结构

- `src/`：Cloudflare Worker、密码与签名逻辑、邮件和管理页。
- `migrations/`：D1 STRICT 表、唯一约束和查询索引。
- `test/`：密码、Ed25519 授权、HTTP 安全头，以及基于 SQLite 的完整账号/D1 业务链测试。
- `wrangler.example.jsonc`：不含真实资源 ID 和密钥的部署模板。

## 本地检查

Node.js 24 可以在不安装依赖的情况下运行安全单元测试：

```bash
npm test
```

`npm test` 会直接运行注册、验证、审核、2 台设备上限、解绑、密码重置、停用/恢复与匿名化删除链路。Wrangler 本地 D1 测试需要先安装项目内的开发依赖；根目录的实施过程不会自动安装或部署。

## 部署前配置

1. 复制 `wrangler.example.jsonc` 为被 `.gitignore` 忽略的 `wrangler.jsonc`。
2. 建立 D1 数据库 `my-bio-tools-auth`，填入真实 `database_id`。
3. 将 `mybiotools.aizs.top` 绑定为 Worker custom domain。
4. 在 Resend 免费计划中验证 `aizs.top`，并确认 `noreply@aizs.top` 可作为发件人。
5. 在 Cloudflare Access 中同时保护 `/admin`、`/admin/*`、`/api/v1/admin` 和 `/api/v1/admin/*`，只允许开发者邮箱。
6. 设置下列 Worker secrets，禁止写入 `vars`、文档或 Git：
   - `PASSWORD_PEPPER`：至少 32 字节高强度随机值。
   - `LICENSE_PRIVATE_JWK`：Ed25519 私钥 JWK JSON。
   - `OMICS_DATABASE_KEY_B64`：32-byte base64 多组学数据库密钥；只作 Worker secret，不写入仓库或 APP。
   - `LICENSE_PUBLIC_JWK`：对应公钥 JWK JSON。
   - `IP_HASH_SALT`：审计日志来源摘要的随机盐。
   - `RESEND_API_KEY`：仅允许通过已验证发信域发送事务邮件的 Resend API Key。
7. 在 GitHub 仓库 `huohuo143/My-Bio-Tools` 发布 Release；Worker 使用 `GITHUB_RELEASES_TOKEN` 代取安装包，客户端不会接触 GitHub 凭据。该 token 应优先使用仅对本仓库 Contents/Metadata 只读的 fine-grained token。更新清单 JSON 只通过 `UPDATE_MANIFEST_JSON` secret 提供。
8. 执行 D1 migrations，先部署 staging，再进行生产部署。

生产发信使用 Resend REST API，Cloudflare Workers、D1 与 Access 保持免费计划。Resend 免费计划当前上限为每月 3,000 封、每天 100 封和 1 个自定义域名；超过限额时授权服务会返回稳定的 `EMAIL_SEND_FAILED`，不会静默跳过邮箱验证。验证 `aizs.top` 前必须先导出 DNS 快照，不覆盖现有 MX/SPF/DKIM/DMARC。

Resend 只接收事务邮件所必需的收件地址、姓名和验证/重置链接。科研输入文件与分析结果不会发送给 Resend 或授权服务。API Key 只保存在 macOS Keychain 与 Cloudflare secrets；部署完成后立即撤销临时 Cloudflare 令牌。

密码派生使用 `node:crypto` scrypt（`N=32768, r=8, p=2, maxmem=64 MiB`），并先经过独立 HMAC pepper。不要改回 Workers Web Crypto 单次 600,000 次 PBKDF2：Cloudflare 虽支持 PBKDF2，但生产运行时会以 `NotSupportedError` 拒绝超过平台上限的单次迭代数。

### Ed25519 运行时兼容性

- Node.js 导出 Ed25519 JWK 时可能附带非标准的 `alg: "Ed25519"`。密钥生成脚本会在写入 Keychain 前删除该字段，Worker 导入既有密钥时也会兼容性移除它；只清理元数据，不轮换密钥。
- `/health` 会验证生产签名密钥对、多组学密钥长度与更新清单。授权服务 v1.9.5+ 的上线验收必须同时得到 `status=ok`、`licenseSigning=ok`、`omicsKeyDelivery=ok`、`appUpdate=ok` 和 `authorizationPeriod=ok`。
- 登录接口会先完成签名配置自检，再创建设备和会话，避免签名故障遗留无效会话。
- 不要手工修改 JWK 的 `kty`、`crv`、`x`、`d`；需要真正轮换密钥时，必须同步重建所有客户端。

## 生产部署入口

1. 安装锁定依赖：`npm install`。
2. 执行 `../script/prepare_auth_secrets.sh`，密钥会进入 macOS Keychain，不输出明文。
3. 在被 Git 忽略的 `wrangler.jsonc` 填入 D1 ID、Access Team Domain 和 AUD。
4. 依次执行 `../script/deploy_auth_service.sh staging` 和 `../script/deploy_auth_service.sh production`。

正式版本使用 `../script/publish_app_update.sh production <DMG>` 一次完成 GitHub Release、资源大小校验、更新清单写入和 Worker 部署。发布脚本不会覆盖已有同名 Release 资源。

`My Bio Tools Admin` Access 应用必须使用同一 AUD 同时保护 `/admin`、`/admin/*`、`/api/v1/admin`、`/api/v1/admin/*`，不得保护整个主机，否则 App 注册与登录会被拦截。

回滚 Production 时，先解除 `mybiotools.aizs.top` 的 Worker Custom Domain/route，再按部署前 DNS 快照恢复域名状态。保留 Production D1 与 Worker 版本用于审计和重部署，不删除数据库。

轮换生产 Ed25519 密钥会使现有离线许可证失效，必须同时重新构建 macOS/Windows 客户端；只有明确轮换时才允许运行 `../script/prepare_auth_secrets.sh --rotate`。

## 密钥生成

在本机终端中使用 Web Crypto 生成 Ed25519 JWK，并立即通过 `wrangler secret put` 分别录入。不要将私钥重定向到项目文件、聊天或日志。

## 上线验收

- 运行 migrations 后检查邮箱唯一约束和 2 台设备上限。
- 验证注册 → 邮箱验证 → 待审核 → 批准 → 登录。
- 验证停用、强制退出、设备解绑和密码重置会撤销会话。
- 验证管理员发送重置邮件，以及“邮箱 + DELETE”双重确认的永久删除和个人信息匿名化。
- 检查 Worker 日志不含密码、令牌、JWK 或完整 IP。
- 检查 `/health` 返回 `licenseSigning=ok`，再允许用户进行真实登录。
