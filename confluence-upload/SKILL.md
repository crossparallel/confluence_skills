---
name: confluence-upload
description: Upload generated Markdown documentation to Confluence by creating new pages under a configured root page and username child page. Use when Codex needs to publish the latest context-transform cache document, upload a local Markdown file, create a Confluence page, or ensure the configured username child page exists under root_page. Works as the publishing step after context-transform generates a Markdown document in context-transform/cache.
---

# Confluence Upload

当用户需要把本地 Markdown 文档发布到 Confluence 时，使用这个 skill。

它通常作为 `context-transform` 的后续步骤使用：`context-transform` 先把本次对话上下文写成 Markdown 并保存到 `context-transform/cache/`，然后 `confluence-upload` 读取最新 Markdown 文件，在 Confluence 中创建新页面。

## 配置

优先读取本地环境变量，其次读取本 skill 目录下的 `config.yaml`：

```text
CONFLUENCE_USERNAME
CONFLUENCE_API_TOKEN
CONFLUENCE_BASE_URL
```

约定：

- `CONFLUENCE_USERNAME` 填邮箱。
- 代码会自动使用邮箱 `@` 前面的部分作为配置里的 `username`。
- `CONFLUENCE_API_TOKEN` 对应 `api-token`。
- `CONFLUENCE_BASE_URL` 对应 `base_url`。
- `root_page` 仍然只从 `config.yaml` 读取。

`config.yaml` 示例：

```yaml
username: "your-email-or-username"
api-token: "your-api-token"
base_url: "https://your-domain.atlassian.net/wiki"
root_page: "your-root-page-id"
```

鉴权方式与 `confluence-access` 和可用的 `confluence.ts` 脚本一致：

```text
Authorization: Bearer <api-token>
```

上传默认层级：

```text
root_page
└── username
    └── 新建文档页面
```

如果 `root_page` 下不存在名为 `username` 的子页面，脚本会先创建该子页面。

## Workflow

1. 确认要上传的 Markdown 文件。默认使用 `context-transform/cache/` 中最新的 `.md` 文件。
2. 读取 `confluence-upload/config.yaml`，获取 `root_page`、`username`、`base_url` 和鉴权信息。
3. 访问 `root_page`，确认其所属 Confluence space。
4. 检查 `root_page` 下是否已有名为 `username` 的子页面；没有则创建。
5. 将 Markdown 转换为基础 Confluence Storage XHTML。
6. 在 `username` 子页面下创建新的 Confluence 页面并写入内容。
7. 返回新页面 ID、标题和 URL。

## Commands

上传最新的 context-transform 缓存文档：

```powershell
python confluence_upload.py upload-latest-context
```

上传指定 Markdown 文件：

```powershell
python confluence_upload.py upload-markdown path\to\doc.md --title "文档标题"
```

确保 username 子页面存在：

```powershell
python confluence_upload.py ensure-user-page
```

访问页面：

```powershell
python confluence_upload.py get-page PAGE_ID
```

## Input Contract

`context-transform` 输出的 Markdown 文件必须满足：

- 文件扩展名为 `.md`。
- 文件保存在 `context-transform/cache/` 下。
- 第一行建议是一级标题，用作默认 Confluence 页面标题。
- 内容使用标准 Markdown 标题、段落、无序列表、代码块和简单表格。

`confluence-upload` 会直接读取该 Markdown 文件，并转换为 Confluence Storage Format 后创建页面。
