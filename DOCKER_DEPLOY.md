# 股权激励监控面板 - Docker 部署方案

## 一、项目概述

基于 FastAPI 的 A股股权激励监控面板，支持：
- 实时价格监控（腾讯财经API）
- 溢价率计算与预警
- 手工添加/导入股票
- 数据持久化（SQLite）

## 二、当前技术栈

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | 3.12 | 运行环境 |
| FastAPI | 0.115.0 | Web框架 |
| SQLite | - | 数据库（文件存储） |
| AKShare/腾讯API | - | 行情数据源 |

## 三、Docker 部署方案

### 3.1 创建 Dockerfile

```dockerfile
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "-c", "import os; os.environ['PROJECT_ROOT'] = '/app'; from src.main import app; import uvicorn; uvicorn.run(app, host='0.0.0.0', port=8000)"]
```

### 3.2 创建 docker-compose.yml

```yaml
version: '3.8'

services:
  equity-monitor:
    build: .
    container_name: equity-monitor
    ports:
      - "8001:8000"  # 映射到宿主机8001端口
    volumes:
      - ./data:/app/data  # 持久化数据库
      - ./config:/app/config  # 持久化配置
    environment:
      - PROJECT_ROOT=/app
    restart: unless-stopped
```

## 四、部署步骤

### 4.1 在 OrbStack 中部署

```bash
# 1. 进入项目目录
cd ~/.openclaw/workspace/equity_monitor

# 2. 构建镜像
docker build -t equity-monitor .

# 3. 运行容器
docker run -d \
  --name equity-monitor \
  -p 8001:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/config:/app/config \
  -e PROJECT_ROOT=/app \
  equity-monitor
```

### 4.2 使用 docker-compose（推荐）

```bash
# 一键启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止
docker-compose down
```

## 五、数据与配置

| 内容 | 位置 | 说明 |
|------|------|------|
| 数据库 | `./data/equity_monitor.db` | SQLite文件，映射到容器内 `/app/data` |
| 配置 | `./config/settings.yaml` | 监控间隔、预警阈值等 |
| 模板 | `./templates/` | CSV导入模板 |

## 六、访问方式

部署成功后访问：
- **本地**: http://localhost:8001
- **OrbStack VM**: http://<vm-ip>:8001

## 七、优化建议

### 7.1 必做优化

| 优化项 | 说明 | 优先级 |
|--------|------|--------|
| **配置外部化** | 将 `settings.yaml` 中的敏感信息或可变配置抽离为环境变量 | P0 |
| **健康检查** | 添加 `/health` 端点用于容器健康检查 | P0 |
| **非root运行** | 创建专用用户运行容器 | P1 |

### 7.2 可选优化

| 优化项 | 说明 | 优先级 |
|--------|------|--------|
| **数据源优化** | 目前使用腾讯API，国内访问良好，海外需代理 | P2 |
| **日志配置** | 添加结构化日志，输出到 stdout | P2 |
| **监控指标** | 可选：添加 Prometheus 监控 | P2 |

### 7.3 推荐优化后的目录结构

```
equity_monitor/
├── Dockerfile
├── docker-compose.yml
├── .env                    # 环境变量配置
├── requirements.txt
├── src/
├── web/
├── config/
│   └── settings.yaml
├── data/                   # 数据目录（映射到容器）
└── templates/
```

### 7.4 优化后的 .env 示例

```bash
# .env 文件
PROJECT_ROOT=/app
DATABASE_URL=sqlite+aiosqlite:///data/equity_monitor.db
MONITOR_INTERVAL=300
TRADING_HOURS_ONLY=false
PORT=8000
HOST=0.0.0.0
```

## 八、注意事项

1. **数据持久化**：确保 `./data` 目录映射到容器内，否则重启后数据丢失
2. **网络问题**：腾讯财经API在海外可能无法访问，需要配置代理
3. **端口冲突**：确保宿主机8001端口未被占用

## 九、验证部署

```bash
# 检查容器状态
docker ps | grep equity-monitor

# 检查日志
docker logs equity-monitor

# 测试API
curl http://localhost:8001/health
```

---

**方案评估**：整体可执行性高，项目依赖简单，Docker化难度低。推荐按上述优化后部署。
