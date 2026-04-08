"""
导入API端点
"""
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
import io
import pandas as pd
from pathlib import Path
import tempfile
from sqlalchemy import select

from ..database import AsyncSessionLocal
from ..database.models import StockWatch
from ..api.akshare_client import akshare_client
from ..utils.batch_import import importer
from ..utils.validators import detect_exchange

router = APIRouter(prefix="/api/import", tags=["import"])


@router.post("/csv")
async def import_csv(file: UploadFile = File(...)):
    """导入CSV文件（简格式：symbol, name, strike_price）"""
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="只支持CSV文件")
    
    try:
        content = await file.read()
        df = pd.read_csv(io.BytesIO(content))
        
        # 验证必要字段
        required_cols = ['symbol', 'strike_price']
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise HTTPException(status_code=400, detail=f"缺少必要字段: {', '.join(missing)}")
        
        imported = 0
        updated = 0
        errors = []
        
        async with AsyncSessionLocal() as session:
            for idx, row in df.iterrows():
                try:
                    symbol = str(row['symbol']).strip()
                    if len(symbol) != 6:
                        errors.append({"row": idx+2, "message": f"股票代码 {symbol} 格式错误"})
                        continue
                    
                    exchange = detect_exchange(symbol)
                    full_code = f"{symbol}.{exchange}"
                    strike_price = float(row['strike_price'])
                    name = str(row.get('name', '')).strip() if pd.notna(row.get('name')) else ''
                    
                    # 如果没有提供名称，尝试获取
                    if not name:
                        try:
                            price_data = await akshare_client.get_price(full_code)
                            if price_data:
                                name = price_data.name
                        except:
                            pass
                    
                    # 检查是否已存在
                    stmt = select(StockWatch).where(
                        StockWatch.symbol == symbol,
                        StockWatch.exchange == exchange
                    )
                    result_db = await session.execute(stmt)
                    existing = result_db.scalar_one_or_none()
                    
                    if existing:
                        existing.strike_price = strike_price
                        if name:
                            existing.name = name
                        updated += 1
                    else:
                        stock = StockWatch(
                            symbol=symbol,
                            exchange=exchange,
                            full_code=full_code,
                            name=name,
                            strike_price=strike_price,
                            is_active=True
                        )
                        session.add(stock)
                        imported += 1
                        
                except Exception as e:
                    errors.append({"row": idx+2, "message": str(e)})
            
            await session.commit()
        
        return {
            "success": True,
            "total": len(df),
            "imported": imported,
            "updated": updated,
            "failed": len(errors),
            "errors": errors[:10]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")


@router.post("/excel")
async def import_excel(file: UploadFile = File(...)):
    """导入Excel文件"""
    if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        raise HTTPException(status_code=400, detail="只支持Excel文件(.xlsx/.xls)")
    
    tmp_path = None
    try:
        # 保存临时文件
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.xlsx', delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        # 导入数据
        result = importer.import_excel(tmp_path)

    finally:
        # 确保临时文件被删除
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()

    # 保存到数据库（同CSV逻辑）
    try:
        async with AsyncSessionLocal() as session:
            for item in result.data:
                from sqlalchemy import select
                stmt = select(StockWatch).where(
                    StockWatch.symbol == item['symbol'],
                    StockWatch.exchange == item['exchange']
                )
                result_db = await session.execute(stmt)
                existing = result_db.scalar_one_or_none()

                if existing:
                    existing.strike_price = item['strike_price']
                    existing.name = item.get('name') or existing.name
                    existing.quantity = item.get('quantity') or existing.quantity
                    existing.custom_threshold = item.get('custom_threshold') or existing.custom_threshold
                    result.updated += 1
                else:
                    stock = StockWatch(**item)
                    session.add(stock)

            await session.commit()

        return {
            "success": True,
            "total": result.total,
            "imported": result.success,
            "updated": result.updated,
            "failed": result.failed,
            "errors": [
                {"row": e.row, "field": e.field, "value": e.value, "message": e.message}
                for e in result.errors[:10]
            ] if result.errors else []
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")


@router.get("/template")
async def download_template():
    """下载导入模板"""
    template_path = Path(__file__).parent.parent.parent / "templates" / "import_template.csv"
    
    if not template_path.exists():
        raise HTTPException(status_code=404, detail="模板文件不存在")
    
    return StreamingResponse(
        open(template_path, 'rb'),
        media_type='text/csv',
        headers={'Content-Disposition': "attachment; filename=equity_import_template.csv"}
    )
