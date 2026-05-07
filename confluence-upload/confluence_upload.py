#!/usr/bin/env python3
"""将本地 Markdown 文档上传为 Confluence 页面。"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).with_name("config.yaml")
DEFAULT_CONTEXT_CACHE_DIR = Path(__file__).resolve().parent.parent / "context-transform" / "cache"
SSL_CONTEXT = ssl._create_unverified_context()


class ConfluenceUploadError(RuntimeError):
    """Confluence 上传或 API 请求失败时抛出的异常。"""


def load_config(config_path: Path = CONFIG_PATH) -> dict[str, str]:
    """默认从脚本同目录读取上传配置。"""
    data: dict[str, str] = {}

    if config_path.exists():
        data.update(_load_config_file(config_path))

    data.update(_load_env_config())

    if not data:
        raise ConfluenceUploadError(f"Config file not found: {config_path}")

    return _validate_config(data, config_path)


def _load_config_file(config_path: Path) -> dict[str, str]:
    """优先使用 PyYAML，缺失时回退到简单解析。"""
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        # 没有安装 PyYAML 时，仍可解析当前这种简单的 key/value 配置。
        return _load_simple_yaml(config_path)

    with config_path.open("r", encoding="utf-8") as config_file:
        raw_data = yaml.safe_load(config_file) or {}

    if not isinstance(raw_data, dict):
        raise ConfluenceUploadError(f"Invalid config file: {config_path}")

    return {str(key): str(value) for key, value in raw_data.items()}


def _load_simple_yaml(config_path: Path) -> dict[str, str]:
    """在不依赖第三方包的情况下解析当前的小型配置文件格式。"""
    data: dict[str, str] = {}

    with config_path.open("r", encoding="utf-8") as config_file:
        for line_number, line in enumerate(config_file, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if ":" not in stripped:
                raise ConfluenceUploadError(
                    f"Invalid config line {line_number}: expected key: value"
                )
            key, value = stripped.split(":", 1)
            data[key.strip()] = value.strip().strip("\"'")

    return data


def _load_env_config() -> dict[str, str]:
    """从本地环境变量读取优先配置。"""
    data: dict[str, str] = {}

    username = os.getenv("CONFLUENCE_USERNAME")
    api_token = os.getenv("CONFLUENCE_API_TOKEN")
    base_url = os.getenv("CONFLUENCE_BASE_URL")

    if username:
        data["username"] = _normalize_username(username)
    if api_token:
        data["api-token"] = api_token
    if base_url:
        data["base_url"] = base_url

    return data


def _validate_config(data: Any, config_path: Path) -> dict[str, str]:
    """检查并规范化上传所需的配置项。"""
    if not isinstance(data, dict):
        raise ConfluenceUploadError(f"Invalid config file: {config_path}")

    required_keys = ("username", "api-token", "base_url", "root_page")
    missing_keys = [key for key in required_keys if not data.get(key)]
    if missing_keys:
        missing = ", ".join(missing_keys)
        raise ConfluenceUploadError(f"Missing required config value(s): {missing}")

    return {
        "username": _normalize_username(str(data["username"])),
        "api-token": str(data["api-token"]),
        "base_url": str(data["base_url"]).rstrip("/"),
        "root_page": str(data["root_page"]),
    }


def _normalize_username(value: str) -> str:
    """将邮箱型用户名转换为本地拼音标识。"""
    username = value.strip()
    if "@" in username:
        username = username.split("@", 1)[0]
    return username


def build_auth_header(username: str, api_token: str) -> str:
    """根据 username 和 API token 构造 Confluence Basic 鉴权头。"""
    if api_token:
        return f"Bearer {api_token}"

    raw_token = f"{username}:{api_token}".encode("utf-8")
    encoded_token = base64.b64encode(raw_token).decode("ascii")
    return f"Basic {encoded_token}"


class ConfluenceUploader:
    """封装文档上传所需的 Confluence REST API。"""

    def __init__(self, config: dict[str, str]) -> None:
        self.username = config["username"]
        self.root_page = config["root_page"]
        self.base_url = config["base_url"].rstrip("/")
        # 认证方式与 confluence-access 保持一致。
        self.headers = {
            "Authorization": build_auth_header(config["username"], config["api-token"]),
            "Accept": "application/json",
            "X-Atlassian-Token": "no-check",
        }

    def get_page(self, page_id: str, expand: str = "space,version,ancestors") -> dict[str, Any]:
        """访问指定 Confluence 页面。"""
        return self._get_api(
            f"/content/{urllib.parse.quote(page_id)}",
            {"expand": expand},
        )

    def create_page(
        self,
        space_key: str,
        title: str,
        body: str,
        parent_id: str,
    ) -> dict[str, Any]:
        """在指定父页面下创建 Confluence 页面。"""
        payload = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "ancestors": [{"id": parent_id}],
            "body": {
                "storage": {
                    "value": body,
                    "representation": "storage",
                }
            },
        }
        return self._post_api("/content", payload)

    def find_child_page_by_title(self, parent_id: str, title: str) -> dict[str, Any] | None:
        """在指定父页面下按标题查找直接子页面。"""
        start = 0
        limit = 100

        while True:
            response = self._get_api(
                f"/content/{urllib.parse.quote(parent_id)}/child/page",
                {
                    "start": str(start),
                    "limit": str(limit),
                    "expand": "space,version",
                },
            )
            for page in response.get("results", []):
                if page.get("title") == title:
                    return page

            results = response.get("results", [])
            if len(results) < limit:
                return None
            start += len(results)

    def ensure_user_page(self, root_page_id: str | None = None) -> dict[str, Any]:
        """确保 root_page 下存在名为 username 的子页面。"""
        root_id = root_page_id or self.root_page
        root_page = self.get_page(root_id, expand="space,version")
        space_key = root_page.get("space", {}).get("key")
        if not space_key:
            raise ConfluenceUploadError(f"Root page has no space key: {root_id}")

        existing_page = self.find_child_page_by_title(root_id, self.username)
        if existing_page:
            return existing_page

        body = f"<p>{html.escape(self.username)} 的文档归档页面。</p>"
        return self.create_page(space_key, self.username, body, root_id)

    def upload_markdown(
        self,
        markdown_path: Path,
        title: str | None = None,
        root_page_id: str | None = None,
    ) -> dict[str, Any]:
        """将本地 Markdown 文件写入 username 子页面下的新 Confluence 文档。"""
        if not markdown_path.exists():
            raise ConfluenceUploadError(f"Markdown file not found: {markdown_path}")

        user_page = self.ensure_user_page(root_page_id=root_page_id)
        parent_id = str(user_page.get("id"))
        space_key = user_page.get("space", {}).get("key")
        if not space_key:
            full_user_page = self.get_page(parent_id, expand="space,version")
            space_key = full_user_page.get("space", {}).get("key")
        if not space_key:
            raise ConfluenceUploadError(f"User page has no space key: {parent_id}")

        markdown_text = markdown_path.read_text(encoding="utf-8")
        page_title = title or markdown_path.stem
        body = markdown_to_storage(markdown_text)
        return self.create_page(space_key, page_title, body, parent_id)

    def _get_api(self, api_path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        return self._request_json("GET", api_path, params=params)

    def _post_api(self, api_path: str, payload: Any) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        return self._request_json(
            "POST",
            api_path,
            body=body,
            extra_headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )

    def _request_json(
        self,
        method: str,
        api_path: str,
        params: dict[str, str] | None = None,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """向 Confluence REST API 发送请求并解析 JSON 响应。"""
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self.base_url}/rest/api{api_path}{query}"
        headers = {**self.headers, **(extra_headers or {})}
        request = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ConfluenceUploadError(
                f"Confluence API request failed with HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ConfluenceUploadError(f"Confluence API request failed: {exc.reason}") from exc

        if not response_body or not response_body.strip():
            return {}
        return json.loads(response_body)


def markdown_to_storage(markdown_text: str) -> str:
    """将常见 Markdown 语法转换为基础 Confluence Storage XHTML。"""
    lines = markdown_text.splitlines()
    blocks: list[str] = []
    paragraph_lines: list[str] = []
    list_items: list[str] = []
    table_rows: list[list[str]] = []
    code_lines: list[str] = []
    in_code_block = False

    def flush_paragraph() -> None:
        if paragraph_lines:
            text = " ".join(line.strip() for line in paragraph_lines if line.strip())
            blocks.append(f"<p>{format_inline_markdown(text)}</p>")
            paragraph_lines.clear()

    def flush_list() -> None:
        if list_items:
            items = "".join(f"<li>{format_inline_markdown(item)}</li>" for item in list_items)
            blocks.append(f"<ul>{items}</ul>")
            list_items.clear()

    def flush_table() -> None:
        if table_rows:
            row_html = []
            for row_index, row in enumerate(table_rows):
                cell_tag = "th" if row_index == 0 else "td"
                cells = "".join(
                    f"<{cell_tag}>{format_inline_markdown(cell.strip())}</{cell_tag}>"
                    for cell in row
                )
                row_html.append(f"<tr>{cells}</tr>")
            blocks.append(f"<table><tbody>{''.join(row_html)}</tbody></table>")
            table_rows.clear()

    def flush_code() -> None:
        if code_lines:
            code = html.escape("\n".join(code_lines))
            blocks.append(
                '<ac:structured-macro ac:name="code">'
                f"<ac:plain-text-body><![CDATA[{code}]]></ac:plain-text-body>"
                "</ac:structured-macro>"
            )
            code_lines.clear()

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code_block:
                flush_code()
                in_code_block = False
            else:
                flush_paragraph()
                flush_list()
                flush_table()
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            flush_table()
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_match:
            flush_paragraph()
            flush_list()
            flush_table()
            level = min(len(heading_match.group(1)), 6)
            text = format_inline_markdown(heading_match.group(2))
            blocks.append(f"<h{level}>{text}</h{level}>")
            continue

        if is_markdown_table_separator(stripped):
            continue

        table_row = parse_markdown_table_row(stripped)
        if table_row:
            flush_paragraph()
            flush_list()
            table_rows.append(table_row)
            continue

        list_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if list_match:
            flush_paragraph()
            flush_table()
            list_items.append(list_match.group(1))
            continue

        flush_table()
        paragraph_lines.append(line)

    flush_code()
    flush_paragraph()
    flush_list()
    flush_table()
    return "\n".join(blocks)


def format_inline_markdown(text: str) -> str:
    """转换少量常见的 Markdown 行内格式。"""
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def parse_markdown_table_row(line: str) -> list[str] | None:
    """解析简单 Markdown 表格行。"""
    if not line.startswith("|") or not line.endswith("|"):
        return None
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return cells if cells else None


def is_markdown_table_separator(line: str) -> bool:
    """判断是否为 Markdown 表格分隔行。"""
    if not line.startswith("|") or not line.endswith("|"):
        return False
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def find_latest_markdown(cache_dir: Path = DEFAULT_CONTEXT_CACHE_DIR) -> Path:
    """查找 context-transform/cache 下最新的 Markdown 文件。"""
    if not cache_dir.exists():
        raise ConfluenceUploadError(f"Context cache directory not found: {cache_dir}")

    markdown_files = [path for path in cache_dir.glob("*.md") if path.is_file()]
    if not markdown_files:
        raise ConfluenceUploadError(f"No Markdown files found in: {cache_dir}")

    return max(markdown_files, key=lambda path: path.stat().st_mtime)


def infer_title_from_markdown(markdown_path: Path) -> str:
    """优先使用 Markdown 的第一个一级标题作为 Confluence 页面标题。"""
    for line in markdown_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            if title:
                return title
    return markdown_path.stem


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def build_uploader(config_path: str | Path = CONFIG_PATH) -> ConfluenceUploader:
    return ConfluenceUploader(load_config(Path(config_path)))


def handle_get_page(args: argparse.Namespace) -> None:
    print_json(build_uploader(args.config).get_page(args.page_id, expand=args.expand))


def handle_ensure_user_page(args: argparse.Namespace) -> None:
    print_json(build_uploader(args.config).ensure_user_page(root_page_id=args.root_page))


def handle_create_page(args: argparse.Namespace) -> None:
    print_json(
        build_uploader(args.config).create_page(
            args.space_key,
            args.title,
            args.body,
            args.parent_id,
        )
    )


def handle_upload_markdown(args: argparse.Namespace) -> None:
    print_json(
        build_uploader(args.config).upload_markdown(
            Path(args.markdown_file),
            title=args.title,
            root_page_id=args.root_page,
        )
    )


def handle_upload_latest_context(args: argparse.Namespace) -> None:
    latest_markdown = find_latest_markdown(Path(args.cache_dir))
    title = args.title or infer_title_from_markdown(latest_markdown)
    result = build_uploader(args.config).upload_markdown(
        latest_markdown,
        title=title,
        root_page_id=args.root_page,
    )
    result["_uploaded_source"] = str(latest_markdown)
    print_json(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload local Markdown documents to Confluence."
    )
    parser.add_argument(
        "--config",
        default=str(CONFIG_PATH),
        help="Path to config.yaml. Defaults to config.yaml next to this script.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    get_page = subparsers.add_parser("get-page", help="Get a Confluence page by ID.")
    get_page.add_argument("page_id", help="Confluence page ID.")
    get_page.add_argument(
        "--expand",
        default="space,version,ancestors",
        help="Confluence expand parameter.",
    )
    get_page.set_defaults(handler=handle_get_page)

    ensure_user_page = subparsers.add_parser(
        "ensure-user-page",
        help="Ensure the username child page exists under root_page.",
    )
    ensure_user_page.add_argument("--root-page", help="Override root_page from config.")
    ensure_user_page.set_defaults(handler=handle_ensure_user_page)

    create_page = subparsers.add_parser("create-page", help="Create a Confluence page.")
    create_page.add_argument("space_key", help="Confluence space key.")
    create_page.add_argument("parent_id", help="Parent page ID.")
    create_page.add_argument("title", help="New page title.")
    create_page.add_argument("body", help="Confluence Storage Format body.")
    create_page.set_defaults(handler=handle_create_page)

    upload_markdown = subparsers.add_parser(
        "upload-markdown",
        help="Create a new Confluence page from a local Markdown file.",
    )
    upload_markdown.add_argument("markdown_file", help="Local Markdown file path.")
    upload_markdown.add_argument("--title", help="Override page title. Defaults to file stem.")
    upload_markdown.add_argument("--root-page", help="Override root_page from config.")
    upload_markdown.set_defaults(handler=handle_upload_markdown)

    upload_latest_context = subparsers.add_parser(
        "upload-latest-context",
        help="Upload the newest Markdown file from context-transform/cache.",
    )
    upload_latest_context.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CONTEXT_CACHE_DIR),
        help="Context-transform cache directory.",
    )
    upload_latest_context.add_argument("--title", help="Override page title.")
    upload_latest_context.add_argument("--root-page", help="Override root_page from config.")
    upload_latest_context.set_defaults(handler=handle_upload_latest_context)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        args.handler(args)
    except (ConfluenceUploadError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
