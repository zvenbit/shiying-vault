# 发布验证记录

检查日期：2026-11-18。环境：macOS arm64、CPython 3.12.13、Node.js 24.15.0、FFmpeg 8.1.1。Python 依赖安装在全新隔离环境，使用锁文件和 `--require-hashes` 校验；没有修改系统 Python 包。

## 当前版本检查

| 检查 | 结果与范围 |
| --- | --- |
| Python 回归 | 69 项通过，包括账户与成员隔离、CSRF、SSRF、登录状态、正文解析、去重、文件重试保护、配额及取消处理 |
| ZIP 导出 | 无效 Range、ASGI 2.4 发送异常、ASGI 2.3 断开连接、打包取消均释放锁和临时文件；升级后旧的模拟请求已补齐 ASGI 协议版本 |
| 音频转换 | 使用临时合成视频调用 FFmpeg 转换并检查保存结果，未访问真实账号或作品 |
| 前端 | app.js、content.js、gallery.js 语法检查通过；登录轮询恢复和文章安全渲染两组 Node 检查通过 |
| 新版浏览器 | Playwright 1.65.2 配套 Chromium 159 的合成页面回归通过：验证弹窗、截图、文本与按键输入、父窗口恢复 |
| 发布保护 | 8 项发布回归覆盖锁文件、项目 LICENSE 与第三方声明、文件与整包校验、私有文件排除、凭据阻断、符号链接拒绝及实际 Git 暂存区扫描 |
| Python 公告 | 锁文件全部 22 个包经 PyPI 版本 JSON 元数据查询，未返回有效安全公告；查询失败会使审计失败 |
| 浏览器脚本公告 | markdown-it 及 bundle 中共 6 个组件经 npm 官方 advisory bulk 接口查询，未返回公告 |
| 第三方来源 | bundle 与官方发行文件一致；npm 归档 SHA-512 已核对；依赖版本依据上游锁文件，补齐全部组件及 Joyent / Node.js 版权文本 |
| 镜像来源 | Playwright 1.65.2 官方镜像标签及 manifest digest 已核对，包含 Linux amd64 / arm64；本机未构建 Docker 镜像 |
| 独立交付包 | 解压副本用 pip 严格校验哈希安装，pip check、69 项 Python 回归、前端检查通过；逐文件和整包 SHA-256 校验通过 |
| 实际服务启动 | 解压副本启动新版 Uvicorn，健康接口、首页和临时合成管理员登录通过；初始化 `.env` 权限为 0600，重复初始化不覆盖，输出不含密码 |

安全公告结果仅表示查询时这些数据源未返回已知问题，不是“没有漏洞”的保证。Python 审计不覆盖系统软件、浏览器二进制及容器镜像，部署方需另行扫描并定期更新。

## 可复现检查

安装运行依赖后，在项目根目录执行：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
node --check app/static/app.js
node --check app/static/content.js
node --check app/static/gallery.js
node tests/test_login_poll.cjs
node tests/test_content.cjs
python3 scripts/check_release.py
python3 scripts/audit_dependencies.py
```

已初始化 Git 并加入待提交文件时，额外执行 `python3 scripts/check_release.py --staged`。该检查读取整个 Git 暂存区的真实内容，不会因工作区已擦除凭据而误判安全；也会拦截通过 `git add -f` 强制加入的常见私有文件。

使用配套浏览器执行合成页面回归：

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/browser_login_smoke.py
```

该回归检查新验证窗口、截图、手动文本/按键输入、关闭验证窗口后恢复父窗口。所有页面由本地夹具返回，其他网络请求全部拒绝，不使用真实抖音 Cookie。

## 文档与打包边界

公开说明与测试夹具已移除真实博主、作品标题、分享链接、作品标识和个人调试记录。源码包采用逐文件白名单，附文件 SHA-256 清单和独立 ZIP 校验文件；凭据、数据库、媒体、浏览器缓存和虚拟环境均不属于交付文件。

当前目录尚未初始化 Git，因而本次不存在可供审计的历史提交。将源码放入其他已有仓库前，应另外检查该仓库的完整历史。忽略规则和提交钩子不能自动清除历史中的秘密。

源码包根目录的 `LICENSE` 保存 PolyForm Noncommercial 1.0.0 的官方英文全文。可用 `SOURCE_MANIFEST.sha256` 校验随包副本。[官方纯文本](https://polyformproject.org/licenses/noncommercial/1.0.0.txt)

## 真实平台与部署验收

此前已在本机验证真实扫码连接、视频下载与音频提取、图文和长文章保存、重复作品过滤，以及内容导航、正文复制和图集预览。公开记录只保留功能范围，不收录原始账号或采集素材。本次依赖升级的自动化检查使用合成素材，不能视为重新完成真实平台端到端验收。

本机未安装 Docker / Nginx，尚未验收目标 Linux 服务器。部署时按 [HERMES.md](HERMES.md) 完成：镜像构建、非 root Chromium 沙箱启动、HTTPS 访问、服务器网络、用户扫码、各内容类型获取与播放、重启持久化及备份恢复。抖音平台条款、账号状态、网络位置及页面变化均可能影响可用性。
