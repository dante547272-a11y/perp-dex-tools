"""
Grid Trading Utilities
网格交易策略辅助工具
"""

from decimal import Decimal
from typing import List, Tuple, Dict
import asyncio


class GridStrategyValidator:
    """网格策略参数验证器"""
    
    @staticmethod
    def validate_basic_params(
        grid_spacing: float,
        grid_upper: int, 
        grid_lower: int,
        per_order: float,
        initial_balance: float
    ) -> List[str]:
        """验证基础网格参数"""
        errors = []
        
        # 网格间距验证
        if grid_spacing <= 0:
            errors.append("网格间距必须大于0")
        elif grid_spacing < 0.1:
            errors.append("网格间距建议不小于0.1%，过小的间距可能导致频繁交易")
        elif grid_spacing > 10:
            errors.append("网格间距建议不大于10%，过大的间距可能错过交易机会")
        
        # 网格数量验证
        if grid_upper <= 0 or grid_lower <= 0:
            errors.append("网格数量必须大于0")
        elif grid_upper > 50 or grid_lower > 50:
            errors.append("单侧网格数量建议不超过50个，过多可能导致资金利用率低")
        
        # 订单金额验证
        if per_order <= 0:
            errors.append("每单金额必须大于0")
        elif per_order < 10:
            errors.append("每单金额建议不少于10 USDT，过小可能不值得交易费用")
        
        # 初始资金验证
        if initial_balance <= 0:
            errors.append("初始资金必须大于0")
        
        # 资金充足性验证
        total_grids = grid_upper + grid_lower
        total_required = total_grids * per_order
        
        if total_required > initial_balance:
            errors.append(
                f"所需资金不足: 需要 {total_required:.2f} USDT，"
                f"但初始资金只有 {initial_balance:.2f} USDT"
            )
        elif total_required < initial_balance * 0.1:
            errors.append(
                f"资金利用率较低: 仅使用 {total_required/initial_balance*100:.1f}% 的资金，"
                "建议增加网格数量或每单金额"
            )
        
        return errors
    
    @staticmethod 
    def validate_risk_params(
        grid_spacing: float,
        grid_upper: int,
        grid_lower: int,
        ticker: str
    ) -> List[str]:
        """验证风险参数"""
        warnings = []
        
        # 计算价格覆盖范围
        upper_range = grid_spacing * grid_upper
        lower_range = grid_spacing * grid_lower
        total_range = upper_range + lower_range
        
        # 价格覆盖范围建议
        if total_range < 10:
            warnings.append(
                f"价格覆盖范围较小 ({total_range:.1f}%)，"
                "在高波动市场中可能快速突破网格范围"
            )
        elif total_range > 50:
            warnings.append(
                f"价格覆盖范围较大 ({total_range:.1f}%)，"
                "资金可能长期被占用"
            )
        
        # 不同币种的特定建议
        if ticker.upper() in ['BTC', 'ETH']:
            if grid_spacing > 2.0:
                warnings.append(f"{ticker} 建议使用更小的网格间距 (<2.0%)，以适应其相对稳定的波动")
        elif ticker.upper() in ['SOL', 'AVAX', 'NEAR']:
            if grid_spacing < 1.0:
                warnings.append(f"{ticker} 建议使用更大的网格间距 (>1.0%)，以适应其较高的波动性")
        
        return warnings


class GridStrategyAnalyzer:
    """网格策略分析器"""
    
    @staticmethod
    def calculate_grid_levels(
        center_price: Decimal,
        grid_spacing: Decimal,
        grid_upper: int,
        grid_lower: int
    ) -> Tuple[List[Decimal], List[Decimal]]:
        """计算网格价格水平"""
        buy_levels = []  # 买入网格（低于中心价格）
        sell_levels = []  # 卖出网格（高于中心价格）
        
        spacing_decimal = grid_spacing / 100
        
        # 计算买入网格
        for i in range(1, grid_lower + 1):
            price = center_price * (1 - spacing_decimal * i)
            buy_levels.append(price)
        
        # 计算卖出网格  
        for i in range(1, grid_upper + 1):
            price = center_price * (1 + spacing_decimal * i)
            sell_levels.append(price)
        
        return buy_levels, sell_levels
    
    @staticmethod
    def estimate_profit_potential(
        center_price: Decimal,
        grid_spacing: Decimal,
        per_order: Decimal,
        grid_count: int,
        expected_fill_rate: float = 0.5
    ) -> Dict[str, Decimal]:
        """估算利润潜力"""
        
        # 每个网格的利润 = 网格间距 * 订单金额
        profit_per_grid = (grid_spacing / 100) * per_order
        
        # 预期每日成交网格数量
        expected_daily_fills = Decimal(str(grid_count * expected_fill_rate))
        
        # 预期每日利润
        daily_profit = profit_per_grid * expected_daily_fills
        
        # 年化收益率估算（基于使用的资金）
        used_capital = per_order * grid_count
        annual_return = (daily_profit * 365) / used_capital * 100
        
        return {
            'profit_per_grid': profit_per_grid,
            'expected_daily_profit': daily_profit,
            'annual_return_rate': annual_return,
            'used_capital': used_capital
        }
    
    @staticmethod
    def print_strategy_analysis(
        center_price: Decimal,
        grid_spacing: Decimal,
        grid_upper: int,
        grid_lower: int,
        per_order: Decimal,
        ticker: str
    ):
        """打印策略分析报告"""
        print("\n" + "="*60)
        print("📊 网格策略分析报告")
        print("="*60)
        
        # 计算网格水平
        buy_levels, sell_levels = GridStrategyAnalyzer.calculate_grid_levels(
            center_price, grid_spacing, grid_upper, grid_lower
        )
        
        # 价格范围
        min_price = min(buy_levels) if buy_levels else center_price
        max_price = max(sell_levels) if sell_levels else center_price
        price_range = ((max_price - min_price) / center_price) * 100
        
        print(f"中心价格: ${center_price:.4f}")
        print(f"价格范围: ${min_price:.4f} - ${max_price:.4f}")
        print(f"覆盖范围: ±{price_range/2:.1f}% ({price_range:.1f}% 总计)")
        
        # 利润分析
        profit_analysis = GridStrategyAnalyzer.estimate_profit_potential(
            center_price, grid_spacing, per_order, grid_upper + grid_lower
        )
        
        print(f"\n💰 盈利分析:")
        print(f"每网格利润: ${profit_analysis['profit_per_grid']:.4f}")
        print(f"预期日收益: ${profit_analysis['expected_daily_profit']:.4f}")
        print(f"预估年化收益率: {profit_analysis['annual_return_rate']:.2f}%")
        print(f"资金使用量: ${profit_analysis['used_capital']:.2f}")
        
        # 风险提示
        print(f"\n⚠️  风险提示:")
        if price_range > 30:
            print("- 价格覆盖范围较大，资金可能长期被占用")
        if price_range < 10:
            print("- 价格覆盖范围较小，需要关注突破风险")
        
        print("- 网格策略适合震荡市场，趋势市场可能产生浮亏")
        print("- 收益估算基于历史数据，实际收益可能有差异")
        print("="*60)


class GridStrategyTester:
    """网格策略模拟测试器"""
    
    @staticmethod
    async def run_simulation_test(
        center_price: float = 2000.0,
        grid_spacing: float = 1.0,
        grid_upper: int = 10,
        grid_lower: int = 10,
        per_order: float = 50.0
    ):
        """运行网格策略模拟测试"""
        print("\n" + "="*60)
        print("🧪 网格策略模拟测试")
        print("="*60)
        
        center_price_decimal = Decimal(str(center_price))
        grid_spacing_decimal = Decimal(str(grid_spacing))
        per_order_decimal = Decimal(str(per_order))
        
        # 打印测试参数
        print(f"测试参数:")
        print(f"  中心价格: ${center_price}")
        print(f"  网格间距: {grid_spacing}%")
        print(f"  上方网格: {grid_upper} 个")
        print(f"  下方网格: {grid_lower} 个")
        print(f"  每单金额: ${per_order}")
        
        # 计算网格水平
        buy_levels, sell_levels = GridStrategyAnalyzer.calculate_grid_levels(
            center_price_decimal, grid_spacing_decimal, grid_upper, grid_lower
        )
        
        print(f"\n📋 网格布局:")
        print(f"买入网格 ({len(buy_levels)} 个):")
        for i, price in enumerate(reversed(buy_levels[-5:]), 1):  # 显示最接近中心价格的5个
            print(f"  买入 #{i}: ${price:.4f}")
        if len(buy_levels) > 5:
            print(f"  ... 还有 {len(buy_levels)-5} 个买入网格")
        
        print(f"卖出网格 ({len(sell_levels)} 个):")
        for i, price in enumerate(sell_levels[:5], 1):  # 显示最接近中心价格的5个
            print(f"  卖出 #{i}: ${price:.4f}")
        if len(sell_levels) > 5:
            print(f"  ... 还有 {len(sell_levels)-5} 个卖出网格")
        
        # 模拟价格波动和交易
        print(f"\n⚡ 模拟价格波动:")
        await GridStrategyTester._simulate_price_movements(
            center_price_decimal, buy_levels, sell_levels, per_order_decimal
        )
        
        print("="*60)
    
    @staticmethod
    async def _simulate_price_movements(
        center_price: Decimal,
        buy_levels: List[Decimal],
        sell_levels: List[Decimal], 
        per_order: Decimal
    ):
        """模拟价格波动和网格交易"""
        import random
        
        current_price = center_price
        total_profit = Decimal('0')
        trades_count = 0
        
        # 模拟10次价格波动
        for i in range(10):
            # 随机价格变动 (-3% 到 +3%)
            price_change = Decimal(str(random.uniform(-3, 3)))
            current_price = current_price * (1 + price_change / 100)
            
            print(f"  波动 #{i+1}: ${current_price:.4f} ({price_change:+.1f}%)")
            
            # 检查是否触发买入网格
            for buy_price in buy_levels:
                if current_price <= buy_price and random.random() < 0.3:  # 30%概率成交
                    # 模拟买入后立即在更高价格卖出
                    sell_price = buy_price * (1 + Decimal('0.01'))  # 1%利润
                    profit = (sell_price - buy_price) * per_order / buy_price
                    total_profit += profit
                    trades_count += 1
                    print(f"    ✅ 网格交易: 买入@${buy_price:.4f} -> 卖出@${sell_price:.4f}, 利润: ${profit:.4f}")
                    break
            
            # 检查是否触发卖出网格
            for sell_price in sell_levels:
                if current_price >= sell_price and random.random() < 0.3:  # 30%概率成交
                    # 模拟卖出后在更低价格买入
                    buy_price = sell_price * (1 - Decimal('0.01'))  # 1%利润
                    profit = (sell_price - buy_price) * per_order / buy_price
                    total_profit += profit
                    trades_count += 1
                    print(f"    ✅ 网格交易: 卖出@${sell_price:.4f} -> 买入@${buy_price:.4f}, 利润: ${profit:.4f}")
                    break
            
            await asyncio.sleep(0.5)  # 模拟时间间隔
        
        print(f"\n📈 模拟结果:")
        print(f"  总交易次数: {trades_count}")
        print(f"  总模拟利润: ${total_profit:.4f}")
        print(f"  平均每笔利润: ${total_profit/max(trades_count, 1):.4f}")


# 使用示例
if __name__ == "__main__":
    async def test_grid_utils():
        """测试网格工具功能"""
        # 参数验证测试
        validator = GridStrategyValidator()
        errors = validator.validate_basic_params(1.0, 10, 10, 50.0, 1000.0)
        if errors:
            print("参数验证错误:")
            for error in errors:
                print(f"  - {error}")
        else:
            print("✅ 参数验证通过")
        
        # 策略分析测试
        analyzer = GridStrategyAnalyzer()
        analyzer.print_strategy_analysis(
            Decimal('2000'), Decimal('1.0'), 10, 10, Decimal('50'), 'ETH'
        )
        
        # 模拟测试
        tester = GridStrategyTester()
        await tester.run_simulation_test(2000.0, 1.0, 10, 10, 50.0)
    
    asyncio.run(test_grid_utils())
