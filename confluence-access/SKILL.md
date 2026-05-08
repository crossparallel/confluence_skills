---
name: confluence-access
description: Search, browse, retrieve, cache, and summarize Confluence spaces, pages, page hierarchies, comments, and attachments through a local Python helper script. Use when Codex needs to search Confluence documentation, read a page, list spaces/pages/children, inspect attachments, download content, answer questions grounded in Confluence pages, or cache retrieved Confluence content locally. 适用于用户要求“查 Confluence 文档”“读取 wiki 页面”“在 cf 上查”“去 CF/Confluence 查”“搜索空间或页面”“在某个疑似 Confluence 空间、页面名称、wiki 名称或文档标题里查询”“获取页面内容”“列出子页面或附件”“基于 Confluence 回答问题”等场景。Do not use for deleting pages/comments, changing permissions, modifying space settings, or any administrative Confluence operation.
---

# Confluence Access

当用户需要查找、访问、读取、总结或本地缓存 Confluence 内容时，使用这个 skill。

核心脚本是 [confluence_api.py](confluence_api.py)。脚本从同目录的 [config.yaml](config.yaml) 读取鉴权配置，调用 Confluence REST API，并把读取到的页面 JSON 缓存在 [cache/](cache/) 目录下。

## 绝对禁止

任何情况下禁止删除或更改 Confluence 上已有的页面。

默认把 Confluence 当作只读系统使用。除非用户明确要求创建新页面、添加评论或上传附件，并且已经确认目标空间/页面和具体内容，否则不要执行任何写入操作。不要调用 `update-page` 或 JSON action `update_page` 修改已有页面。

## 使用场景

使用此 skill 处理以下任务：

- 根据关键词、标题或 CQL 搜索 Confluence 页面。
- 用户提到“在 cf 上查”“去 CF 查”“查 wiki”“查某个空间/页面/文档标题”等口语化需求。
- 在用户给出的名称疑似 Confluence 空间 key、空间名称、页面标题、wiki 名称或文档标题时，先尝试用此 skill 搜索确认。
- 查找或列出 Confluence 空间。
- 读取指定页面内容，并基于页面内容回答问题。
- 列出空间下的页面或某个页面的子页面。
- 列出页面附件，或在确认后上传附件。
- 下载页面正文到本地文件。
- 将读取到的页面内容缓存为 JSON，便于后续本地查看。
- 解析页面正文中的 Confluence 用户引用，并尽量映射为显示名称。

不要使用此 skill 处理以下任务：

- 删除页面、评论或附件。
- 修改已有页面内容。
- 修改空间设置、权限、用户、组或平台配置。
- 执行任何管理员级 Confluence 操作。

## 配置

优先读取本地环境变量，其次读取 `confluence-access/config.yaml`：

```text
CONFLUENCE_USERNAME
CONFLUENCE_API_TOKEN
CONFLUENCE_BASE_URL
```

约定：

- `CONFLUENCE_USERNAME` 填邮箱。
- `confluence-access` 会把它作为鉴权账号使用完整邮箱。
- 如果没有环境变量，再读取 `config.yaml`。

`config.yaml` 示例：

```yaml
username: "your-email-or-username"
api-token: "your-api-token"
base_url: "https://your-confluence.example.com"
```

鉴权方式固定为：

```text
Authorization: Basic base64("username:api-token")
```

开始实际查询前，可以先检查配置：

```powershell
python confluence_api.py check-config
```

## 调用方式

脚本支持两种调用方式。

CLI 子命令方式：

```powershell
python confluence_api.py <command> [args]
```

JSON 管道方式：

```powershell
'{"action":"check_config"}' | python confluence_api.py
```

人工调试时优先使用 CLI 子命令。需要由其他脚本或 agent 结构化调用时，优先使用 JSON 管道方式。

## 常用只读命令

按关键词搜索页面：

```powershell
python confluence_api.py search-pages "keyword" --space SPACEKEY --limit 10
```

使用原始 CQL 搜索：

```powershell
python confluence_api.py search-cql 'text ~ "keyword" AND space = "SPACEKEY" AND type = page'
```

按标题搜索页面：

```powershell
python confluence_api.py search-by-title "Page Title" --space SPACEKEY
```

搜索空间：

```powershell
python confluence_api.py search-spaces "keyword" --limit 10
```

列出空间：

```powershell
python confluence_api.py list-spaces --limit 50
```

列出空间下的页面：

```powershell
python confluence_api.py list-pages SPACEKEY --limit 25
```

列出子页面：

```powershell
python confluence_api.py list-children PAGE_ID --limit 25
```

读取页面：

```powershell
python confluence_api.py get-page PAGE_ID --summary
```

按空间和精确标题读取页面：

```powershell
python confluence_api.py get-page-by-title SPACEKEY "Page Title"
```

下载页面正文：

```powershell
python confluence_api.py download-page PAGE_ID output.html
python confluence_api.py download-page PAGE_ID output.txt --text
```

列出附件：

```powershell
python confluence_api.py list-attachments PAGE_ID --limit 25
```

## JSON Actions

常用只读 action：

```json
{"action":"search","cql":"text ~ \"keyword\" AND type = page","limit":10}
{"action":"search_by_title","title":"Page Title","spaceKey":"SPACEKEY"}
{"action":"get_page","pageId":"12345678"}
{"action":"list_spaces","limit":50}
{"action":"list_pages","spaceKey":"SPACEKEY","limit":25}
{"action":"list_children","pageId":"12345678","limit":25}
{"action":"list_attachments","pageId":"12345678","limit":25}
```

脚本中也提供以下写入 action，但必须谨慎使用：

```json
{"action":"create_page","spaceKey":"SPACEKEY","title":"Title","body":"<p>Content</p>","parentId":"12345678"}
{"action":"add_comment","pageId":"12345678","body":"<p>Comment</p>"}
{"action":"upload_attachment","pageId":"12345678","filePath":"C:\\path\\file.pdf","comment":"optional"}
```

不要使用 `update_page`。任何情况下禁止删除或更改 Confluence 上已有的页面。

## 缓存行为

通过以下方式读取页面时，脚本会自动把页面内容保存为 JSON：

- `get-page`
- `get-page-by-title`
- `download-page`
- JSON action `get_page`

缓存路径：

```text
confluence-access/cache/page-<page_id>.json
```

如果后续任务需要查看同一页面，可以优先读取缓存。若用户强调内容必须最新，重新调用 Confluence API 刷新缓存。

## 回答规范

搜索或列表结果只展示关键信息：标题、页面 ID、作者、空间 key/name、URL、版本号、最近更新时间。

搜索类任务默认采用 Search-Read-Reflect-Refine 循环，由 agent 根据任务目标自行迭代搜索、读取、反思和细化。不要依赖固定两轮搜索策略。

循环方式：

- **Search**：用 `search-pages`、`search-by-title`、`search-cql`、`list-pages`、`list-children` 等基础命令定位候选页面。搜索或列表结果只展示关键信息：标题、页面 ID、作者、空间、URL、版本号、最近更新时间。
- **Read**：读取最相关、最可能回答问题的页面正文。优先使用 `get-page --summary`，必要时读取多个互补页面。
- **Reflect**：基于已读内容判断是否足够回答用户问题，显式检查信息是否缺失、范围是否不完整、来源是否过旧、不同页面之间是否存在冲突或矛盾。
- **Refine**：如果仍有缺失、冲突、矛盾或证据不足，根据已掌握信息调整下一步搜索词、空间、标题词、CQL 条件或页面层级，再回到 Search。

## 跨平台连续追踪

同一个搜索任务中可以连续调用 `confluence-access` 和 `jira-access`。

- 如果在 Confluence 页面正文、链接、表格、附件说明或页面标题中发现 Jira issue 链接、issue key、Jira 项目 key 或明确的 Jira 查询线索，可以切换使用 `jira-access` 继续搜索或读取对应 Jira 内容。
- 切换到 Jira 后，应保留来源链路，例如“Confluence 页面 A 指向 Jira issue B”，并在最终回答中说明信息来自哪个平台。
- 不需要因为跨平台而重新询问用户；只要该 Jira 内容明显有助于回答当前问题，就可以继续访问。
- 如果 Jira 内容又反向指向 Confluence 页面，也可以再切回 `confluence-access`，直到 Search-Read-Reflect-Refine 循环满足停止条件。

停止条件：

- 已读取的页面内容能够覆盖用户问题的关键方面。
- 主要事实有明确来源支持。
- 已检查明显的缺失、冲突和矛盾；若仍无法消除，必须在回答中标注“不确定”“存在冲突”或“待确认”。

读取页面后，回答必须基于实际页面内容，用正常文段语言总结；不要把完整 JSON 原始结果作为默认回复。

只有当用户明确询问具体内容的来源、要求查看原始检索结果、要求核对页面元数据，或要求返回 JSON 时，才返回 JSON 格式的全部页面信息。需要说明来源时，可在文段回答中引用页面标题、页面 ID 或 URL；如果用户要求完整来源，再给出完整 JSON。

使用 `get-page --summary` 时，脚本会尝试把正文中的 `ri:userkey` 解析为显示名称。

如果遇到错误，说明明确的阻塞原因：

- `401`：凭据或 token 错误。
- `403`：账号权限不足。
- `404`：页面、空间或附件不存在。
- `409`：版本冲突；不要通过更新页面来重试。

## 推荐流程

1. 如果不确定配置是否正确，先运行 `check-config`。
2. 根据用户问题构造第一组搜索词，使用 `search-pages`、`search-by-title` 或 `search-cql` 定位候选页面。
3. 读取最相关页面正文，并记录页面标题、URL、空间和页面 ID 作为来源。
4. 反思已读内容是否满足问题：是否缺信息、是否只覆盖局部、是否存在时间版本差异、是否有冲突或矛盾。
5. 如有缺失或冲突，调整搜索条件继续搜索和读取；必要时搜索同一空间的父子页面、相近标题、最近更新页面或明确的 CQL 条件。
6. 重复 Search-Read-Reflect-Refine，直到认为结果足以回答，或确认剩余问题无法从可访问文档中消除。
7. 汇总整理已读取页面的实际内容，用正常文段语言回答用户；只有用户追问来源、原始结果或 JSON 时，才返回完整 JSON 信息。
