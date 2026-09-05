# 工程运行与展示说明

## 容器

单个 FastAPI 服务，采用 Dockerfile + docker run，不需要 Compose 或数据库容器。工作目录 /app，运行用户 UID 10001，启动命令 `uvicorn main:app --host 0.0.0.0 --port 8000`。安装 Git 支持现有工具；构建不初始化 Demo、不创建 Git commit、不调用模型。

整个 /app/workspace 应挂载持久化 volume，覆盖 SQLite checkpoint、JSON 会话、审批、日志、用户文件和 Eval 证据。建议 named volume；Linux bind mount 需配置 UID 10001 写权限。备份 SQLite 时先停止写入服务，再备份整个 workspace，不要只复制活跃数据库主文件。

.env 不进入构建上下文；运行时用 `--env-file .env` 注入 DEEPSEEK_API_KEY、DEEPSEEK_BASE_URL、DEEPSEEK_MODEL。不要公开真实 Key 或含用户代码的 Eval 证据。

/health 仅检查 HTTP 服务存活，不证明模型可用。Docker HEALTHCHECK 使用 Python 标准库，无需 curl。本轮没有 Docker 命令，未构建镜像或验证容器健康。

## 测试与 CI

运行 `python -m pytest -q`、`python -m pip check`、`git diff --check`。pytest.ini 只收集 tests；learning/ 中的适配示例被部分测试导入，测试分发包不能遗漏。

旧测试有直接写 workspace 和依赖前序测试创建目录的行为。tests/conftest.py 为每个测试建立临时工作区，替换已加载模块中的工作区路径，隔离审批、日志和会话。MCP 内存服务使用临时项目；真实 stdio 调用使用临时 app 源码副本，保留子进程发现与调用验证。

网络夹具拒绝测试主进程外网 socket 连接，允许 Windows asyncio 使用 loopback；它不是对子进程的通用网络沙箱。现有 MCP 子进程仅运行本地只读服务，模型测试使用 fake。Git 工具测试在临时仓库创建测试提交，与提交本项目无关。

CI 使用临时 runner、只读 contents 权限，不保存 checkout 凭据；禁用 dotenv、清空 Key，模型地址指向本地不可用端口。不使用 secrets、不上传 artifact、不打印环境变量。不运行 eval smoke/full 或 mcp_demo.py，它们调用真实模型，有成本、网络依赖和随机性。云端 workflow 需后续 push / pull_request 验证。

## 展示与验收

README 给出只读分析、修改审批验证、MCP 概览三条路径。使用 fixture 复制准备 Demo，无需新框架或 Git commit。scripts/dev.py setup-demo 是另一条 Git 演示准备路径，会创建独立 baseline commit；本轮未执行。

先展示计划与轨迹，写入前展示 pending_action，再手动审批，最后展示真实 diff、checks 与 rollback。提前结束、跳过测试或失败应如实展示，不将 finished 或单次分数当稳定成功率。

在线 Eval runner 与后端必须共享工作区；本机 runner 指向独立容器时不能假设目录自动共享。离线重评分只读取归档，不访问当前业务工作区或模型。

## 验证记录

Day21：Python 3.13.5，全量 pytest 383 passed，pip check 通过。Docker Runtime 缺失，未真实 build / 容器 smoke；未运行真实 DeepSeek Eval。依赖尚未全部锁定，干净环境安装与云端 CI 需后续实际验证。
