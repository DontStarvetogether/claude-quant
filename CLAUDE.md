# Web 调试规范

## 排查 Web 端页面问题

- **重点关注服务端接口交互**：排查 web 端页面问题时，优先检查 API 请求/响应、SSE 数据流、错误状态码等与后端的交互，而不是 UI 高亮、样式、CSS 类等视觉问题。
- **使用 Chrome DevTools MCP 调试**：不要用 Playwright 写测试的方式进行排查，直接使用 Chrome DevTools（https://skills.sh/chromedevtools/chrome-devtools-mcp/chrome-devtools）进行实时调试。
