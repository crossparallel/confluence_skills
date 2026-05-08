# Skills 使用说明

本目录包含四个本地 Codex skills，用于把当前工作上下文整理成文档、查询 Confluence/Jira 内容，并把生成的 Markdown 发布到 Confluence。

```text
skills/
├── confluence-access/
├── confluence-upload/
├── context-transform/
└── jira-access/
```

## 1. 使用前准备

### 1.1 Agent 本地配置

使用前需要确保 agent 能发现本目录下的 skills。推荐做法是将三个 skill 目录放在 Codex 本地 skills 目录中，或确保当前运行环境把本目录作为 skills 根目录加载。

常见本地目录结构：

```text
C:\Users\<用户名>\.codex\skills\
├── confluence-access\
├── confluence-upload\
├── context-transform\
└── jira-access\
```

每个 skill 都必须保留自己的 `SKILL.md`：

```text
confluence-access/SKILL.md
confluence-upload/SKILL.md
context-transform/SKILL.md
jira-access/SKILL.md
```

agent 会通过 `SKILL.md` 中的 `name` 和 `description` 判断何时使用对应 skill。用户提问时也可以直接点名 skill，例如：

```text
使用 confluence-access 查一下某个页面
使用 jira-access 查一下某个 Jira 任务
用 context-transform 生成本次工作记录
用 confluence-upload 上传最新文档
```

### 1.2 Python 运行环境

三个 skill 的脚本使用 Python 运行。请先确认本机可执行：

```powershell
python --version
```

如果系统中同时安装多个 Python 版本，可使用明确路径或 `py` 启动器。运行命令时建议在对应 skill 目录下执行，例如：

```powershell
cd C:\Users\KK\Desktop\skills\confluence-access
python confluence_api.py check-config
```

### 1.3 敏感信息管理

`config.yaml` 中会包含用户名、API token、Confluence 地址和页面 ID。不要把真实 token、密码或内部地址提交到公开仓库。

建议只在本机配置真实值，对外共享时保留占位符。

### 1.4 可选配置方式

三个 skill 的配置都支持两种来源，优先级如下：

1. 本地环境变量
2. 同目录下的 `config.yaml`

支持的环境变量：

```text
CONFLUENCE_USERNAME
CONFLUENCE_API_TOKEN
CONFLUENCE_BASE_URL
```

约定如下：

- `CONFLUENCE_USERNAME` 填邮箱地址，例如 `wangyikai@example.com`。
- `confluence-access` 会把它作为鉴权账号使用完整邮箱。
- `confluence-upload` 和 `context-transform` 里需要的 `username` 是个人页标题/文档用户名，使用邮箱 `@` 前面的部分，例如 `wangyikai`。
- `CONFLUENCE_API_TOKEN` 对应 `api-token`。
- `CONFLUENCE_BASE_URL` 对应 `base_url`。

如果环境变量和 `config.yaml` 同时存在，环境变量优先覆盖。

## 2. 三个 Skills 的主要功能

### 2.1 confluence-access

`confluence-access` 用于只读访问 Confluence 内容，适合用户要求查询、搜索、读取、总结 Confluence 页面时使用。

主要能力：

- 按关键词、标题或 CQL 搜索 Confluence 页面。
- 查找或列出 Confluence 空间。
- 读取指定页面内容，并基于实际页面回答问题。
- 列出空间下页面、页面子页面和页面附件。
- 下载页面正文到本地文件。
- 将读取到的页面 JSON 缓存到 `confluence-access/cache/`。

默认工作方式：

1. 读取 `confluence-access/config.yaml`。
2. 调用 `confluence_api.py` 访问 Confluence REST API。
3. 搜索或读取目标页面。
4. 对搜索到的相关页面信息进行汇总整理。
5. 默认用正常文段语言回答用户。
6. 只有用户明确询问来源、原始结果、页面元数据或要求 JSON 时，才返回完整 JSON 信息。

重要限制：

- 默认把 Confluence 当作只读系统。
- 不要删除页面、评论或附件。
- 不要修改已有页面内容。
- 不要修改空间设置、权限、用户、组或平台配置。

常用命令：

```powershell
cd C:\Users\KK\Desktop\skills\confluence-access
python confluence_api.py check-config
python confluence_api.py search-pages "keyword" --space SPACEKEY --limit 10
python confluence_api.py get-page PAGE_ID --summary
python confluence_api.py list-attachments PAGE_ID --limit 25
```

### 2.2 jira-access

`jira-access` 用于只读访问 Jira 内容，适合用户要求查询、搜索、读取、总结 Jira issue、项目、评论、附件、工作日志、board 或 sprint 时使用。

主要能力：

- 使用 JQL 或关键词搜索 Jira issue。
- 读取 issue 详情、评论、附件列表和工作日志。
- 列出项目、搜索用户、查看 Agile board 和 sprint。
- 下载 issue 附件到本地文件。
- 将读取到的 issue JSON 缓存到 `jira-access/cache/`。

默认工作方式：

1. 优先读取 `JIRA_BASE_URL`、`JIRA_USERNAME`、`JIRA_API_TOKEN`，缺失时读取 `jira-access/config.yaml`。
2. 调用 `jira_api.py` 访问 Jira REST API。
3. 使用 Search-Read-Reflect-Refine 循环搜索、读取、反思和细化。
4. 检查是否存在缺失、冲突、矛盾或待确认内容。
5. 默认用正常文段语言回答用户。
6. 只有用户明确询问来源、原始结果、issue 元数据或要求 JSON 时，才返回完整 JSON 信息。

重要限制：

- 默认把 Jira 当作只读系统。
- 不要删除 issue、评论或附件。
- 不要修改工作流、权限、字段配置、项目设置或平台配置。
- 只有用户明确要求并确认目标 issue 和内容时，才允许添加评论或上传附件。

常用命令：

```powershell
cd C:\Users\KK\Desktop\skills\jira-access
python jira_api.py check-config
python jira_api.py search-jql "project = ABC ORDER BY updated DESC" --max-results 20
python jira_api.py search-issues "登录失败" --project ABC --max-results 20
python jira_api.py get-issue ABC-123 --summary
python jira_api.py list-comments ABC-123
python jira_api.py list-attachments ABC-123
```

### 2.3 context-transform

`context-transform` 用于把当前对话、工作过程或本地上下文整理成企业可沉淀的 Markdown 文档。

主要能力：

- 生成当前会话总结、工作记录、实施记录或交付说明。
- 生成会议纪要、技术讨论纪要、技术方案或技术决策记录。
- 生成问题复盘、异常处理说明、项目阶段总结或交接文档。
- 输出适合复制到 Confluence 的 Markdown。
- 默认将文档保存到 `context-transform/cache/`，随后继续调用 `confluence-upload` 上传到 Confluence。

默认工作方式：

1. 判断用户需要的文档类型。
2. 读取 `context-transform/config.yaml` 中的稳定元数据。
3. 梳理当前对话或本地上下文。
4. 提取目标、背景、执行过程、关键决策、异常、结果和后续建议。
5. 生成带标题和元数据表的 Markdown 文档。
6. 保存到 `context-transform/cache/`，作为上传输入。
7. 默认继续调用 `confluence-upload` 上传最新 Markdown 文档。
8. 只有用户明确提及本地总结、只生成本地文件、无需上传或不要上传时，才停止在本地文件。

默认文档必须包含元数据表：

```markdown
| 字段            | 内容       |
| --------------- | ---------- |
| 用户名          | 待确认     |
| 记录日期        | YYYY-MM-DD |
| 使用模型参数    | 待确认     |
| 使用 Agent 类别 | 待确认     |
| 文档写入位置    | 待确认     |
```

`使用模型参数` 和 `使用 Agent 类别` 必须根据本次实际运行情况现场确认后填写；无法确认时写“待确认”，不要默认填写 `codex` 或任何固定模型/agent 名称。

使用时可以这样描述需求：

```text
把本次会话整理成实施记录
生成一份问题复盘文档
将当前上下文沉淀成 Confluence 文档
```

### 2.4 confluence-upload

`confluence-upload` 用于把本地 Markdown 文档发布到 Confluence，通常作为 `context-transform` 的后续步骤。

主要能力：

- 上传 `context-transform/cache/` 中最新生成的 Markdown 文档。
- 上传指定 Markdown 文件。
- 在配置的根页面下确保存在用户个人子页面。
- 将 Markdown 转换为基础 Confluence Storage XHTML。
- 在用户个人子页面下创建新的 Confluence 页面。

默认工作方式：

```text
root_page
└── username
    └── 新建文档页面
```

上传流程：

1. 读取 `confluence-upload/config.yaml`。
2. 获取 `root_page`、`username`、`base_url` 和 token。
3. 确认根页面所属 Confluence space。
4. 检查 `root_page` 下是否存在名为 `username` 的子页面。
5. 如果不存在，则先创建用户子页面。
6. 读取 Markdown 文件并转换为 Confluence Storage Format。
7. 在用户子页面下创建新页面。
8. 返回新页面 ID、标题和 URL。

常用命令：

```powershell
cd C:\Users\KK\Desktop\skills\confluence-upload
python confluence_upload.py ensure-user-page
python confluence_upload.py upload-latest-context
python confluence_upload.py upload-markdown ..\context-transform\cache\example.md --title "文档标题"
```

## 3. Config 配置教程

### 3.1 confluence-access/config.yaml

用于查询和读取 Confluence。鉴权方式为 Basic：

```text
Authorization: Basic base64("username:api-token")
```

配置示例：

```yaml
# 用于 Basic 鉴权的 Confluence 账号。
# 如果是 Confluence Cloud，通常填写账号邮箱。
username: "your-email-or-username"

# 与 username 配套使用的 API token 或类似密码的访问令牌。
api-token: "your-api-token"

# Confluence 基础地址。
base_url: "https://your-confluence.example.com"
```

配置完成后验证：

```powershell
cd C:\Users\KK\Desktop\skills\confluence-access
python confluence_api.py check-config
python confluence_api.py list-spaces --limit 5
```

如果返回 `401`，通常是账号或 token 错误。如果返回 `403`，通常是账号权限不足。

### 3.2 context-transform/config.yaml

用于生成 Markdown 文档时填充稳定元数据。

也可以优先通过环境变量提供：

```text
CONFLUENCE_USERNAME
CONFLUENCE_API_TOKEN
CONFLUENCE_BASE_URL
```

其中 `CONFLUENCE_USERNAME` 填邮箱，文档元数据中的 `username` 使用邮箱 `@` 前面的部分；`CONFLUENCE_BASE_URL` 用作 Confluence 地址或文档写入位置；`CONFLUENCE_API_TOKEN` 仅供后续 Confluence 访问或上传流程使用，生成文档时不要写入文档内容。

配置示例：

```yaml
# 生成文档中展示的用户名。
# 中文姓名建议使用拼音，便于企业文档统一检索。
username: "your-name"

# 生成文档计划写入或维护的 Confluence 页面/空间地址。
confluence_url: "https://your-confluence.example.com"
```

字段说明：

- `username`：写入 Markdown 元数据表的用户标识。
- `confluence_url`：写入 Markdown 元数据表的文档位置或目标空间地址。

生成文档中引用 Confluence 页面来源时，应优先使用可直接访问的 Markdown 链接，例如 `[页面标题](页面URL)`；页面 ID 只能作为辅助信息，不要只给页面 ID。

如果某个值暂时无法确认，agent 应在生成文档时写“待确认”，不要编造。

### 3.3 confluence-upload/config.yaml

用于把 Markdown 上传到 Confluence。鉴权方式为 Bearer：

```text
Authorization: Bearer <api-token>
```

配置示例：

```yaml
# Confluence 用户名，同时作为默认个人子页面标题使用。
username: "your-email-or-username"

# Confluence PAT/API token。
api-token: "your-api-token"

# Confluence 基础地址。
base_url: "https://your-confluence.example.com"

# 默认根页面 ID。上传时会先在该页面下查找或创建 username 子页面。
root_page: "your-root-page-id"
```

字段说明：

- `username`：上传时使用的用户标识，也会作为根页面下的个人子页面标题。
- `api-token`：用于 Confluence 写入接口的访问令牌。
- `base_url`：Confluence 基础地址。
- `root_page`：发布入口根页面 ID。新文档会创建到 `root_page / username / 新文档页面` 层级下。

配置完成后验证：

```powershell
cd C:\Users\KK\Desktop\skills\confluence-upload
python confluence_upload.py get-page YOUR_ROOT_PAGE_ID
python confluence_upload.py ensure-user-page
```

## 4. 推荐组合流程

### 4.1 查询 Confluence 并回答问题

适用 skill：`confluence-access`

```text
用户提问
-> agent 搜索 Confluence 页面
-> agent 读取相关页面
-> agent 汇总整理全部相关页面信息
-> agent 用正常文段语言回答
-> 仅在用户追问来源或 JSON 时返回完整 JSON
```

### 4.2 生成文档并上传 Confluence

适用 skills：`context-transform` + `confluence-upload`

```text
用户要求生成文档
-> context-transform 整理当前上下文
-> 写入 context-transform/cache/*.md
-> confluence-upload 读取最新 Markdown
-> 在 root_page/username 下创建 Confluence 页面
-> 返回页面 ID、标题和 URL
```

默认上传命令：

```powershell
cd C:\Users\KK\Desktop\skills
python confluence-upload\confluence_upload.py upload-latest-context
```

## 5. 常见问题

### agent 没有自动使用 skill

确认以下事项：

- skill 目录中存在 `SKILL.md`。
- `SKILL.md` 顶部包含正确的 `name` 和 `description`。
- 当前 agent 运行环境能读取该 skills 目录。
- 用户请求与 skill 描述匹配，或用户明确点名对应 skill。

### 查询 Confluence 失败

优先检查：

- `base_url` 是否正确。
- token 是否过期或权限不足。
- 当前账号是否有目标空间或页面权限。
- 内网 Confluence 是否需要 VPN 或公司网络。

### 上传 Confluence 失败

优先检查：

- `confluence-upload/config.yaml` 的 `root_page` 是否为真实页面 ID。
- token 是否具备创建页面权限。
- `root_page` 所属空间是否允许当前账号写入。
- `context-transform/cache/` 下是否存在可上传的 `.md` 文件。

### 不想自动上传

默认情况下，生成 Markdown 文档后会继续上传到 Confluence。只有用户明确提及“本地总结”“只生成本地文件”“无需上传”“不要上传”等含义时，agent 才只使用 `context-transform`，生成完成后提示本地文件路径，并说明本次未上传。

## 6. 安全约束

- 不要在公开文档中泄露 token、密码、密钥或内部凭证。
- `confluence-access` 默认只读，不要修改或删除已有 Confluence 页面。
- `confluence-upload` 只用于创建新页面，不用于覆盖已有页面。
- 生成文档时，不要记录与任务无关的敏感个人信息。
- 用户未明确要求时，不返回完整 JSON 原始结果。
