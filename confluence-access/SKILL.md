---
name: confluence-access
description: Search, browse, retrieve, cache, and summarize Confluence spaces, pages, page hierarchies, comments, and attachments through a local Python helper script. For normal page-search tasks, use the views-ranked two-stage workflow by default: recall up to 50 relevance results and 50 recent results, fetch views for candidates, then read the top 5 by views. Use when Codex needs to search Confluence documentation, read a page, list spaces/pages/children, inspect attachments, download content, answer questions grounded in Confluence pages, or cache retrieved Confluence content locally. 适用于用户要求“查 Confluence 文档”“读取 wiki 页面”“在 cf 上查”“去 CF/Confluence 查”“搜索空间或页面”“在某个疑似 Confluence 空间、页面名称、wiki 名称或文档标题里查询”“获取页面内容”“列出子页面或附件”“基于 Confluence 回答问题”等场景。Do not use for deleting pages/comments, changing permissions, modifying space settings, or any administrative Confluence operation.
---

# Confluence Access

当用户需要查找、访问、读取、总结或本地缓存 Confluence 内容时，使用这个 skill。

核心脚本是 [confluence_api.py](confluence_api.py)。脚本从同目录的 [config.yaml](config.yaml) 读取鉴权配置，调用 Confluence REST API，并把读取到的页面 JSON 缓存在 [cache/](cache/) 目录下。

## 默认搜索工作流

只要任务需要“搜索 Confluence 页面并读取内容”，每一轮搜索都必须使用 views-ranked 两阶段流程。脚本已经把 `search-pages`、`search-cql` 和 JSON action `search` 改成默认执行这个流程；不要用原始搜索结果直接挑页面读取，除非 Page Information 无法解析 views、用户明确要求不用 views，或任务只是调试/列出原始候选。

默认流程：

1. 根据当前搜索目标构造关键词或 CQL。
2. 调用 `search-pages "keyword" --space SPACEKEY` 或 `search-cql 'CQL'`。这两个命令默认会按相关度召回最多 50 条、按更新时间召回最多 50 条，并合并去重。
3. 脚本读取候选页面 views，选择 views 最高的 5 条。
4. 脚本默认读取这 5 条页面正文，并返回 `topResults` 和 `pages`。
5. 对 `pages` 执行 Extract / Reflect / Refine，生成下一轮搜索目标；下一轮继续调用 `search-pages` 或 `search-cql`，不要退回 raw 搜索。

只有调试、确认 CQL、Page Information 无法解析 views 时回退，或用户明确要求原始候选列表时，才使用 `--raw`、`search-pages-raw`、`search-cql-raw` 或 JSON action `search_raw`。

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
- Data Center 不使用 analytics REST API 查询 views。脚本通过轻量 Page Information 页面 `pages/viewinfo.action?pageId=...` 读取浏览量，只解析页面信息 HTML，不下载页面正文。

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

默认搜索入口（搜索页面并准备读取正文时使用）：

```powershell
python confluence_api.py search-pages "keyword" --space SPACEKEY
python confluence_api.py search-cql 'text ~ "keyword" AND space = "SPACEKEY" AND type = page'
```

上面两个命令会执行完整默认搜索流程：按相关度召回最多 50 条、按更新时间召回最多 50 条、合并去重、读取候选 views、选择 views 最高的 5 条，并默认读取这 5 条正文。需要只看 Top 5 元数据时加 `--no-read-top`。

显式 views-ranked 命令（等价于默认搜索入口，可用于强调参数）：

```powershell
python confluence_api.py search-pages-ranked-by-views "keyword" --space SPACEKEY --recall-limit 50 --top-limit 5
python confluence_api.py search-ranked-by-views 'text ~ "keyword" AND type = page' --recall-limit 50 --top-limit 5
```

低层 raw 搜索命令（只用于调试、确认 CQL、Page Information 无法解析 views 时的回退，或用户明确要求查看原始候选列表）：

按关键词搜索页面，不读取 views 或正文：

```powershell
python confluence_api.py search-pages "keyword" --space SPACEKEY --raw --limit 10
python confluence_api.py search-pages-raw "keyword" --space SPACEKEY --limit 10
```

使用原始 CQL 搜索，不读取 views 或正文：

```powershell
python confluence_api.py search-cql 'text ~ "keyword" AND space = "SPACEKEY" AND type = page' --raw
python confluence_api.py search-cql-raw 'text ~ "keyword" AND space = "SPACEKEY" AND type = page'
```

按标题搜索页面，不读取 views：

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

获取页面浏览量：

```powershell
python confluence_api.py get-page-views PAGE_ID
python confluence_api.py get-pages-views PAGE_ID_1 PAGE_ID_2 PAGE_ID_3
```

在 Data Center 中，`get-page-views` 不调用 analytics API；它只请求轻量页面信息页 `pages/viewinfo.action?pageId=...`，从 HTML 中解析浏览量。批量查询会并发执行，但不会下载正文。若无法解析，会返回 `viewsAvailable: false` 和失败原因；不要把不可用当作 views 为 0。

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
{"action":"search","cql":"text ~ \"keyword\" AND type = page"}
{"action":"search_raw","cql":"text ~ \"keyword\" AND type = page","limit":10}
{"action":"search_ranked_by_views","cql":"text ~ \"keyword\" AND type = page","recallLimit":50,"topLimit":5,"readTop":true}
{"action":"search_pages_ranked_by_views","keyword":"keyword","spaceKey":"SPACEKEY","recallLimit":50,"topLimit":5,"readTop":true}
{"action":"search_by_title","title":"Page Title","spaceKey":"SPACEKEY"}
{"action":"get_page_views","pageId":"12345678"}
{"action":"get_pages_views","pageIds":["12345678","23456789"]}
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

搜索或列表结果只展示关键信息：标题、页面 ID、作者、空间 key/name、URL、版本号、最近更新时间；参与默认搜索流程的候选还必须包含 views 或 views 不可用原因。

搜索类任务默认采用 Search-Read-Extract-Reflect-Refine 循环，由 agent 根据任务目标自行迭代搜索、读取、提炼、反思和细化。默认最大搜索轮次为 10 轮，不要依赖固定两轮搜索策略。

**强制默认规则**：只要任务需要“搜索 Confluence 页面并读取内容”，每一轮 Search 都必须执行 views-ranked 两阶段流程。`search-pages`、`search-cql` 和 JSON action `search` 已经默认执行该流程并读取 Top 5 页面正文。不要用 `--raw`、`search-pages-raw`、`search-cql-raw` 或 `search_raw` 的前几条结果进入 Read，除非 Page Information 无法解析 views、用户明确要求不用 views，或任务只是调试/列出原始搜索结果。若跳过 views，必须在回答或工作记录中说明原因。

循环方式：

- **Search 阶段一（双路召回）**：根据当前搜索目标构造 CQL 或关键词查询。默认使用 `search-pages` 或 `search-cql`；其内部会取按相关程度排序的前最多 50 条结果，并取按更新时间排序的前最多 50 条结果，合并去重后形成最多 100 条候选。若需要手动执行，分别使用 raw CQL 搜索和带 `ORDER BY lastmodified DESC` 的 raw CQL 搜索。
- **Search 阶段二（views 排序）**：对阶段一候选批量读取 Page Information HTML 并解析 views 浏览量，按 views 从高到低选择最多 5 条结果。只对这 5 条获取完整文档内容，进入后续分析。若 Page Information 无法解析 views，说明限制，并退化为基于相关度、更新时间和标题/空间线索选择最多 5 条读取。
- **Read**：读取阶段二选出的最多 5 个页面正文。默认 `search-pages` / `search-cql` 会一次完成；需要精确控制时使用 `--no-read-top` 只返回 Top 5 元数据，再用 `get-page --summary` 逐页读取。
- **Extract**：每轮读取后必须先整理可检索的关键信息，再进入下一轮。至少整理以下内容：
	- 关键词：业务术语、系统名、模块名、流程动作词、别名/缩写、中英文同义词。
	- 编号类线索：需求号、项目号、工单号、发布版本号、规则编号、页面 ID、空间 key、Jira issue key。
	- 业务语境：适用部门/团队、上下游系统、场景边界、时间范围（例如季度、发布日期）、地域或环境（生产/测试）。
	- 实体关系：页面中出现的“系统-流程-角色-产物”关系，以及父子页面或跨平台链接（Confluence/Jira）。
	- 冲突与不确定点：术语歧义、版本不一致、口径冲突、待确认字段。
	将提炼结果整合为“下一轮检索包”：`must_terms`（必须命中）、`optional_terms`（可选扩展）、`exclude_terms`（需排除歧义）、`id_candidates`（编号候选）、`scope_hints`（空间/层级限制）。
- **Reflect**：基于提炼结果判断是否足够回答用户问题，显式检查信息是否缺失、范围是否不完整、来源是否过旧、不同页面之间是否存在冲突或矛盾。
- **Refine**：如果仍有缺失、冲突、矛盾或证据不足，必须使用上一轮“下一轮检索包”重写查询：
	- 优先把 `must_terms + id_candidates` 组合到标题搜索或 CQL 精确条件。
	- 用 `scope_hints` 收窄空间、父子页面层级或时间范围。
	- 用 `exclude_terms` 排除高频噪声词或歧义主题。
	- 再用 `optional_terms` 扩展召回，补充可能遗漏页面。
	完成后再回到 Search。

## 跨平台连续追踪

同一个搜索任务中可以连续调用 `confluence-access` 和 `jira-access`。

- 如果在 Confluence 页面正文、链接、表格、附件说明或页面标题中发现 Jira issue 链接、issue key、Jira 项目 key 或明确的 Jira 查询线索，应把这些编号加入当前轮 `id_candidates`，然后切换使用 `jira-access` 继续搜索或读取对应 Jira 内容。
- 切换到 Jira 后，应保留来源链路，例如“Confluence 页面 A 指向 Jira issue B”，并在最终回答中说明信息来自哪个平台。
- 不需要因为跨平台而重新询问用户；只要该 Jira 内容明显有助于回答当前问题，就可以继续访问。
- 如果 Jira 内容又反向指向 Confluence 页面，也可以再切回 `confluence-access`，直到 Search-Read-Extract-Reflect-Refine 循环满足停止条件。

停止条件：

- 已读取的页面内容能够覆盖用户问题的关键方面。
- 主要事实有明确来源支持。
- 已检查明显的缺失、冲突和矛盾；若仍无法消除，必须在回答中标注“不确定”“存在冲突”或“待确认”。
- 当循环达到 10 轮上限仍需继续时，必须先询问用户“是否继续搜索”；仅在用户明确同意后才能继续后续轮次。

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
2. 根据用户问题构造第一组搜索词，使用 `search-pages` 或 `search-cql` 执行默认两阶段搜索：相关度前 50 + 更新时间前 50，合并后补 views，取 views 最高的 5 条并读取正文。除非 views 不可用或用户明确要求，否则不要用 raw 搜索结果直接选页面读取。
3. 读取这 5 条页面正文，并记录页面标题、URL、空间、页面 ID 和 views 作为来源线索；如果 views 不可用，记录不可用原因并退化为读取最相关/最新的最多 5 条。
4. 每轮读取后提炼关键信息：关键词、编号、业务语境、实体关系、冲突点，并整理成“下一轮检索包”（`must_terms`/`optional_terms`/`exclude_terms`/`id_candidates`/`scope_hints`）。
5. 反思已读内容是否满足问题：是否缺信息、是否只覆盖局部、是否存在时间版本差异、是否有冲突或矛盾。
6. 如有缺失或冲突，基于“下一轮检索包”重写搜索条件继续搜索和读取；必要时搜索同一空间的父子页面、相近标题、最近更新页面或明确的 CQL 条件。
7. 重复 Search-Read-Extract-Reflect-Refine，默认最多 10 轮；达到 10 轮后如仍需继续，先询问用户是否继续。
8. 在用户同意继续后再进入第 11 轮及后续轮次；若用户不同意，则基于当前证据给出阶段性结论并标注未解决点。
9. 汇总整理已读取页面的实际内容，用正常文段语言回答用户；只有用户追问来源、原始结果或 JSON 时，才返回完整 JSON 信息。
