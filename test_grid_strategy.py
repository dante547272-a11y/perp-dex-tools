#!/usr/bin/env python3
"""
网格策略测试验证脚本
用于验证网格策略的各个组件是否正常工作
"""

import asyncio
import sys
from decimal import Decimal
from trading_bot import TradingConfig
from grid_utils import GridStrategyValidator, GridStrategyAnalyzer, GridStrategyTester


def test_config_creation():
    """测试网格配置创建"""
    print("🔧 测试配置创建...")
    
    try:
        config = TradingConfig(
            ticker='ETH',
            contract_id='ETH-PERP',
            quantity=Decimal('0.1'),
            take_profit=Decimal('0.02'),
            tick_size=Decimal('0.01'),
            direction='buy',
            max_orders=50,
            wait_time=300,
            exchange='edgex',
            grid_step=Decimal('-100'),
            stop_price=Decimal('-1'),
            pause_price=Decimal('-1'),
            boost_mode=False,
            # 网格参数
            grid_mode=True,
            grid_spacing=Decimal('1.0'),
            grid_upper_count=10,
            grid_lower_count=10,
            grid_initial_balance=Decimal('1000.0'),
            grid_per_order_amount=Decimal('50.0')
        )
        
        print(f"✅ 配置创建成功: {config.ticker} 网格策略")
        print(f"   网格间距: {config.grid_spacing}%")
        print(f"   上方网格: {config.grid_upper_count}")
        print(f"   下方网格: {config.grid_lower_count}")
        return True
        
    except Exception as e:
        print(f"❌ 配置创建失败: {e}")
        return False


def test_parameter_validation():
    """测试参数验证功能"""
    print("\n🔍 测试参数验证...")
    
    validator = GridStrategyValidator()
    
    # 测试正常参数
    errors = validator.validate_basic_params(1.0, 10, 10, 50.0, 1000.0)
    if not errors:
        print("✅ 正常参数验证通过")
    else:
        print(f"❌ 正常参数验证失败: {errors}")
        return False
    
    # 测试异常参数
    errors = validator.validate_basic_params(0, 10, 10, 50.0, 1000.0)  # 网格间距为0
    if errors:
        print("✅ 异常参数检测成功")
    else:
        print("❌ 异常参数检测失败")
        return False
    
    return True


def test_grid_calculation():
    """测试网格计算功能"""
    print("\n📊 测试网格计算...")
    
    try:
        analyzer = GridStrategyAnalyzer()
        
        center_price = Decimal('2000.0')
        grid_spacing = Decimal('1.0')
        
        buy_levels, sell_levels = analyzer.calculate_grid_levels(
            center_price, grid_spacing, 5, 5
        )
        
        if len(buy_levels) == 5 and len(sell_levels) == 5:
            print("✅ 网格计算成功")
            print(f"   买入网格范围: ${min(buy_levels):.2f} - ${max(buy_levels):.2f}")
            print(f"   卖出网格范围: ${min(sell_levels):.2f} - ${max(sell_levels):.2f}")
            return True
        else:
            print(f"❌ 网格数量错误: 买入{len(buy_levels)}, 卖出{len(sell_levels)}")
            return False
            
    except Exception as e:
        print(f"❌ 网格计算失败: {e}")
        return False


async def test_simulation():
    """测试策略模拟"""
    print("\n🧪 测试策略模拟...")
    
    try:
        tester = GridStrategyTester()
        
        # 运行快速模拟测试
        await tester._simulate_price_movements(
            Decimal('2000.0'),
            [Decimal('1980.0'), Decimal('1960.0')],  # 买入网格
            [Decimal('2020.0'), Decimal('2040.0')],  # 卖出网格
            Decimal('50.0')
        )
        
        print("✅ 策略模拟完成")
        return True
        
    except Exception as e:
        print(f"❌ 策略模拟失败: {e}")
        return False


async def main():
    """主测试函数"""
    print("=" * 60)
    print("🚀 网格策略组件测试")
    print("=" * 60)
    
    test_results = []
    
    # 运行各项测试
    test_results.append(test_config_creation())
    test_results.append(test_parameter_validation())
    test_results.append(test_grid_calculation())
    test_results.append(await test_simulation())
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("📋 测试结果汇总")
    print("=" * 60)
    
    passed_tests = sum(test_results)
    total_tests = len(test_results)
    
    print(f"总测试数: {total_tests}")
    print(f"通过测试: {passed_tests}")
    print(f"失败测试: {total_tests - passed_tests}")
    
    if passed_tests == total_tests:
        print("\n🎉 所有测试通过！网格策略组件工作正常")
        print("\n📖 使用指南:")
        print("1. 分析策略参数: python grid_bot.py --analyze-only --ticker ETH")
        print("2. 运行模拟测试: python grid_bot.py --test-mode --ticker ETH")
        print("3. 开始实际交易: python grid_bot.py --exchange edgex --ticker ETH")
        return 0
    else:
        print(f"\n❌ 有 {total_tests - passed_tests} 项测试失败，请检查代码")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
