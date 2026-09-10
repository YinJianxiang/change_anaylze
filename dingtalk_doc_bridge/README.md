# 钉钉文档 Bridge（包 MCP，无 AppKey）

把 Cursor 里「钉钉文档」MCP（streamable-http）包成 HTTP，供 `task-worker` 的
`DINGTALK_DOC_BRIDGE_URL` 调用。不使用钉钉开放平台 AppKey/AppSecret。

## 1. 从 Cursor 复制 MCP 地址

打开 `%USERPROFILE%\.cursor\mcp.json`，找到：

```json
"钉钉文档": {
  "type": "streamable-http",
  "url": "https://mcp-gw.dingtalk.com/server/...?key=..."
}
```

把完整 `url`（含 `key=`）写入 change_analyze `.env`：

```env
DINGTALK_MCP_URL=https://mcp-gw.dingtalk.com/server/...?key=...
DINGTALK_DOC_BRIDGE_TOKEN=change-me
DINGTALK_DOC_BRIDGE_URL=http://127.0.0.1:8091/v1/doc/content
```

`key` 等同访问凭证，勿提交到 git。

## 2. 启动 Bridge

```powershell
cd D:\Project\change_analyze
pip install -r dingtalk_doc_bridge\requirements.txt
$env:DINGTALK_MCP_URL="粘贴mcp.json里的url"
$env:DINGTALK_DOC_BRIDGE_TOKEN="change-me"
python -m dingtalk_doc_bridge --port 8091
```

健康检查：`GET http://127.0.0.1:8091/health`

## 3. 手动试拉

```powershell
curl -X POST http://127.0.0.1:8091/v1/doc/content `
  -H "Authorization: Bearer change-me" `
  -H "Content-Type: application/json" `
  -d "{\"nodeId\":\"https://alidocs.dingtalk.com/i/nodes/你的文档ID\",\"format\":\"markdown\"}"
```

成功时返回 `{ "title", "markdown", "content", "nodeId", "source": "dingtalk_mcp" }`。

## 4. 接到 task-worker

`.env` 中保持：

```env
REQUIREMENT_FETCH_ENABLED=true
DINGTALK_DOC_BRIDGE_URL=http://127.0.0.1:8091/v1/doc/content
DINGTALK_DOC_BRIDGE_TOKEN=change-me
```

**不要**再配 `DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET`（有 Bridge URL 时 worker 只走 Bridge）。

Docker Compose 下把 URL 改成 `http://dingtalk-doc-bridge:8091/v1/doc/content`，并启用 profile `dingtalk`。

## 注意

- Bridge 依赖钉钉 MCP 网关的 `key` 有效；过期后需在 Cursor 重新授权并更新 `DINGTALK_MCP_URL`。
- Bridge 需与 worker 同时运行；Cursor 不必打开。
