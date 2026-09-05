# 项目路线图

当前定位是可展示、可审计、可评估、可恢复的单机 Coding Agent Runtime。历史见 [CHANGELOG](../CHANGELOG.md)，当前能力与运行方式见 [README](../README.md)。不为本轮收尾虚构新版本号。

## 已实现

- 规则 Planner、明确写意图识别、Executor 步骤约束与审计。
- workspace 路径规范化、策略检查、写入前审批、验证与快照回滚。
- Reflection 与有限重试；原生 Loop、SSE、LangGraph v1 / v2 并行。
- 独立 SQLite checkpoint、v2 thread 恢复与不存在 thread 的 404。
- Context Builder：完整状态保留、请求侧压缩、工具消息协议顺序保护。
- AST 结构与 Python 依赖分析；本地只读 MCP 接入。
- 16 个 Golden Tasks、12 指标、Evidence、offline rescoring、compare。
- Day21：Dockerfile、构建上下文隔离、GitHub Actions 配置、中文作品集 README 与 Demo 文档。Docker build 和云端 CI 尚未实际运行。

## 后续方向

1. **Observability**：统一事件、耗时与失败定位，先补可观测性再扩展规模。
2. **Codebase Retrieval / RAG**：在 AST 与关键词分析上评估仓库检索收益。
3. **Multi-agent Review（optional）**：先解决单运行时可靠性，再评估独立审查收益。

## 当前边界

规则规划、premature final、长上下文预算和验证覆盖仍有限；MCP 只有一个本地只读工具，默认 v2 不注入；SQLite / JSON 不提供完整生产多租户保障。无认证、限流、云部署与不可信代码隔离。详细限制见 README。
