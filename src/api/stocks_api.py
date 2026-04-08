"""
股票管理API端点
支持添加、更新和批量删除
"""
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import select

from ..database import AsyncSessionLocal
from ..database.models import StockWatch, PriceCache
from ..api.akshare_client import akshare_client
from ..utils.validators import detect_exchange

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


class StockUpdateRequest(BaseModel):
    """股票更新请求"""
    full_code: str
    name: Optional[str] = None
    strike_price: float
    custom_threshold: Optional[float] = None  # 小数形式，如0.15表示15%
    notes: Optional[str] = None


class StockAddRequest(BaseModel):
    """股票添加请求"""
    symbol: str  # 6位代码，如 600519
    strike_price: float
    quantity: Optional[int] = None
    custom_threshold: Optional[float] = None
    notes: Optional[str] = None


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""
    full_codes: List[str]


@router.post("/add")
async def add_stock(request: StockAddRequest):
    """手工添加股票"""
    # 标准化股票代码
    symbol = request.symbol.strip()
    if len(symbol) != 6:
        raise HTTPException(status_code=400, detail="股票代码必须是6位数字")
    
    # 自动识别交易所
    exchange = detect_exchange(symbol)
    full_code = f"{symbol}.{exchange}"
    
    # 获取股票名称
    name = ""
    try:
        price = await akshare_client.get_price(full_code)
        if price:
            name = price.name
    except Exception:
        pass
    
    async with AsyncSessionLocal() as session:
        # 检查是否已存在
        stmt = select(StockWatch).where(
            StockWatch.symbol == symbol,
            StockWatch.exchange == exchange
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            raise HTTPException(status_code=400, detail=f"股票 {full_code} 已存在")
        
        # 创建新记录
        stock = StockWatch(
            symbol=symbol,
            exchange=exchange,
            full_code=full_code,
            name=name,
            strike_price=request.strike_price,
            quantity=request.quantity,
            custom_threshold=request.custom_threshold,
            notes=request.notes,
            is_active=True
        )
        session.add(stock)
        
        # 获取实时价格并缓存
        current_price = 0
        try:
            # 添加短暂延迟避免请求过快
            await asyncio.sleep(0.5)
            price_data = await akshare_client.get_price(full_code)
            if price_data:
                current_price = price_data.current_price
                # 检查缓存是否已存在，如果存在则更新
                stmt_cache = select(PriceCache).where(PriceCache.symbol == symbol)
                result_cache = await session.execute(stmt_cache)
                existing_cache = result_cache.scalar_one_or_none()
                
                if existing_cache:
                    existing_cache.last_price = current_price
                    existing_cache.change_percent = price_data.change_percent
                else:
                    cache = PriceCache(
                        symbol=symbol,
                        exchange=exchange,
                        full_code=full_code,
                        last_price=current_price,
                        change_percent=price_data.change_percent
                    )
                    session.add(cache)
        except Exception:
            pass
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"股票 {full_code} ({name}) 添加成功",
            "stock": {
                "full_code": full_code,
                "name": name,
                "strike_price": request.strike_price,
                "current_price": current_price,
                "quantity": request.quantity,
                "custom_threshold": request.custom_threshold,
                "notes": request.notes
            }
        }


@router.post("/update")
async def update_stock(request: StockUpdateRequest):
    """更新股票信息"""
    async with AsyncSessionLocal() as session:
        # 查找股票
        parts = request.full_code.split('.')
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail="股票代码格式错误")
        
        symbol, exchange = parts
        
        stmt = select(StockWatch).where(
            StockWatch.symbol == symbol,
            StockWatch.exchange == exchange
        )
        result = await session.execute(stmt)
        stock = result.scalar_one_or_none()
        
        if not stock:
            raise HTTPException(status_code=404, detail="股票不存在")
        
        # 更新字段
        if request.name is not None:
            stock.name = request.name
        stock.strike_price = request.strike_price
        stock.custom_threshold = request.custom_threshold
        stock.notes = request.notes
        
        await session.commit()
        
        return {
            "success": True,
            "message": "股票信息已更新",
            "stock": {
                "full_code": request.full_code,
                "name": stock.name,
                "strike_price": stock.strike_price,
                "custom_threshold": stock.custom_threshold,
                "notes": stock.notes
            }
        }


@router.post("/batch-delete")
async def batch_delete_stocks(request: BatchDeleteRequest):
    """批量删除股票"""
    async with AsyncSessionLocal() as session:
        deleted_count = 0
        
        for full_code in request.full_codes:
            parts = full_code.split('.')
            if len(parts) != 2:
                continue
            
            symbol, exchange = parts
            
            stmt = select(StockWatch).where(
                StockWatch.symbol == symbol,
                StockWatch.exchange == exchange
            )
            result = await session.execute(stmt)
            stock = result.scalar_one_or_none()
            
            if stock:
                await session.delete(stock)
                deleted_count += 1
        
        await session.commit()
        
        return {
            "success": True,
            "deleted": deleted_count,
            "message": f"已删除 {deleted_count} 只股票"
        }
