# 拾映 · 抖音内容库

可部署到轻量 Linux 服务器的多人内容归档工具。粘贴抖音博主主页、作品链接或带链接的分享文案，选择内容类型，后台采集并保存在个人内容库。

**部署交给 Hermes 时，先让它阅读 [HERMES.md](HERMES.md)。** 本目录是 `shiying-vault` 仓库根目录。站点使用 `shiying` 子域名，例如 `https://shiying.example.com`。部署时将 `example.com` 替换为自己的域名。

## 功能

| 内容 | 保存方式 |
| --- | --- |
| 视频 | 读取页面返回的最高码率候选地址，保存视频、封面和文案 |
| 音频 | 从视频提取 MP3；图文配乐保存原始音频格式 |
| 图文 | 按顺序保存图片与文案 |
| 长文章 | 从详情响应或文章正文区域读取，导出 Markdown、TXT 与配图 |
| 元信息 | 作品 ID、作者、来源、发布时间、统计与文件清单 |

- 管理员创建成员；成员的任务、文件和抖音登录状态相互隔离。
- 抖音扫码连接，或导入自己的 Cookie；登录信息在服务器上加密保存。
- 队列、进度、取消、失败重试、服务重启恢复、个人配额。
- 图片和视频预览、音频播放、文章阅读及一键复制正文、搜索与分类、单文件及任务 ZIP 下载。
- 默认过滤本人已经保存的作品，同一博主再次采集时只保存新内容。
- 使用独立 Chromium 上下文观察平台页面正常返回的数据，不包含签名逆向、验证码自动处理或付费内容访问逻辑。

## 使用边界与验证状态

当前版本已完成独立 Python 3.12 环境的回归检查，详见 [VALIDATION.md](VALIDATION.md)。此前已在本机验证扫码连接、视频下载与音频提取、图文、长文章及重复过滤；公开文档不保留真实账号、博主、作品标识或原始调试记录。**目标 Linux 服务器仍须按 [HERMES.md](HERMES.md) 验收**，本机通过不代表服务器网络或平台登录必然可用。

抖音的账号限制、服务器 IP、地区网络、登录失效、页面改版都会影响获取。扫码窗口可能需要手动选择登录、刷新二维码或完成验证。Cookie 导入仅表示已保存，不表示平台已认可该会话。系统会说明失败或部分完成；如果全部作品已保存，会明确显示「没有新作品」及过滤数量。

手机确认后若返回登录校验错误（2046），尚未登录成功。若页面出现身份验证，继续在当前页面完成；若仍停在手机确认，不要重复点击确认，可使用页面右侧的短信验证码登录。工具会显示新弹出的验证页面；需要填写手机号或验证码时，先点击画面中的输入框，再使用下方「手动输入」。输入内容仅发送到本人当前登录窗口，不回显或保存；也可使用「下一输入框」「退格」「确认键」。只有取得实际登录 Cookie 后才显示连接成功。

已修复官方验证配置 `vcs.zijieapi.com` 和确认后加载的身份验证脚本 `auth.zijieapi.com` 的误拦截。登录信息在连接成功后加密保存。当前成员可通过 `GET /api/connection/diagnostics` 查看自己活跃窗口的状态码和失败资源域名；诊断只存在内存，不含二维码、请求路径、参数、Cookie 或验证码。

主页列表可能未完整加载；达到数量上限或未观察到列表结束时，结果会标记为「部分完成」。文章列表可能只有约 499 字预览，本工具要求详情数据或正文区域，读取失败会保留失败状态。DOM 正文提取仍依赖平台页面结构；图中文字 OCR、视频语音转写、直播录制和定时同步不在当前版本范围。重试会保留同一任务中已完成作品。

## 重复采集与正文复制

「只获取未保存的作品」默认勾选。按当前成员的作品 ID 跨任务过滤，适用于同一博主、分享短链和单作品链接；不同成员互不影响。只有成功保存、登记文件仍存在且大小匹配的作品才会过滤，失败、跳过或缺失文件的作品仍会尝试获取。任务数量上限按未保存作品计算，过滤的旧作品不占名额；列表未完整加载时仍会给出部分完成提示。

同一成员的任务依次执行，避免并发重复下载；不同成员可按服务器并发配置同时处理。任务详情会显示已过滤数量；全部过滤时不会创建重复作品或下载文件。需要重下、更新正文或补充之前未选择的格式时，取消勾选后创建新任务。点击旧任务的「重试」则继续处理该任务中尚未成功的作品，并重新核对已完成条目的文件。重试不清空作品目录，会核对现有文件大小、复用完好的媒体，只补缺失或大小不匹配的文件；音频提取失败时保留源视频供下次使用。新文件先写入临时目录，校验配额后替换并登记；失败、取消或数据库提交异常时保留旧文件及原下载地址。

任务卡片标题最多显示两行，长文案在详情内完整保留。在采集任务详情中，点击已保存作品卡片的标题、说明或空白处均可打开内容详情，键盘 Enter / 空格也可操作。从任务列表进入内容后，顶部显示「返回任务详情」；返回会恢复原列表的滚动位置及选中卡片，自动刷新也保留位置。阅读过程中，返回、切换与复制按钮保持在窗口上方；直接从内容库进入时不会显示旧任务的返回入口。内容详情中的「一键复制内容」复制当前显示的正文或文案。阅读和复制时自动把 `<br/>`、`<br>` 等换成换行，已保存的旧内容立即适用；原始 TXT / Markdown 导出文件保持原样。HTTPS 和本机使用浏览器剪贴板接口，普通 HTTP 提供复制兼容方式；浏览器拒绝复制时会给出提示。

内容详情提供「上一个 / 下一个」及当前序号，按同一任务列表的顺序连续查看有已保存文件的作品，不跨任务切换；第一个和最后一个会禁用对应方向，只有一条内容时两个按钮均禁用。从内容库直接进入，也按该内容所属任务的顺序切换。连续查看后点击「返回任务详情」，仍恢复最初进入内容时的列表位置。支持键盘 ← / → 切换；从任务列表进入时按 Esc 返回该列表并恢复原位置，直接从内容库进入时按 Esc 关闭详情。左右键在输入框、可编辑区域或播放器获得焦点时保留原有操作；组合键、输入法组字期间及长按重复事件不会触发内容切换。

文章优先读取已保存的 Markdown，按标题、段落、加粗、列表和引用排版；配图放回正文，点击可放大。复制得到渲染后的纯文本，原始 Markdown / TXT 下载内容不变。浏览器关闭原始 HTML 执行，配图只使用本作品已保存文件，不重新请求抖音图片链接；未保存配图会明确提示。

图文详情展示主图和全部缩略图，可用「上一张 / 下一张」或点击缩略图切换，点击主图放大。文章下方也提供全部配图缩略图。放大窗口支持原尺寸、适应窗口和下载当前原图；← / → 切换图片，Esc 只关闭图片窗口并恢复正文阅读位置。焦点在图集内时左右键切换配图，焦点回到内容工具栏时左右键切换作品。首尾图片不循环跳转。

## Docker 启动

建议先按单采集任务并发运行；默认容器上限 1.5 GB 内存。2 核 / 2 GB 服务器可用于低并发试运行，实际内存及磁盘容量以首次验收监测为准。

```bash
python3 scripts/setup_env.py
docker compose up -d --build
curl --fail http://127.0.0.1:8765/healthz
```

初始化脚本会询问最终访问地址并生成 `.env`。管理员用户名为 `admin`，随机密码只保存在 `.env`。首次初始化后，修改环境变量中的密码不会覆盖已有账号；登录后可在界面修改密码。

容器端口仅绑定到服务器 `127.0.0.1`。使用现有 Nginx 或其他反向代理提供 HTTPS，参考 [Nginx 示例](deploy/nginx.conf.example)。`.env` 的 `PUBLIC_URL` 必须与浏览器实际访问的协议、域名和端口一致；当前仅支持域名根路径部署。多个网站共用服务器时，更改 `APP_PORT` 并同步代理 upstream。

应用使用 SQLite 和进程内浏览器池；**保持一个容器副本和一个 Uvicorn worker**。采集并发通过 `WORKER_CONCURRENCY` 控制，轻量服务器保持 `1`。

## 本地开发

运行与发布验证使用 **CPython 3.12**、FFmpeg。`requirements.in` 声明直接依赖和安全下限，`requirements.txt` 固定全部依赖及 SHA-256；浏览器镜像与 Playwright 包版本保持一致。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
.venv/bin/python -m playwright install chromium
python3 scripts/setup_env.py
set -a
. ./.env
set +a
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

浏览器打开 `.env` 中的本地 `PUBLIC_URL`。Linux 本地运行可使用 `playwright install --with-deps chromium` 安装系统依赖；Docker 镜像已包含这些依赖。macOS 可设置 `BROWSER_CHANNEL=chrome` 使用已安装的 Chrome；此模式仍创建独立会话，不读取你的 Chrome 用户资料。

本机排查身份验证时可设置 `BROWSER_HEADLESS=false`，让独立 Chrome 窗口可见，直接在该窗口完成验证。服务器默认保持无界面模式，通过工具内画面操作；没有桌面显示服务的服务器不要设置为 `false`。涉及摄像头等无法由远程画面转发的验证，需要用户在自己的浏览器完成；当前版本不转发摄像头。

## 检查

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
node --check app/static/app.js
node --check app/static/content.js
node --check app/static/gallery.js
node tests/test_login_poll.cjs
node tests/test_content.cjs
# 可选：用合成页面检查真实浏览器的验证弹窗和输入，不访问抖音
BROWSER_CHANNEL=chrome .venv/bin/python tests/browser_login_smoke.py
```

测试使用临时数据库和明确标注的合成素材，不会读取真实抖音 Cookie。浏览器平台集成测试需要在实际部署网络和用户授权登录后另行进行。

## 数据与维护

ZIP 打包与下载期间，同一任务暂时不能重试或删除；下载中断或无效范围请求会释放导出状态，可以重新下载。

持久化目录 `/data` 包含 SQLite、`encryption.key` 和每个任务的媒体文件。完整备份这个目录才能恢复加密登录信息。ZIP 使用数据卷内临时空间，发送完成后删除；打包时需预留与任务文件大小相当的额外磁盘。

`.gitignore` 与 `.dockerignore` 排除常见凭据、数据库、浏览器状态和运行目录。文件改名或已被 Git 跟踪时仍可能漏过忽略规则，发布前还应执行下方检查。仅上传源码。管理员不通过界面读取成员的内容，但服务器运维人员拥有底层数据和密钥访问权限。

接口只接受指定抖音页面域名；资源下载和浏览器请求限制在抖音相关域名，并拒绝内网地址。代理返回 `198.18.0.0/15` Fake-IP 时，通过 Google 的 HTTPS DNS 独立复核该域名确有公网地址，复核失败仍会拒绝；仅发送公开域名，不发送 Cookie 或完整资源链接。正常公网 DNS 不需要此额外查询。公网访问应置于 HTTPS 代理之后。使用者应只归档自己有权访问和保存的内容。

项目结构：`app/` 后台与静态界面，`tests/` 离线检查，`deploy/` 代理与沙箱配置，`scripts/` 初始化和运维检查。

## 发布保护与依赖维护

初始化自己的 Git 仓库后，在本项目根目录启用随附的提交检查：

```bash
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit
python3 scripts/check_release.py --staged
```

钩子扫描整个暂存区，包括已跟踪文件和 `git add -f` 加入的文件；仅报告文件名与命中类型，不打印凭据值。默认检查覆盖常见密钥、平台令牌、登录 Cookie 和私有运行文件名，不能识别所有秘密。钩子需要每个克隆单独启用；启用前若已有 `core.hooksPath`，将本检查接入原钩子，保留原检查。发布前还要人工查看 `git diff --cached`；如凭据曾提交，删除当前文件并不能清除历史，须吊销凭据并另行清理历史。

源码检查与在线依赖公告复查：

```bash
python3 scripts/check_release.py
python3 scripts/audit_dependencies.py
```

依赖审计只向 PyPI 发送包名和版本；网络失败或存在有效公告均返回非零。它检查查询时已公布的 Python 公告，不覆盖浏览器、系统包或未知漏洞。

升级时修改 `requirements.in`，使用 uv 重新生成锁文件，更新 Dockerfile 中匹配的 Playwright 镜像版本与 digest，再安装、测试并打包：

```bash
uv pip compile requirements.in --python-version 3.12 --universal --generate-hashes --upgrade --no-header --output-file requirements.txt
```

Docker 固定浏览器基础镜像 digest；系统 FFmpeg 仍通过镜像的 Ubuntu 软件源安装。定期更新基础镜像和系统包，构建后的镜像也需要单独扫描。

## 源码交付

本目录可直接作为独立 Git 仓库。部署入口为 [HERMES.md](HERMES.md)，本机验证与服务器待验收项见 [VALIDATION.md](VALIDATION.md)。

重新生成源码包：

```bash
python3 scripts/package_source.py
```

默认在项目的上一级生成 `shiying-vault-source.zip` 和 `shiying-vault-source.zip.sha256`。脚本按文件白名单收集源码，检查常见凭证格式并拒绝符号链接。新增源码需在 `scripts/package_source.py` 登记。项目 `LICENSE`、第三方声明和依赖锁文件是打包必需文件。包内附 `SOURCE_MANIFEST.sha256`，Linux 解压后可执行 `sha256sum -c SOURCE_MANIFEST.sha256`，macOS 使用 `shasum -a 256 -c SOURCE_MANIFEST.sha256`。

## 参考

- [Playwright Python Docker](https://playwright.dev/python/docs/docker)：浏览器隔离及 Docker 沙箱配置。
- [抖音长文章结构参考](https://github.com/njausxl/douyin-image-post-ocr)：列表预览与详情正文的区别。
- 第三方配置来源及许可证见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 项目许可证

拾映的原创代码采用 **PolyForm Noncommercial 1.0.0**。你可以为个人学习等非商用目的使用、修改和分享代码。分发原版或修改版时，请附上许可全文或官方链接，并保留许可方提供的 `Required Notice:` 声明。超出许可所列用途的商业使用需另行取得权利人授权。[项目许可证全文](LICENSE) · [官方条款](https://polyformproject.org/licenses/noncommercial/1.0.0)

第三方组件分别适用其上游许可证。使用抖音平台和归档作品时，还需遵守平台条款并取得所需的内容授权。[第三方声明](THIRD_PARTY.md)
