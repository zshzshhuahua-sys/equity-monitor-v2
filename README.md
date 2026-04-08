# 股权激励监控面板 V2.0

本地运行的A股股权激励监控面板，批量监控股票价格与执行价格差异，提供可视化展示和自定义预警功能。

## 功能特性

- **批量导入**: CSV/Excel 导入A股代码和执行价格
- **实时价格**: AKShare 获取A股价格（新浪财经/东方财富接口）
- **价差计算**: 实时价 vs 执行价的差额和百分比
- **三级预警**: 🟢正常(<5%) 🟡关注(5-10%) 🟠警告(10-20%) 🔴严重(≥20%)
- **自定义阈值**: 全局默认 + 单股票覆盖
- **可视化面板**: Web 端实时展示和图表
- **北京时间**: 自动识别A股交易时间（北京时间）

## 技术栈

- **后端**: Python + FastAPI + SQLAlchemy + SQLite + AKShare
- **前端**: Vanilla JS + Tailwind CSS + Chart.js + DataTables
- **数据源**: AKShare（新浪财经/东方财富接口）

## 快速开始

```bash
# 使用 Docker 部署
mkdir -p secrets
printf '%s\n' '你的邮箱 app password' > secrets/smtp_password
chmod 600 secrets/smtp_password
docker compose up -d --build

# 打开浏览器访问
http://localhost:8002
```

容器启动后可用以下接口确认新调度已加载：

```bash
curl http://localhost:8002/api/crawl/schedule
```

正常应返回 `job_id=scheduled_crawl`，`cron_hours=10,22`。

## A股支持

- **沪市**: 主板(600/601/603)、科创板(688)
- **深市**: 主板(000)、创业板(300)
- **北交所**: 430/83/87/88 开头

## V2.0 升级说明

- 修复：交易时间判断使用北京时间（而非UTC）
- 修复：Excel批量导入功能（importer未定义错误）
- 新增：支持北京时间自动识别休市/开市状态
