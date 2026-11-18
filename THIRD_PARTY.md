# 第三方组件与声明

本文件记录源码包随附的第三方组件和部署时安装的依赖。第三方组件适用下文各自列明的上游许可证。项目原创代码采用 PolyForm Noncommercial 1.0.0。[项目许可证](LICENSE)

## 随源码分发：浏览器 Markdown 组件

`app/static/vendor/markdown-it.min.js` 是 markdown-it 15.0.3 官方 npm 发行包的浏览器 UMD 压缩文件，未修改。下载时核对发行包 SHA-512，并核对本地 bundle 的 SHA-256；来源、完整性值及组件版本记录在 `app/static/vendor/markdown-it-source.json`。

bundle 的 source map 确认包含下列组件，版本依据上游 15.0.3 标签下的 [package-lock.json](https://github.com/markdown-it/markdown-it/blob/15.0.3/package-lock.json)。CLI 专用 argparse 未进入浏览器 bundle。

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| markdown-it | 15.0.3 | MIT |
| entities | 8.1.0 | BSD-2-Clause |
| linkify-it | 6.1.0 | MIT |
| mdurl | 2.1.0 | MIT |
| punycode.js | 2.3.1 | MIT |
| uc.micro | 3.0.0 | MIT |

全部原始版权与许可文本见 [THIRD-PARTY-NOTICES.txt](app/static/vendor/THIRD-PARTY-NOTICES.txt)，包括 mdurl 中继承的 Joyent / Node.js URL parser MIT 声明；markdown-it 原始 LICENSE 另保留在 `app/static/vendor/MARKDOWN-IT-LICENSE`。分发 bundle 时应一并保留这些声明。浏览器运行时不使用外部 CDN。

## 随源码分发：Playwright 沙箱配置

`deploy/seccomp_profile.json` 与 Microsoft Playwright [官方固定提交文件](https://github.com/microsoft/playwright/blob/ae935a43d9e376e4759548f6b3c6905c7b282333/utils/docker/seccomp_profile.json) 内容一致。该文件使用 Apache-2.0，完整文本见 [PLAYWRIGHT-LICENSE](deploy/PLAYWRIGHT-LICENSE)。

## 部署时安装：Python 依赖

这些 Python 包不嵌入源码 ZIP，安装时从软件源获取。`requirements.txt` 锁定全部版本及分发文件哈希，`requirements.in` 记录直接依赖与安全下限。以下许可证来自相应版本的 PyPI 官方元数据；其 wheel 中的 LICENSE / NOTICE、底层库及附带二进制许可也应保留。若分发虚拟环境或容器镜像，须继续满足这些许可的分发要求。

| 包 | 固定版本 | 上游声明许可证 |
| --- | --- | --- |
| [annotated-doc](https://pypi.org/project/annotated-doc/0.0.6/) | 0.0.6 | MIT |
| [annotated-types](https://pypi.org/project/annotated-types/0.8.0/) | 0.8.0 | MIT |
| [anyio](https://pypi.org/project/anyio/4.16.0/) | 4.16.0 | MIT |
| [certifi](https://pypi.org/project/certifi/2026.10.23/) | 2026.10.23 | MPL-2.0 |
| [cffi](https://pypi.org/project/cffi/2.1.1/) | 2.1.1 | MIT-0 |
| [click](https://pypi.org/project/click/8.5.1/) | 8.5.1 | BSD-3-Clause |
| [cryptography](https://pypi.org/project/cryptography/50.0.2/) | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| [fastapi](https://pypi.org/project/fastapi/0.163.2/) | 0.163.2 | MIT |
| [greenlet](https://pypi.org/project/greenlet/3.5.6/) | 3.5.6 | MIT AND PSF-2.0 |
| [h11](https://pypi.org/project/h11/0.16.0/) | 0.16.0 | MIT |
| [httpcore](https://pypi.org/project/httpcore/1.0.9/) | 1.0.9 | BSD-3-Clause |
| [httpx](https://pypi.org/project/httpx/0.28.1/) | 0.28.1 | BSD-3-Clause |
| [idna](https://pypi.org/project/idna/3.20/) | 3.20 | BSD-3-Clause |
| [opentelemetry-api](https://pypi.org/project/opentelemetry-api/1.45.2/) | 1.45.2 | Apache-2.0 |
| [playwright](https://pypi.org/project/playwright/1.65.2/) | 1.65.2 | Apache-2.0 |
| [pycparser](https://pypi.org/project/pycparser/3.12/) | 3.12 | BSD-3-Clause |
| [pydantic](https://pypi.org/project/pydantic/2.14.3/) | 2.14.3 | MIT |
| [pydantic-core](https://pypi.org/project/pydantic-core/2.50.4/) | 2.50.4 | MIT |
| [starlette](https://pypi.org/project/starlette/1.10.0/) | 1.10.0 | BSD-3-Clause |
| [typing-extensions](https://pypi.org/project/typing-extensions/4.16.0/) | 4.16.0 | PSF-2.0 |
| [typing-inspection](https://pypi.org/project/typing-inspection/0.4.5/) | 0.4.5 | MIT |
| [uvicorn](https://pypi.org/project/uvicorn/0.54.0/) | 0.54.0 | BSD-3-Clause |

## 部署时安装：系统与浏览器

- Playwright 官方 Python 镜像包含 Python、Ubuntu 系统组件、浏览器和驱动，各有自己的版权声明。Dockerfile 固定版本和镜像 digest；这不将整个镜像改为 Apache-2.0。自行发布镜像时需保留镜像内已有声明，并检查所包含组件的源代码提供义务。
- FFmpeg 通过 Ubuntu 软件源安装，实际许可证取决于构建选项。请以 `ffmpeg -L`、`ffmpeg -buildconf` 及镜像内 `/usr/share/doc/ffmpeg/` 为准，参见 [FFmpeg 官方法律说明](https://www.ffmpeg.org/legal.html)。本源码交付不包含 FFmpeg 二进制；分发含 FFmpeg 的镜像时还需满足该构建的 LGPL / GPL 及对应源代码要求。

## 许可边界

本项目未获得抖音官方授权，也不隶属抖音。平台名称仅用于说明兼容对象。软件代码的许可不包含平台使用权、账户权限、音乐、图片、视频或文章的转载许可。使用者应确认自己的使用符合平台条款并已获得所需内容权利。
