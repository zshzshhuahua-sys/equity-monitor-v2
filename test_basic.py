# 股权激励监控面板 - 测试脚本
"""
测试基础架构功能
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.database import init_db, close_db, AsyncSessionLocal
from src.database.models import StockWatch
from src.api.akshare_client import akshare_client
from src.utils.batch_import import importer


async def _test_database():
    """测试数据库"""
    print("🗄️ 测试数据库...")
    await init_db()
    print("✅ 数据库初始化成功")
    
    async with AsyncSessionLocal() as session:
        # 添加测试数据
        test_stock = StockWatch(
            symbol="000001",
            exchange="SZ",
            full_code="000001.SZ",
            name="平安银行",
            strike_price=10.50,
            is_active=True
        )
        session.add(test_stock)
        await session.commit()
        print(f"✅ 测试数据插入成功: {test_stock.full_code}")
    
    await close_db()
    print("✅ 数据库测试完成")


async def _test_akshare():
    """测试AKShare"""
    print("\n📈 测试AKShare...")
    try:
        # 测试单只股票
        price = await akshare_client.get_price("000001")
        if price:
            print(f"✅ 单只股票查询成功: {price.full_code} = {price.current_price}")
        else:
            print("❌ 单只股票查询失败")
        
        # 测试批量查询
        prices = await akshare_client.get_prices_batch(["000001", "600519", "300750"])
        print(f"✅ 批量查询成功: 获取 {len(prices)} 只股票")
        for code, p in list(prices.items())[:3]:
            print(f"   {code}: {p.current_price}")
            
    except Exception as e:
        print(f"❌ AKShare测试失败: {e}")


def _test_import():
    """测试导入功能"""
    print("\n📥 测试导入功能...")
    test_csv = """symbol,name,strike_price,quantity,custom_threshold
000001,平安银行,10.50,1000,0.15
600519,贵州茅台,1500.00,500,0.10
300750,宁德时代,200.00,2000,
invalid_code,无效股票,100,,"""
    
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(test_csv)
        temp_path = f.name
    
    result = importer.import_csv(Path(temp_path))
    print(f"✅ 导入测试完成:")
    print(f"   总数: {result.total}")
    print(f"   成功: {result.success}")
    print(f"   失败: {result.failed}")
    if result.errors:
        print(f"   错误示例: {result.errors[0].message}")
    
    Path(temp_path).unlink()


async def main():
    """主测试函数"""
    print("=" * 50)
    print("🚀 股权激励监控面板 - 基础架构测试")
    print("=" * 50)
    
    try:
        await _test_database()
    except Exception as e:
        print(f"❌ 数据库测试失败: {e}")
    
    try:
        await _test_akshare()
    except Exception as e:
        print(f"❌ AKShare测试失败: {e}")
    
    try:
        _test_import()
    except Exception as e:
        print(f"❌ 导入测试失败: {e}")
    
    print("\n" + "=" * 50)
    print("✅ 基础架构测试完成")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
