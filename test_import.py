#!/usr/bin/env python3
"""
股权激励监控面板 - 导入测试脚本
测试CSV/Excel导入功能
"""
import requests
import pandas as pd
import tempfile
from pathlib import Path
import json

BASE_URL = "http://localhost:8001"

def test_import_csv():
    """测试CSV导入"""
    print("=" * 60)
    print("🧪 测试CSV导入功能")
    print("=" * 60)
    
    # 创建测试数据
    test_data = """symbol,name,strike_price,quantity,custom_threshold
000001,平安银行,10.50,1000,0.15
600519,贵州茅台,1500.00,500,0.10
300750,宁德时代,200.00,2000,
688981,中芯国际,50.00,3000,0.20
000858,五粮液,120.00,800,0.12
002415,海康威视,25.00,5000,
601318,中国平安,45.00,2000,0.08
000333,美的集团,55.00,1500,0.10
600036,招商银行,35.00,2500,
300059,东方财富,15.00,6000,0.15
invalid_code,无效股票,100,,"""
    
    # 保存临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(test_data)
        temp_path = f.name
    
    print(f"\n📄 测试数据文件: {temp_path}")
    print("\n测试数据预览:")
    df = pd.read_csv(temp_path)
    print(df.to_string())
    
    # 发送导入请求
    print(f"\n📤 发送导入请求到 {BASE_URL}/api/import/csv")
    
    try:
        with open(temp_path, 'rb') as f:
            files = {'file': ('test_stocks.csv', f, 'text/csv')}
            response = requests.post(f"{BASE_URL}/api/import/csv", files=files, timeout=30)
        
        print(f"\n📥 响应状态: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print("\n✅ 导入成功!")
            print(f"   总数: {result['total']}")
            print(f"   成功导入: {result['imported']}")
            print(f"   更新: {result['updated']}")
            print(f"   失败: {result['failed']}")
            
            if result['errors']:
                print("\n⚠️ 错误详情:")
                for error in result['errors']:
                    print(f"   行{error['row']}: {error['message']}")
        else:
            print(f"\n❌ 导入失败: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print(f"\n❌ 连接失败: 请确保服务已启动")
        print(f"   运行: python src/main.py")
        return False
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        return False
    finally:
        # 清理临时文件
        Path(temp_path).unlink(missing_ok=True)
    
    return True


def test_import_excel():
    """测试Excel导入"""
    print("\n" + "=" * 60)
    print("🧪 测试Excel导入功能")
    print("=" * 60)
    
    # 创建测试数据
    data = {
        'symbol': ['000001', '600519', '300750'],
        'name': ['平安银行', '贵州茅台', '宁德时代'],
        'strike_price': [10.50, 1500.00, 200.00],
        'quantity': [1000, 500, 2000],
        'custom_threshold': [0.15, 0.10, None]
    }
    df = pd.DataFrame(data)
    
    # 保存临时Excel文件
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as f:
        temp_path = f.name
    
    df.to_excel(temp_path, index=False)
    
    print(f"\n📄 测试数据文件: {temp_path}")
    print("\n测试数据预览:")
    print(df.to_string())
    
    # 发送导入请求
    print(f"\n📤 发送导入请求到 {BASE_URL}/api/import/excel")
    
    try:
        with open(temp_path, 'rb') as f:
            files = {'file': ('test_stocks.xlsx', f, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
            response = requests.post(f"{BASE_URL}/api/import/excel", files=files, timeout=30)
        
        print(f"\n📥 响应状态: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print("\n✅ 导入成功!")
            print(f"   总数: {result['total']}")
            print(f"   成功导入: {result['imported']}")
            print(f"   更新: {result['updated']}")
            print(f"   失败: {result['failed']}")
        else:
            print(f"\n❌ 导入失败: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print(f"\n❌ 连接失败: 请确保服务已启动")
        return False
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        return False
    finally:
        # 清理临时文件
        Path(temp_path).unlink(missing_ok=True)
    
    return True


def test_get_prices():
    """测试获取价格数据"""
    print("\n" + "=" * 60)
    print("🧪 测试获取价格数据")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/api/monitor/prices", timeout=30)
        
        print(f"\n📥 响应状态: {response.status_code}")
        
        if response.status_code == 200:
            prices = response.json()
            print(f"\n✅ 获取成功! 共 {len(prices)} 只股票")
            
            if prices:
                print("\n价格数据预览:")
                df = pd.DataFrame(prices)
                print(df[['full_code', 'name', 'current_price', 'strike_price', 'diff_percent', 'alert_level']].to_string())
            else:
                print("\n⚠️ 暂无价格数据，请先启动监控或手动刷新")
        else:
            print(f"\n❌ 获取失败: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print(f"\n❌ 连接失败: 请确保服务已启动")
        return False
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        return False
    
    return True


def test_monitor_status():
    """测试监控状态"""
    print("\n" + "=" * 60)
    print("🧪 测试监控状态")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/api/monitor/status", timeout=10)
        
        print(f"\n📥 响应状态: {response.status_code}")
        
        if response.status_code == 200:
            status = response.json()
            print("\n✅ 获取成功!")
            print(f"   监控运行中: {status['is_running']}")
            print(f"   交易时间: {status['is_trading_time']}")
            print(f"   轮询间隔: {status['interval_seconds']}秒")
            print(f"   仅交易时间监控: {status['trading_hours_only']}")
        else:
            print(f"\n❌ 获取失败: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print(f"\n❌ 连接失败: 请确保服务已启动")
        return False
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        return False
    
    return True


def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("🚀 股权激励监控面板 - 功能测试")
    print(f"   服务地址: {BASE_URL}")
    print("=" * 60)
    
    # 检查服务是否启动
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        print(f"\n✅ 服务已启动 (状态: {response.status_code})")
    except requests.exceptions.ConnectionError:
        print("\n❌ 服务未启动!")
        print("   请先运行: python src/main.py")
        return
    
    # 运行测试
    results = []
    
    results.append(("CSV导入", test_import_csv()))
    results.append(("Excel导入", test_import_excel()))
    results.append(("获取价格", test_get_prices()))
    results.append(("监控状态", test_monitor_status()))
    
    # 总结
    print("\n" + "=" * 60)
    print("📊 测试结果汇总")
    print("=" * 60)
    
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"   {name}: {status}")
    
    all_passed = all(r[1] for r in results)
    
    if all_passed:
        print("\n🎉 所有测试通过!")
    else:
        print("\n⚠️ 部分测试失败，请检查日志")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
