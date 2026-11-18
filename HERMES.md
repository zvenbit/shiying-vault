# Hermes 部署交接

目标：将本仓库的「拾映」部署到用户的轻量 Linux 服务器，供多个独立账号使用。本次交付到源码和部署包为止，尚未连接目标服务器。

发布前检查范围与结果见 [VALIDATION.md](VALIDATION.md)。目标服务器仍须验证镜像构建、Linux 浏览器沙箱、HTTPS 代理及平台出口网络。源码包不含账号、Cookie、密钥或下载内容。原创代码采用 PolyForm Noncommercial 1.0.0。[项目许可证](LICENSE)

## 1. 确定部署环境

`shiying-vault` 仓库根目录应直接包含 `Dockerfile`、`compose.yaml`、`app/` 和 `HERMES.md`；从 ZIP 获取时进入其中的 `shiying-vault/`。读取 `compose.yaml`、`.env.example` 和 `VALIDATION.md`。向用户取得 Git 仓库地址、服务器正常访问方式和最终域名；沿用服务器现有 HTTPS 反向代理。检查 Docker Compose v2、可用内存、磁盘及 `8765` 端口。

如果使用交付 ZIP，在解压目录执行 `sha256sum -c SOURCE_MANIFEST.sha256` 核对文件；通过 Git 获取时记录实际 commit。之后的升级沿用同一仓库路径和 Compose 项目名，避免误建新数据卷。

完成条件：明确仓库目录、最终 HTTPS URL、代理 upstream 和持久化数据卷；已有服务保持可用。

## 2. 初始化并启动

1. 克隆仓库进入根目录，运行 `python3 scripts/setup_env.py`，填入实际 HTTPS 地址。以 `https://shiying.example.com` 为例，将 `example.com` 换成实际主域名，并让 `PUBLIC_URL` 和反向代理使用同一地址。已有 `.env` 时保留现有凭据，只调整必要配置。
2. 默认保留 `WORKER_CONCURRENCY=1`、单容器副本、单 Uvicorn worker。镜像 Playwright 版本须与锁文件 `requirements.txt` 一致；保留镜像 digest 和安装时的 `--require-hashes`，勿在服务器临时放宽依赖版本。
3. 先执行 `docker compose config --quiet`，再运行 `docker compose up -d --build`。用 `docker compose ps` 检查 healthy 状态，用 `docker compose port vault 8765` 确认实际本机端口；修改 APP_PORT 时同步代理 upstream。完整 `docker compose config` 会展开环境变量，诊断报告使用 `--quiet` 以免输出密码。
4. 参照 `deploy/nginx.conf.example` 加入独立站点配置，替换域名及证书路径。执行代理配置检查后 reload。
5. 管理员初始密码保存在服务器 `.env`，通过用户认可的安全渠道交付；在部署报告中只说明保存位置。

在容器内验证健康状态和真实 Chromium 启动（无须抖音账号）：

```bash
docker compose exec -T vault python - <<'PY'
import urllib.request
from playwright.sync_api import sync_playwright
assert urllib.request.urlopen("http://127.0.0.1:8765/healthz", timeout=3).status == 200
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, chromium_sandbox=True)
    page = browser.new_page()
    page.goto("data:text/html,<title>vault-check</title>")
    assert page.title() == "vault-check"
    browser.close()
print("PASS: health and sandboxed Chromium")
PY
```

运行 `docker compose exec -T vault python - < scripts/doctor.py` 检查 FFmpeg、数据卷和平台域名地址校验。失败按下方排障分支处理。初始化密码只用于新数据卷；升级时保留 `.env`、数据卷和加密密钥。服务器无桌面环境时保持默认无界面浏览器。

完成条件：HTTPS 页面可打开，healthz 为 200，Chromium 沙箱启动通过，管理员可以登录；容器端口仅绑定 loopback。

## 3. 分层验收

先验证不依赖抖音的平台功能，再进行真实平台访问。

1. 创建一个测试成员。确认成员访问不了其他用户的任务、文件和连接，且无法打开成员管理。
2. 核对 doctor 输出。工具支持通过独立 HTTPS DNS 复核代理 Fake-IP；若复核失败、解析到其他内网地址或 DNS 失败，修正服务器 DNS / 代理配置后重试；保留域名与公网地址校验。地址校验通过后仍需验证作品接口。
3. 由用户在自己的「账号与连接」页面扫码登录，或自行导入其抖音 Cookie。验证由用户手动完成。
4. 从用户提供的一个公开单视频开始，勾选视频和音频；确认 MP4 和 MP3 可以播放，而不只是文件存在。
5. 分别验证一个图文作品和一个长文章，核对图片数量、顺序、文章开头及结尾。特别检查长文是否只保存了列表摘要。
   长文章详情的 `article_content` 可能是 JSON 字符串，须解码后读取 `markdown`；`long_article_abstract` 不可当作正文。详情仍标记 `has_more=true` 时不能当作完整文章导出。源码已覆盖这两项回归。
6. 用一个博主主页设置上限 2，检查数量上限提示、任务状态、ZIP 内容。保持「只获取未保存的作品」勾选，再次提交相同主页，确认旧作品被过滤、新作品不受旧作品数量影响；取消勾选应可重新下载。打开正文确认 `<br/>` 正常换行并验证「一键复制内容」。取消一个任务后等待停止，再重试，确认已完成作品保留。
7. 重启容器，确认账号、已保存文件和连接状态仍在，未完成任务重新排队。

本次阅读与文件保护更新无需迁移数据库：重新构建服务后刷新网页即可，已有内容直接适用。验收时补充检查：任务卡片标题最多两行；文章正确显示加粗、引用、段落和本地配图；复制得到纯文本；图集缩略图、放大、原尺寸、下载原图可用。图片预览中左右键切换图片，Esc 回正文；正文 Esc 回任务列表并恢复位置。

文件重试不再清空整个作品目录，先检查任务中已完成条目的登记文件，只补缺失或大小不匹配的媒体，保留原文件 ID 和下载地址。音频提取失败时保留源视频；在隔离测试中模拟取消、配额不足和数据库提交异常，确认旧文件仍可读取。生产验收不要人为破坏用户已保存文件。导出期间同一任务的重试和删除会返回 409；下载中断或无效 Range 请求会清理临时 ZIP 并释放导出锁，随后应可重新导出。

完成条件：记录实际验收通过的内容类型与失败项。平台拒绝访问或页面结构不匹配时，报告具体阻塞，保留任务错误信息；不要以健康检查成功代替采集成功。

## 4. 升级、备份与回滚

升级前记录当前 Git commit 和镜像 ID；从 `docker inspect` 的 Mounts 中确认容器 `/data` 对应的实际卷名（通常带 Compose 项目前缀）。暂停新任务并等待活跃采集、导出完成，然后 `docker compose stop vault`。将该卷完整备份到权限受限的目录，包含 SQLite 主文件、WAL/SHM、`encryption.key`、`jobs/`；同时保存 `.env`。备份可读且文件齐全后再升级。

更新源码后重新 build 并启动，再执行 healthz、登录和已有文件下载检查。失败则回到原 commit 重建，必要时从同一份完整备份恢复数据卷。使用 `docker compose down` 保留卷；移除数据卷只用于用户明确要求的清空操作。

## 排障分支

- **403 请求来源不匹配**：核对 `PUBLIC_URL` 与浏览器地址，调整后 recreate 容器。
- **Chromium 启动失败**：检查 Docker 日志、浏览器版本、`deploy/seccomp_profile.json`、宿主机用户命名空间和 AppArmor 限制。保持沙箱配置；需要改变宿主机安全设置时先说明具体变更。
- **抖音加载失败 / 无作品**：先运行 doctor，再检查服务器对抖音的网络可达性及账号连接。页面改版时适配 `app/collector.py` 和 `app/parser.py`，为实际响应补充脱敏测试夹具。
- **扫码确认后提示 2046**：未取得登录态，不能仅凭错误码认定验证弹窗已经显示。先运行 doctor，确认验证配置 `vcs.zijieapi.com`、确认后加载的身份验证脚本 `auth.zijieapi.com` 及已有验证 CDN 可访问。当前成员可读取 `GET /api/connection/diagnostics` 查看自己窗口的最新 QR 状态、错误码及失败资源域名，无二维码或凭证。若页面出现身份验证，让用户在当前页面完成；若仍停在手机确认，勿反复要求确认，可让用户尝试右侧短信登录。若必须使用摄像头，当前远程画面不支持转发。本机排查可设置 `BROWSER_HEADLESS=false`；无显示服务的 Linux 保持默认 `true`。必须等到连接保存后再验收采集。
- **OOM / 浏览器崩溃**：保持单任务并发，减少同时扫码会话，检查容器和宿主机内存后再调整资源配额。
- **磁盘不足 / ZIP 失败**：检查数据卷及临时 `exports/` 目录；确认没有进行中的导出后清理意外中断遗留的 ZIP。通过界面删除用户选择的旧任务。

最后交付：实际访问 URL、部署 commit / 镜像 ID、实际数据卷名与备份位置、管理员凭据保存位置、各内容类型真实验收结果和仍需处理的限制。只有镜像构建、浏览器启动、HTTPS 登录、平台采集、文件下载和重启持久化都通过，才能报告服务器部署完成。
