"""Day18 MCP 外部工具接入（本地 stdio，只读演示）。

包结构：
- server.py：项目自己的本地 MCP 服务端（只读 project_overview 工具）；
- client.py：MCP 客户端连接工厂（stdio 子进程 / 内存服务端两种形态）；
- bridge.py：MCP → Agent 工具转换层（命名空间、allowlist、风险登记、
  Schema 转换、同步调用适配器与错误类型）；
- runtime.py：把 MCP 扩展组装进 LangGraph v2 runtime 的装配器。
"""
