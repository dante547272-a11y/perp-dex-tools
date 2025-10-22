"""
Grid Trading Bot - Classic Grid Strategy Implementation
支持所有交易所的通用网格策略
"""

import asyncio
import time
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import traceback

from trading_bot import TradingBot, TradingConfig
from exchanges.base import OrderInfo


@dataclass
class GridLevel:
    """单个网格级别的数据结构"""
    price: Decimal
    side: str  # 'buy' or 'sell'
    quantity: Decimal
    order_id: Optional[str] = None
    is_filled: bool = False
    level_index: int = 0  # 网格级别索引，0为中心价格
    

class GridTradingBot(TradingBot):
    """经典网格交易策略机器人"""
    
    def __init__(self, config: TradingConfig):
        super().__init__(config)
        
        # 网格状态管理
        self.center_price: Optional[Decimal] = None
        self.grid_levels: List[GridLevel] = []
        self.active_grid_orders: Dict[str, GridLevel] = {}  # order_id -> GridLevel
        self.total_profit: Decimal = Decimal('0')
        self.grid_trades_count: int = 0
        
        # 动态移动网格相关
        self.grid_moves_count: int = 0  # 网格移动次数
        self.last_price_check: Optional[Decimal] = None
        self.price_breakthrough_threshold: Decimal = config.grid_breakthrough_threshold  # 突破阈值
        
        # 验证网格配置
        self._validate_grid_config()
        
    def _validate_grid_config(self):
        """验证网格策略配置参数"""
        if not self.config.grid_mode:
            raise ValueError("Grid mode must be enabled for GridTradingBot")
            
        if self.config.grid_spacing <= 0:
            raise ValueError("Grid spacing must be positive")
            
        if self.config.grid_upper_count <= 0 or self.config.grid_lower_count <= 0:
            raise ValueError("Grid counts must be positive")
            
        if self.config.grid_per_order_amount <= 0:
            raise ValueError("Grid per order amount must be positive")
            
        self.logger.log("Grid configuration validated successfully", "INFO")
    
    def _round_quantity(self, quantity: Decimal) -> Decimal:
        """将数量舍入到合适的精度"""
        # 根据不同交易所和代币使用不同的数量精度
        if self.config.exchange.lower() == 'grvt':
            # GRVT根据代币类型使用不同精度
            if self.config.ticker.upper() in ['HYPE', 'DOGE', 'ADA', 'XRP']:
                # 低价代币使用整数精度，最小1.0
                rounded = quantity.quantize(Decimal('1'), rounding=ROUND_DOWN)
                return max(rounded, Decimal('1'))
            elif self.config.ticker.upper() in ['SOL', 'AVAX', 'NEAR']:
                # 中价代币使用1位小数，最小0.1
                rounded = quantity.quantize(Decimal('0.1'), rounding=ROUND_DOWN)
                return max(rounded, Decimal('0.1'))
            else:
                # 高价代币（BTC, ETH等）使用2位小数，最小0.01
                rounded = quantity.quantize(Decimal('0.01'), rounding=ROUND_DOWN)
                return max(rounded, Decimal('0.01'))
        elif self.config.exchange.lower() in ['edgex', 'backpack']:
            # EdgeX和Backpack使用4位小数
            return quantity.quantize(Decimal('0.0001'), rounding=ROUND_DOWN)
        else:
            # 其他交易所默认使用4位小数
            return quantity.quantize(Decimal('0.0001'), rounding=ROUND_DOWN)
    
    def _calculate_grid_levels(self) -> List[GridLevel]:
        """计算所有网格级别"""
        if not self.center_price:
            raise ValueError("Center price not set")
        
        grid_levels = []
        spacing_decimal = self.config.grid_spacing / 100  # 转换为小数
        
        # 创建买入网格（价格低于中心价格）
        for i in range(1, self.config.grid_lower_count + 1):
            price = self.center_price * (1 - spacing_decimal * i)
            price = self.exchange_client.round_to_tick(price)
            
            # 计算代币数量：USDT金额 / 价格
            token_quantity = self.config.grid_per_order_amount / price
            token_quantity = self._round_quantity(token_quantity)
            
            grid_level = GridLevel(
                price=price,
                side='buy',
                quantity=token_quantity,
                level_index=-i  # 负数表示低于中心价格
            )
            grid_levels.append(grid_level)
        
        # 创建卖出网格（价格高于中心价格）
        for i in range(1, self.config.grid_upper_count + 1):
            price = self.center_price * (1 + spacing_decimal * i)
            price = self.exchange_client.round_to_tick(price)
            
            # 计算代币数量：USDT金额 / 价格
            token_quantity = self.config.grid_per_order_amount / price
            token_quantity = self._round_quantity(token_quantity)
            
            grid_level = GridLevel(
                price=price,
                side='sell',
                quantity=token_quantity,
                level_index=i  # 正数表示高于中心价格
            )
            grid_levels.append(grid_level)
        
        return grid_levels
    
    async def _initialize_center_price(self):
        """初始化中心价格"""
        try:
            best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(self.config.contract_id)
            if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
                raise ValueError("Invalid bid/ask data")
            
            # 使用中间价作为中心价格
            self.center_price = (best_bid + best_ask) / 2
            self.center_price = self.exchange_client.round_to_tick(self.center_price)
            
            self.logger.log(f"Center price initialized: {self.center_price}", "INFO")
            
        except Exception as e:
            self.logger.log(f"Error initializing center price: {e}", "ERROR")
            raise
    
    async def _place_grid_order(self, grid_level: GridLevel) -> bool:
        """下单到指定网格级别"""
        try:
            if grid_level.side == 'buy':
                order_result = await self.exchange_client.place_close_order(
                    self.config.contract_id,
                    grid_level.quantity,
                    grid_level.price,
                    'buy'
                )
            else:  # sell
                order_result = await self.exchange_client.place_close_order(
                    self.config.contract_id,
                    grid_level.quantity,
                    grid_level.price,
                    'sell'
                )
            
            if order_result.success:
                grid_level.order_id = order_result.order_id
                self.active_grid_orders[order_result.order_id] = grid_level
                
                self.logger.log(
                    f"Grid order placed: {grid_level.side.upper()} {grid_level.quantity} @ {grid_level.price} "
                    f"[Level {grid_level.level_index}] [Order ID: {order_result.order_id}]",
                    "INFO"
                )
                return True
            else:
                self.logger.log(
                    f"Failed to place grid order: {order_result.error_message}",
                    "ERROR"
                )
                return False
                
        except Exception as e:
            self.logger.log(f"Error placing grid order: {e}", "ERROR")
            return False
    
    async def _initialize_all_grids(self):
        """初始化所有网格订单"""
        self.logger.log("Initializing grid levels...", "INFO")
        
        # 计算所有网格级别
        self.grid_levels = self._calculate_grid_levels()
        
        self.logger.log(
            f"Calculated {len(self.grid_levels)} grid levels "
            f"(Buy: {self.config.grid_lower_count}, Sell: {self.config.grid_upper_count})",
            "INFO"
        )
        
        # 并发下单到所有网格级别
        success_count = 0
        for grid_level in self.grid_levels:
            success = await self._place_grid_order(grid_level)
            if success:
                success_count += 1
            
            # 小延迟避免订单频率限制
            await asyncio.sleep(0.1)
        
        # 计算并记录网格范围信息
        if self.grid_levels:
            buy_levels = [level for level in self.grid_levels if level.side == 'buy']
            sell_levels = [level for level in self.grid_levels if level.side == 'sell']
            
            min_price = min([level.price for level in buy_levels]) if buy_levels else self.center_price
            max_price = max([level.price for level in sell_levels]) if sell_levels else self.center_price
            
            price_range_percent = ((max_price - min_price) / self.center_price) * 100
            
            self.logger.log(
                f"Grid Range - Center: {self.center_price:.4f}, "
                f"Range: {min_price:.4f} - {max_price:.4f} ({price_range_percent:.1f}%)",
                "INFO"
            )
        
        self.logger.log(
            f"Grid initialization completed: {success_count}/{len(self.grid_levels)} orders placed successfully",
            "INFO"
        )
        
        if success_count == 0:
            raise Exception("Failed to place any grid orders")
    
    async def _handle_grid_order_fill(self, filled_order_id: str, filled_price: Decimal, filled_size: Decimal):
        """处理网格订单成交"""
        if filled_order_id not in self.active_grid_orders:
            return
        
        filled_grid = self.active_grid_orders[filled_order_id]
        filled_grid.is_filled = True
        
        self.logger.log(
            f"Grid order filled: {filled_grid.side.upper()} {filled_size} @ {filled_price} "
            f"[Level {filled_grid.level_index}]",
            "INFO"
        )
        
        # 计算利润（每个网格成交都有利润）
        # 网格策略的利润来自于价差 = 网格间距 * 订单金额
        if filled_grid.side == 'sell':
            # 卖出订单成交，利润 = 网格间距 * USDT金额
            profit = (self.config.grid_spacing / 100) * self.config.grid_per_order_amount
        else:
            # 买入订单成交，也有利润（为下次卖出做准备）
            profit = (self.config.grid_spacing / 100) * self.config.grid_per_order_amount
        
        self.total_profit += profit
        self.grid_trades_count += 1
        
        self.logger.log(
            f"Grid trade profit: {profit:.4f} USDT (Total: {self.total_profit:.4f} USDT, "
            f"Trades: {self.grid_trades_count})",
            "INFO"
        )
        
        # 移除已成交的订单
        del self.active_grid_orders[filled_order_id]
        
        # 在相同级别重新下单
        await self._refill_grid_level(filled_grid)
    
    async def _refill_grid_level(self, filled_grid: GridLevel):
        """在成交的网格级别重新下单"""
        try:
            # 重新计算代币数量（因为价格可能已经变化）
            token_quantity = self.config.grid_per_order_amount / filled_grid.price
            token_quantity = self._round_quantity(token_quantity)
            
            # 创建新的网格级别（相同价格和方向）
            new_grid = GridLevel(
                price=filled_grid.price,
                side=filled_grid.side,
                quantity=token_quantity,
                level_index=filled_grid.level_index
            )
            
            # 下单
            success = await self._place_grid_order(new_grid)
            
            if success:
                self.logger.log(
                    f"Grid level refilled: {new_grid.side.upper()} @ {new_grid.price} "
                    f"[Level {new_grid.level_index}]",
                    "INFO"
                )
            else:
                self.logger.log(
                    f"Failed to refill grid level {new_grid.level_index}",
                    "WARNING"
                )
                
        except Exception as e:
            self.logger.log(f"Error refilling grid level: {e}", "ERROR")
    
    async def _check_price_breakthrough(self) -> bool:
        """检测价格是否突破网格边界"""
        try:
            # 如果未启用动态模式，直接返回
            if not self.config.grid_dynamic_mode:
                return False
                
            if not self.center_price or not self.grid_levels:
                return False
            
            # 获取当前价格
            best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(self.config.contract_id)
            if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
                return False
            
            current_price = (best_bid + best_ask) / 2
            self.last_price_check = current_price
            
            # 计算网格边界
            spacing_decimal = self.config.grid_spacing / 100
            upper_boundary = self.center_price * (1 + spacing_decimal * self.config.grid_upper_count)
            lower_boundary = self.center_price * (1 - spacing_decimal * self.config.grid_lower_count)
            
            # 计算突破阈值（网格间距的一半）
            breakthrough_threshold = self.center_price * (spacing_decimal * self.price_breakthrough_threshold)
            
            # 检测上边界突破
            if current_price > upper_boundary + breakthrough_threshold:
                self.logger.log(
                    f"Price breakthrough detected: UP - Current: {current_price:.4f}, "
                    f"Upper boundary: {upper_boundary:.4f}",
                    "INFO"
                )
                await self._move_grid_up(current_price)
                return True
            
            # 检测下边界突破
            if current_price < lower_boundary - breakthrough_threshold:
                self.logger.log(
                    f"Price breakthrough detected: DOWN - Current: {current_price:.4f}, "
                    f"Lower boundary: {lower_boundary:.4f}",
                    "INFO"
                )
                await self._move_grid_down(current_price)
                return True
            
            return False
            
        except Exception as e:
            self.logger.log(f"Error checking price breakthrough: {e}", "ERROR")
            return False
    
    async def _move_grid_up(self, current_price: Decimal):
        """向上移动网格"""
        try:
            self.logger.log("Moving grid UP...", "INFO")
            
            # 计算新的中心价格（向上移动一个网格间距）
            spacing_decimal = self.config.grid_spacing / 100
            new_center_price = self.center_price * (1 + spacing_decimal)
            
            await self._execute_grid_move(new_center_price, "UP")
            
        except Exception as e:
            self.logger.log(f"Error moving grid up: {e}", "ERROR")
    
    async def _move_grid_down(self, current_price: Decimal):
        """向下移动网格"""
        try:
            self.logger.log("Moving grid DOWN...", "INFO")
            
            # 计算新的中心价格（向下移动一个网格间距）
            spacing_decimal = self.config.grid_spacing / 100
            new_center_price = self.center_price * (1 - spacing_decimal)
            
            await self._execute_grid_move(new_center_price, "DOWN")
            
        except Exception as e:
            self.logger.log(f"Error moving grid down: {e}", "ERROR")
    
    async def _execute_grid_move(self, new_center_price: Decimal, direction: str):
        """执行网格移动"""
        try:
            old_center_price = self.center_price
            
            # 1. 取消所有现有网格订单
            self.logger.log("Cancelling all existing grid orders...", "INFO")
            cancelled_count = await self._cancel_all_grid_orders()
            
            # 2. 更新中心价格
            self.center_price = self.exchange_client.round_to_tick(new_center_price)
            
            # 3. 重新计算网格级别
            self.grid_levels = self._calculate_grid_levels()
            
            # 4. 重新下单到所有网格级别
            self.logger.log("Placing orders to new grid levels...", "INFO")
            success_count = 0
            for grid_level in self.grid_levels:
                success = await self._place_grid_order(grid_level)
                if success:
                    success_count += 1
                await asyncio.sleep(0.1)  # 避免订单频率限制
            
            # 5. 记录移动结果
            self.grid_moves_count += 1
            
            self.logger.log(
                f"Grid moved {direction}: {old_center_price:.4f} -> {self.center_price:.4f} "
                f"| Cancelled: {cancelled_count} | Placed: {success_count}/{len(self.grid_levels)} "
                f"| Total moves: {self.grid_moves_count}",
                "INFO"
            )
            
        except Exception as e:
            self.logger.log(f"Error executing grid move: {e}", "ERROR")
    
    async def _cancel_all_grid_orders(self) -> int:
        """取消所有网格订单"""
        cancelled_count = 0
        
        try:
            for order_id in list(self.active_grid_orders.keys()):
                try:
                    cancel_result = await self.exchange_client.cancel_order(order_id)
                    if cancel_result.success:
                        cancelled_count += 1
                    
                    # 从活跃订单中移除
                    if order_id in self.active_grid_orders:
                        del self.active_grid_orders[order_id]
                    
                    await asyncio.sleep(0.05)  # 避免频率限制
                    
                except Exception as e:
                    self.logger.log(f"Error cancelling order {order_id}: {e}", "WARNING")
            
            return cancelled_count
            
        except Exception as e:
            self.logger.log(f"Error cancelling grid orders: {e}", "ERROR")
            return cancelled_count
    
    def _setup_grid_websocket_handlers(self):
        """设置网格策略的WebSocket处理程序"""
        def grid_order_update_handler(message):
            """处理网格订单更新"""
            try:
                # 检查是否为我们的合约
                if message.get('contract_id') != self.config.contract_id:
                    return
                
                order_id = message.get('order_id')
                status = message.get('status')
                filled_size = Decimal(message.get('filled_size', 0))
                price = Decimal(message.get('price', 0))
                
                # 处理订单成交
                if status == 'FILLED' and order_id in self.active_grid_orders:
                    # 使用异步任务处理成交
                    if self.loop:
                        asyncio.create_task(
                            self._handle_grid_order_fill(order_id, price, filled_size)
                        )
                
                # 记录订单状态变化
                if order_id in self.active_grid_orders:
                    grid_level = self.active_grid_orders[order_id]
                    self.logger.log(
                        f"Grid order update: [{order_id}] {status} "
                        f"{grid_level.side.upper()} @ {price} [Level {grid_level.level_index}]",
                        "INFO"
                    )
                
            except Exception as e:
                self.logger.log(f"Error handling grid order update: {e}", "ERROR")
                self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
        
        # 设置订单更新处理程序
        self.exchange_client.setup_order_update_handler(grid_order_update_handler)
    
    async def _grid_status_monitor(self):
        """网格状态监控"""
        try:
            active_orders = await self.exchange_client.get_active_orders(self.config.contract_id)
            
            # 统计网格订单状态
            buy_orders = sum(1 for order in active_orders if order.side == 'buy')
            sell_orders = sum(1 for order in active_orders if order.side == 'sell')
            
            # 显示当前价格
            current_price_info = ""
            if self.last_price_check:
                current_price_info = f"Current Price: {self.last_price_check:.4f} | "
            
            self.logger.log(
                f"Grid Status - {current_price_info}Active Orders: {len(active_orders)} "
                f"(Buy: {buy_orders}, Sell: {sell_orders}) | "
                f"Total Profit: {self.total_profit:.4f} USDT | "
                f"Grid Trades: {self.grid_trades_count} | "
                f"Grid Moves: {self.grid_moves_count}",
                "INFO"
            )
            
            # 检查是否有网格订单丢失，需要重新下单
            expected_orders = len(self.grid_levels)
            if len(active_orders) < expected_orders * 0.8:  # 如果活跃订单少于期望的80%
                self.logger.log(
                    f"WARNING: Only {len(active_orders)}/{expected_orders} grid orders active. "
                    "Consider rebalancing.",
                    "WARNING"
                )
            
        except Exception as e:
            self.logger.log(f"Error in grid status monitor: {e}", "ERROR")
    
    async def run(self):
        """运行网格交易策略"""
        try:
            # 临时设置一个有效的quantity值来通过交易所验证
            # 网格策略实际不使用这个值
            original_quantity = self.config.quantity
            
            # 根据不同代币设置合适的临时数量
            if self.config.ticker.upper() in ['HYPE', 'DOGE', 'ADA', 'XRP']:
                # 低价代币通常需要更大的最小数量
                self.config.quantity = Decimal('1.0')
            elif self.config.ticker.upper() in ['SOL', 'AVAX', 'NEAR']:
                # 中价代币
                self.config.quantity = Decimal('0.1')
            else:
                # 高价代币（BTC, ETH等）
                self.config.quantity = Decimal('0.01')
            
            # 获取合约信息
            self.config.contract_id, self.config.tick_size = await self.exchange_client.get_contract_attributes()
            
            # 恢复原始值
            self.config.quantity = original_quantity
            
            # 记录网格配置
            self.logger.log("=== Grid Trading Configuration ===", "INFO")
            self.logger.log(f"Ticker: {self.config.ticker}", "INFO")
            self.logger.log(f"Exchange: {self.config.exchange}", "INFO")
            self.logger.log(f"Grid Spacing: {self.config.grid_spacing}%", "INFO")
            self.logger.log(f"Upper Grids: {self.config.grid_upper_count}", "INFO")
            self.logger.log(f"Lower Grids: {self.config.grid_lower_count}", "INFO")
            self.logger.log(f"Per Order Amount: {self.config.grid_per_order_amount} USDT", "INFO")
            self.logger.log(f"Dynamic Mode: {self.config.grid_dynamic_mode}", "INFO")
            if self.config.grid_dynamic_mode:
                self.logger.log(f"Breakthrough Threshold: {self.config.grid_breakthrough_threshold}x grid spacing", "INFO")
            self.logger.log("==================================", "INFO")
            
            # 设置事件循环
            self.loop = asyncio.get_running_loop()
            
            # 连接到交易所
            await self.exchange_client.connect()
            await asyncio.sleep(3)  # 等待连接稳定
            
            # 设置网格WebSocket处理程序
            self._setup_grid_websocket_handlers()
            
            # 初始化中心价格
            await self._initialize_center_price()
            
            # 初始化所有网格
            await self._initialize_all_grids()
            
            self.logger.log("Grid trading strategy started successfully!", "INFO")
            
            # 主循环：监控网格状态
            last_monitor_time = 0
            while not self.shutdown_requested:
                current_time = time.time()
                
                # 每60秒监控一次网格状态
                if current_time - last_monitor_time > 60:
                    await self._grid_status_monitor()
                    last_monitor_time = current_time
                
                # 检查价格突破（每10秒检测一次）
                if not hasattr(self, '_last_breakthrough_check'):
                    self._last_breakthrough_check = 0
                
                if current_time - self._last_breakthrough_check > 10:
                    breakthrough_detected = await self._check_price_breakthrough()
                    self._last_breakthrough_check = current_time
                    
                    # 如果发生网格移动，稍等片刻让订单稳定
                    if breakthrough_detected:
                        await asyncio.sleep(5)
                
                # 检查价格停止/暂停条件
                stop_trading, pause_trading = await self._check_price_condition()
                if stop_trading:
                    msg = f"Grid strategy stopped due to stop price triggered"
                    await self.send_notification(msg)
                    await self.graceful_shutdown(msg)
                    break
                
                if pause_trading:
                    await asyncio.sleep(5)
                    continue
                
                await asyncio.sleep(1)  # 主循环间隔
                
        except KeyboardInterrupt:
            self.logger.log("Grid bot stopped by user", "INFO")
            await self.graceful_shutdown("User interruption (Ctrl+C)")
        except Exception as e:
            self.logger.log(f"Critical error in grid strategy: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            await self.graceful_shutdown(f"Critical error: {e}")
            raise
        finally:
            # 清理资源
            try:
                await self.exchange_client.disconnect()
            except Exception as e:
                self.logger.log(f"Error disconnecting from exchange: {e}", "ERROR")
    
    async def graceful_shutdown(self, reason: str = "Unknown"):
        """优雅关闭网格策略"""
        self.logger.log(f"Grid strategy shutdown: {reason}", "INFO")
        self.logger.log(
            f"Final Statistics - Total Profit: {self.total_profit:.4f} USDT, "
            f"Grid Trades: {self.grid_trades_count}, Grid Moves: {self.grid_moves_count}",
            "INFO"
        )
        
        # 可选：取消所有活跃的网格订单
        # 注释掉以保持订单继续工作
        # await self._cancel_all_grid_orders()
        
        await super().graceful_shutdown(reason)
    
    async def _cancel_all_grid_orders(self):
        """取消所有网格订单（可选功能）"""
        try:
            self.logger.log("Cancelling all grid orders...", "INFO")
            
            for order_id in list(self.active_grid_orders.keys()):
                try:
                    await self.exchange_client.cancel_order(order_id)
                    await asyncio.sleep(0.1)  # 避免频率限制
                except Exception as e:
                    self.logger.log(f"Error cancelling order {order_id}: {e}", "WARNING")
            
            self.logger.log("All grid orders cancellation requests sent", "INFO")
            
        except Exception as e:
            self.logger.log(f"Error cancelling grid orders: {e}", "ERROR")
