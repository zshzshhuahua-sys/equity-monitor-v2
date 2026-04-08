"""
监控API端点
"""
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from sqlalchemy import select, desc
from pydantic import BaseModel

from ..database import AsyncSessionLocal
from ..database.models import StockWatch, PriceCache, AlertLog
from ..core.price_fetcher import price_fetcher
from ..core.monitor_service import monitor_service
from ..core.diff_calculator import diff_calculator, AlertLevel
from ..core.alert_rules import alert_engine

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


# ============ 请求/响应模型 ============

class PriceResponse(BaseModel):
    id: int
    symbol: str
    full_code: str
    name: Optional[str]
    current_price: float
    strike_price: float
    diff_amount: float
    diff_percent: float
    alert_level: str
    is_profitable: bool
    notes: Optional[str] = None
    created_at: Optional[str] = None


class AlertResponse(BaseModel):
    id: int
    symbol: str
    full_code: str
    name: Optional[str]
    alert_type: str
    threshold_value: float
    trigger_price: float
    price_diff_percent: float
    is_acknowledged: bool
    created_at: Optional[str] = None


class MonitorStatusResponse(BaseModel):
    is_running: bool
    is_trading_time: bool
    interval_seconds: int
    trading_hours_only: bool


# ============ API端点 ============

@router.get("/prices", response_model=List[PriceResponse])
async def get_prices():
    """获取所有监控股票的当前价格和价差"""
    async with AsyncSessionLocal() as session:
        # 分别查询监控列表和价格缓存
        stmt_stocks = select(StockWatch).where(StockWatch.is_active == True)
        result_stocks = await session.execute(stmt_stocks)
        stocks = result_stocks.scalars().all()
        
        stmt_cache = select(PriceCache)
        result_cache = await session.execute(stmt_cache)
        caches = {c.symbol: c for c in result_cache.scalars().all()}
        
        prices = []

        # 重新按created_at排序并分配连续序号
        sorted_stocks = sorted(stocks, key=lambda s: s.created_at if s.created_at else datetime.min)
        
        # 构建结果并分配序号
        for idx, stock in enumerate(sorted_stocks, 1):
            cache = caches.get(stock.symbol)
            
            if cache:
                diff = diff_calculator.calculate(
                    current_price=cache.last_price,
                    strike_price=stock.strike_price,
                    symbol=stock.symbol,
                    full_code=stock.full_code,
                    name=stock.name
                )
                
                prices.append(PriceResponse(
                    id=idx,  # 使用连续的序号
                    symbol=stock.symbol,
                    full_code=stock.full_code,
                    name=stock.name,
                    current_price=cache.last_price,
                    strike_price=stock.strike_price,
                    diff_amount=diff.diff_amount,
                    diff_percent=diff.diff_percent,
                    alert_level=diff.alert_level.value,
                    is_profitable=diff.is_profitable,
                    notes=stock.notes,
                    created_at=stock.created_at.isoformat() if stock.created_at else ""
                ))
            else:
                prices.append(PriceResponse(
                    id=idx,
                    symbol=stock.symbol,
                    full_code=stock.full_code,
                    name=stock.name,
                    current_price=0.0,
                    strike_price=stock.strike_price,
                    diff_amount=-stock.strike_price,
                    diff_percent=-100.0,
                    alert_level="unknown",
                    is_profitable=False,
                    notes=stock.notes,
                    created_at=stock.created_at.isoformat() if stock.created_at else ""
                ))
        
        return prices


@router.get("/status", response_model=MonitorStatusResponse)
async def get_monitor_status():
    """获取监控服务状态"""
    from ..config import settings
    
    return MonitorStatusResponse(
        is_running=monitor_service.is_running(),
        is_trading_time=price_fetcher.is_trading_time(),
        interval_seconds=settings.monitor.interval_seconds,
        trading_hours_only=settings.monitor.trading_hours_only
    )


@router.post("/start")
async def start_monitor():
    """启动监控服务"""
    if monitor_service.is_running():
        raise HTTPException(status_code=400, detail="监控服务已在运行")
    
    await monitor_service.start()
    return {"success": True, "message": "监控服务已启动"}


@router.post("/stop")
async def stop_monitor():
    """停止监控服务"""
    if not monitor_service.is_running():
        raise HTTPException(status_code=400, detail="监控服务未运行")
    
    await monitor_service.stop()
    return {"success": True, "message": "监控服务已停止"}


@router.post("/refresh")
async def refresh_prices():
    """手动刷新价格"""
    try:
        prices = await price_fetcher.fetch_watchlist_prices()
        await price_fetcher.update_price_cache(prices)
        return {"success": True, "count": len(prices), "message": f"已刷新 {len(prices)} 只股票价格"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新失败: {str(e)}")


# ============ 预警相关API ============

@router.get("/alerts", response_model=List[AlertResponse])
async def get_alerts(
    acknowledged: Optional[bool] = None,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0)
):
    """获取预警记录"""
    async with AsyncSessionLocal() as session:
        # 查询预警记录
        stmt = select(AlertLog).order_by(desc(AlertLog.created_at)).limit(limit).offset(offset)
        
        if acknowledged is not None:
            stmt = stmt.where(AlertLog.is_acknowledged == acknowledged)
        
        result = await session.execute(stmt)
        alerts_data = result.scalars().all()
        
        # 获取关联的股票信息
        stock_ids = [a.stock_id for a in alerts_data]
        stmt_stocks = select(StockWatch).where(StockWatch.id.in_(stock_ids))
        result_stocks = await session.execute(stmt_stocks)
        stocks = {s.id: s for s in result_stocks.scalars().all()}
        
        alerts = []
        for alert in alerts_data:
            stock = stocks.get(alert.stock_id)
            alerts.append(AlertResponse(
                id=alert.id,
                symbol=stock.symbol if stock else "",
                full_code=stock.full_code if stock else "",
                name=stock.name if stock else None,
                alert_type=alert.alert_type,
                threshold_value=alert.threshold_value,
                trigger_price=alert.trigger_price,
                price_diff_percent=alert.price_diff_percent,
                is_acknowledged=alert.is_acknowledged,
                created_at=alert.created_at.isoformat() if alert.created_at else ""
            ))
        
        return alerts


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int):
    """确认预警（标记为已读）"""
    async with AsyncSessionLocal() as session:
        from datetime import datetime
        
        stmt = select(AlertLog).where(AlertLog.id == alert_id)
        result = await session.execute(stmt)
        alert = result.scalar_one_or_none()
        
        if not alert:
            raise HTTPException(status_code=404, detail="预警记录不存在")
        
        alert.is_acknowledged = True
        alert.acknowledged_at = datetime.utcnow()
        await session.commit()
        
        return {"success": True, "message": "预警已确认"}


@router.post("/alerts/acknowledge-all")
async def acknowledge_all_alerts():
    """确认所有未读预警"""
    async with AsyncSessionLocal() as session:
        from datetime import datetime
        from sqlalchemy import update
        
        stmt = update(AlertLog).where(
            AlertLog.is_acknowledged == False
        ).values(
            is_acknowledged=True,
            acknowledged_at=datetime.utcnow()
        )
        
        result = await session.execute(stmt)
        await session.commit()
        
        return {"success": True, "count": result.rowcount, "message": f"已确认 {result.rowcount} 条预警"}


@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: int):
    """删除预警记录"""
    async with AsyncSessionLocal() as session:
        stmt = select(AlertLog).where(AlertLog.id == alert_id)
        result = await session.execute(stmt)
        alert = result.scalar_one_or_none()
        
        if not alert:
            raise HTTPException(status_code=404, detail="预警记录不存在")
        
        await session.delete(alert)
        await session.commit()
        
        return {"success": True, "message": "预警已删除"}
