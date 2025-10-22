#!/usr/bin/env python3
"""
Grid Trading Bot - 经典网格策略
支持所有交易所的通用网格交易策略
"""

import argparse
import asyncio
import logging
from pathlib import Path
import sys
import dotenv
from decimal import Decimal
from grid_trading_bot import GridTradingBot
from trading_bot import TradingConfig
from exchanges import ExchangeFactory
from grid_utils import GridStrategyValidator, GridStrategyAnalyzer, GridStrategyTester


def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='Grid Trading Bot - 经典网格策略，支持所有交易所',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
    # EdgeX 交易所 ETH 网格策略
    python grid_bot.py --exchange edgex --ticker ETH --grid-spacing 0.5 --grid-upper 15 --grid-lower 15 --per-order 50

    # Backpack 交易所 BTC 网格策略  
    python grid_bot.py --exchange backpack --ticker BTC --grid-spacing 1.0 --grid-upper 10 --grid-lower 10 --per-order 100

    # 自定义网格配置
    python grid_bot.py --exchange grvt --ticker SOL --grid-spacing 0.8 --grid-upper 20 --grid-lower 20 --per-order 30
        """
    )

    # 交易所选择
    parser.add_argument('--exchange', type=str, default='edgex',
                        choices=ExchangeFactory.get_supported_exchanges(),
                        help='交易所选择 (默认: edgex). '
                             f'支持的交易所: {", ".join(ExchangeFactory.get_supported_exchanges())}')

    # 基础交易参数
    parser.add_argument('--ticker', type=str, default='ETH',
                        help='交易对符号 (默认: ETH)')
    parser.add_argument('--env-file', type=str, default=".env",
                        help='环境变量文件路径 (默认: .env)')

    # 网格策略参数
    parser.add_argument('--grid-spacing', type=float, default=1.0,
                        help='网格间距百分比 (默认: 1.0, 表示1.0%%)')
    parser.add_argument('--grid-upper', type=int, default=10,
                        help='当前价格上方的网格数量 (默认: 10)')
    parser.add_argument('--grid-lower', type=int, default=10,
                        help='当前价格下方的网格数量 (默认: 10)')
    parser.add_argument('--per-order', type=float, default=50.0,
                        help='每个网格订单的金额 USDT (默认: 50.0)')
    parser.add_argument('--initial-balance', type=float, default=1000.0,
                        help='网格策略初始资金 USDT (默认: 1000.0)')

    # 风险控制参数
    parser.add_argument('--stop-price', type=Decimal, default=-1,
                        help='停止交易价格。到达该价格时退出程序 (默认: -1, 不设置)')
    parser.add_argument('--pause-price', type=Decimal, default=-1,
                        help='暂停交易价格。到达该价格时暂停交易 (默认: -1, 不设置)')

    # 高级参数
    parser.add_argument('--max-orders', type=int, default=100,
                        help='最大同时活跃订单数 (默认: 100)')
    
    # 动态网格参数
    parser.add_argument('--disable-dynamic', action='store_true',
                        help='禁用动态网格移动功能')
    parser.add_argument('--breakthrough-threshold', type=float, default=0.5,
                        help='价格突破阈值（网格间距的百分比，默认0.5）')
    
    # 测试和演示功能
    parser.add_argument('--test-mode', action='store_true',
                        help='运行策略模拟测试，不执行真实交易')
    parser.add_argument('--analyze-only', action='store_true',
                        help='仅分析策略参数，不执行交易')
    
    return parser.parse_args()


def setup_logging():
    """设置日志配置"""
    # 清除现有处理程序
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 设置日志级别
    root_logger.setLevel(logging.WARNING)

    # 抑制第三方库的调试日志
    logging.getLogger('websockets').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('lighter').setLevel(logging.WARNING)


def validate_grid_parameters(args):
    """验证网格策略参数"""
    validator = GridStrategyValidator()
    
    # 基础参数验证
    errors = validator.validate_basic_params(
        args.grid_spacing, args.grid_upper, args.grid_lower, 
        args.per_order, args.initial_balance
    )
    
    if errors:
        print("❌ 参数验证失败:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
    
    # 风险参数检查（警告）
    warnings = validator.validate_risk_params(
        args.grid_spacing, args.grid_upper, args.grid_lower, args.ticker
    )
    
    if warnings:
        print("⚠️  参数建议:")
        for warning in warnings:
            print(f"  - {warning}")
        print()  # 空行分隔


async def main():
    """主函数"""
    args = parse_arguments()
    
    # 设置日志
    setup_logging()
    
    # 验证参数
    validate_grid_parameters(args)
    
    # 处理测试模式
    if args.test_mode:
        print("🧪 运行网格策略模拟测试...")
        tester = GridStrategyTester()
        await tester.run_simulation_test(
            center_price=2000.0,  # 使用默认测试价格
            grid_spacing=args.grid_spacing,
            grid_upper=args.grid_upper,
            grid_lower=args.grid_lower,
            per_order=args.per_order
        )
        return
    
    # 处理仅分析模式
    if args.analyze_only:
        print("📊 分析网格策略参数...")
        analyzer = GridStrategyAnalyzer()
        analyzer.print_strategy_analysis(
            Decimal('2000.0'),  # 使用假设价格进行分析
            Decimal(str(args.grid_spacing)),
            args.grid_upper,
            args.grid_lower,
            Decimal(str(args.per_order)),
            args.ticker
        )
        return
    
    # 检查环境变量文件（实际交易模式才需要）
    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"环境变量文件未找到: {env_path.resolve()}")
        sys.exit(1)
    dotenv.load_dotenv(args.env_file)

    # 创建网格策略配置
    config = TradingConfig(
        # 基础参数
        ticker=args.ticker.upper(),
        contract_id='',  # 将在运行时设置
        tick_size=Decimal(0),  # 将在运行时设置
        quantity=Decimal(0),  # 网格策略不使用此参数
        take_profit=Decimal(0),  # 网格策略不使用此参数
        direction='buy',  # 网格策略同时买卖
        max_orders=args.max_orders,
        wait_time=0,  # 网格策略不使用此参数
        exchange=args.exchange.lower(),
        grid_step=Decimal(-100),  # 网格策略不使用此参数
        stop_price=args.stop_price,
        pause_price=args.pause_price,
        boost_mode=False,
        
        # 网格策略参数
        grid_mode=True,
        grid_spacing=Decimal(str(args.grid_spacing)),
        grid_upper_count=args.grid_upper,
        grid_lower_count=args.grid_lower,
        grid_initial_balance=Decimal(str(args.initial_balance)),
        grid_per_order_amount=Decimal(str(args.per_order)),
        # 动态网格参数
        grid_dynamic_mode=not args.disable_dynamic,
        grid_breakthrough_threshold=Decimal(str(args.breakthrough_threshold)),
    )

    # 打印策略配置摘要
    print("=" * 60)
    print("🔷 经典网格交易策略")
    print("=" * 60)
    print(f"交易所: {config.exchange.upper()}")
    print(f"交易对: {config.ticker}")
    print(f"网格间距: {config.grid_spacing}%")
    print(f"上方网格: {config.grid_upper_count} 个")
    print(f"下方网格: {config.grid_lower_count} 个")
    print(f"每单金额: {config.grid_per_order_amount} USDT")
    print(f"总网格数: {config.grid_upper_count + config.grid_lower_count} 个")
    print(f"预计占用资金: {(config.grid_upper_count + config.grid_lower_count) * config.grid_per_order_amount} USDT")
    print(f"动态移动: {'启用' if config.grid_dynamic_mode else '禁用'}")
    if config.grid_dynamic_mode:
        print(f"突破阈值: {config.grid_breakthrough_threshold}x网格间距")
    print("=" * 60)
    
    # 确认启动
    try:
        response = input("确认启动网格策略? (y/N): ").strip().lower()
        if response not in ['y', 'yes']:
            print("已取消启动")
            return
    except KeyboardInterrupt:
        print("\n已取消启动")
        return

    # 创建并运行网格交易机器人
    print("\n🚀 启动网格交易策略...")
    grid_bot = GridTradingBot(config)
    
    try:
        await grid_bot.run()
    except Exception as e:
        print(f"网格策略执行失败: {e}")
        return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n网格策略已停止")
