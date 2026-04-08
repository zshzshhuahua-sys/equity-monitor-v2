-- 初始数据库迁移
-- 创建股权激励监控面板所需的表

-- 监控股票表
CREATE TABLE IF NOT EXISTS stock_watch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(6) NOT NULL,
    exchange VARCHAR(2) NOT NULL,
    full_code VARCHAR(9) NOT NULL,
    name VARCHAR(20),
    strike_price FLOAT NOT NULL,
    quantity INTEGER,
    custom_threshold FLOAT,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, exchange)
);

-- 价格缓存表
CREATE TABLE IF NOT EXISTS price_cache (
    symbol VARCHAR(6) PRIMARY KEY,
    exchange VARCHAR(2) NOT NULL,
    full_code VARCHAR(9) NOT NULL,
    last_price FLOAT NOT NULL,
    change_percent FLOAT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 预警记录表
CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    alert_type VARCHAR(20) NOT NULL,
    threshold_value FLOAT NOT NULL,
    trigger_price FLOAT NOT NULL,
    price_diff_percent FLOAT NOT NULL,
    is_acknowledged BOOLEAN DEFAULT 0,
    acknowledged_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (stock_id) REFERENCES stock_watch(id)
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_stock_watch_active ON stock_watch(is_active);
CREATE INDEX IF NOT EXISTS idx_stock_watch_created ON stock_watch(created_at);
CREATE INDEX IF NOT EXISTS idx_price_cache_updated ON price_cache(last_updated);
CREATE INDEX IF NOT EXISTS idx_alert_log_stock_created ON alert_log(stock_id, created_at);
CREATE INDEX IF NOT EXISTS idx_alert_log_acknowledged ON alert_log(is_acknowledged);
