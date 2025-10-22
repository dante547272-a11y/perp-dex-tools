"""
Grid Trading Bot - Classic Grid Strategy Implementation
支持所有交易所的通用网格策略
"""

import asyncio
import time
import decimal
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
        
        # 验证网格参数是否会导致价格为0或负数
        spacing_decimal = self.config.grid_spacing / 100
        max_lower_reduction = spacing_decimal * self.config.grid_lower_count
        
        if max_lower_reduction >= 1.0:
            raise ValueError(
                f"Grid configuration will cause negative prices: "
                f"spacing({self.config.grid_spacing}%) × lower_count({self.config.grid_lower_count}) "
                f"= {max_lower_reduction*100:.1f}% >= 100%. "
                f"Reduce grid spacing or lower grid count."
            )
        
        # 警告当价格降幅过大时
        if max_lower_reduction >= 0.8:
            self.logger.log(
                f"WARNING: Lower grids will reduce price by {max_lower_reduction*100:.1f}%. "
                f"Consider reducing grid spacing or count to avoid very low prices.",
                "WARNING"
            )
            
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
            
            # 价格安全检查
            if price <= 0:
                self.logger.log(
                    f"Invalid buy grid price {price:.8f} at level {i}. "
                    f"Reduce grid count or spacing to avoid price <= 0",
                    "ERROR"
                )
                raise ValueError(f"Buy grid price <= 0 at level {i}: {price}")
            
            # 计算代币数量：USDT金额 / 价格
            try:
                token_quantity = self.config.grid_per_order_amount / price
                token_quantity = self._round_quantity(token_quantity)
            except decimal.DivisionByZero:
                self.logger.log(
                    f"Division by zero error: price={price}, per_order_amount={self.config.grid_per_order_amount}",
                    "ERROR"
                )
                raise ValueError(f"Cannot calculate quantity: price is {price}")
            
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
            
            # 价格安全检查
            if price <= 0:
                self.logger.log(
                    f"Invalid sell grid price {price:.8f} at level {i}. "
                    f"Price calculation error in sell grid",
                    "ERROR"
                )
                raise ValueError(f"Sell grid price <= 0 at level {i}: {price}")
            
            # 计算代币数量：USDT金额 / 价格
            try:
                token_quantity = self.config.grid_per_order_amount / price
                token_quantity = self._round_quantity(token_quantity)
            except decimal.DivisionByZero:
                self.logger.log(
                    f"Division by zero error: price={price}, per_order_amount={self.config.grid_per_order_amount}",
                    "ERROR"
                )
                raise ValueError(f"Cannot calculate quantity: price is {price}")
            
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
        
        # 移除已成交的订单
        del self.active_grid_orders[filled_order_id]
        
        # 网格策略核心逻辑：订单成交后在相同价格下反向订单
        if filled_grid.side == 'buy':
            # 买单成交 → 在相同价格下卖单
            await self._place_opposite_order_after_buy(filled_grid, filled_price, filled_size)
        else:
            # 卖单成交 → 在相同价格下买单
            await self._place_opposite_order_after_sell(filled_grid, filled_price, filled_size)
        
        # 计算网格交易利润（基于价格波动获利）
        self.grid_trades_count += 1
        self.logger.log(
            f"📊 Grid Trade #{self.grid_trades_count}: {filled_grid.side.upper()} {filled_size} @ {filled_price:.4f}",
            "INFO"
        )
    
    async def _place_opposite_order_after_buy(self, filled_grid: GridLevel, filled_price: Decimal, filled_size: Decimal):
        """买单成交后，在相同价格下卖单"""
        try:
            # 创建反向卖单（相同价格）
            opposite_grid = GridLevel(
                price=filled_price,  # 相同价格
                side='sell',         # 反向方向
                quantity=filled_size,  # 使用实际成交数量
                level_index=filled_grid.level_index  # 相同网格级别
            )
            
            # 下反向订单
            success = await self._place_grid_order(opposite_grid)
            
            if success:
                self.logger.log(
                    f"🔄 Opposite order placed: SELL {filled_size} @ {filled_price:.4f} "
                    f"[After BUY @ {filled_price:.4f} - Level {filled_grid.level_index}]",
                    "INFO"
                )
            else:
                self.logger.log(
                    f"❌ Failed to place opposite sell order @ {filled_price:.4f}",
                    "WARNING"
                )
                
        except Exception as e:
            self.logger.log(f"Error placing opposite order after buy: {e}", "ERROR")
    
    async def _place_opposite_order_after_sell(self, filled_grid: GridLevel, filled_price: Decimal, filled_size: Decimal):
        """卖单成交后，在相同价格下买单"""
        try:
            # 计算相同价格的买入数量：USDT金额 / 价格
            try:
                buy_quantity = self.config.grid_per_order_amount / filled_price
                buy_quantity = self._round_quantity(buy_quantity)
            except decimal.DivisionByZero:
                self.logger.log(f"Division by zero in opposite order: price={filled_price}", "ERROR")
                return
            
            # 创建反向买单（相同价格）
            opposite_grid = GridLevel(
                price=filled_price,  # 相同价格
                side='buy',          # 反向方向
                quantity=buy_quantity,  # 重新计算数量
                level_index=filled_grid.level_index  # 相同网格级别
            )
            
            # 下反向订单
            success = await self._place_grid_order(opposite_grid)
            
            if success:
                self.logger.log(
                    f"🔄 Opposite order placed: BUY {buy_quantity} @ {filled_price:.4f} "
                    f"[After SELL @ {filled_price:.4f} - Level {filled_grid.level_index}]",
                    "INFO"
                )
            else:
                self.logger.log(
                    f"❌ Failed to place opposite buy order @ {filled_price:.4f}",
                    "WARNING"
                )
                
        except Exception as e:
            self.logger.log(f"Error placing opposite order after sell: {e}", "ERROR")
    
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
        """执行网格移动 - 保持网格数量平衡的优化版本"""
        try:
            old_center_price = self.center_price
            self.center_price = self.exchange_client.round_to_tick(new_center_price)
            
            if direction == "UP":
                # 网格向上移动：取消最低价买单，添加最高价卖单
                cancelled, added = await self._move_grid_up_optimized()
            else:
                # 网格向下移动：取消最高价卖单，添加最低价买单  
                cancelled, added = await self._move_grid_down_optimized()
            
            # 记录移动结果
            self.grid_moves_count += 1
            
            self.logger.log(
                f"🔄 Grid moved {direction}: {old_center_price:.4f} -> {self.center_price:.4f} "
                f"| Cancelled: {cancelled} | Added: {added} | Total moves: {self.grid_moves_count}",
                "INFO"
            )
            
        except Exception as e:
            self.logger.log(f"Error executing grid move: {e}", "ERROR")
    
    async def _move_grid_up_optimized(self) -> Tuple[int, int]:
        """网格向上移动的优化版本：取消最低价买单，添加最高价卖单"""
        cancelled_count = 0
        added_count = 0
        
        try:
            # 1. 找到最低价格的买单（最远离新中心价格）
            buy_orders = [(order_id, grid_level) for order_id, grid_level in self.active_grid_orders.items() 
                         if grid_level.side == 'buy']
            
            if not buy_orders:
                self.logger.log("No buy orders found for grid move up", "WARNING")
                return cancelled_count, added_count
            
            # 按价格排序，找到最低价的买单
            lowest_buy = min(buy_orders, key=lambda x: x[1].price)
            order_id_to_cancel, grid_to_cancel = lowest_buy
            
            # 2. 取消最低价买单
            try:
                cancel_result = await self.exchange_client.cancel_order(order_id_to_cancel)
                if cancel_result.success:
                    cancelled_count += 1
                    del self.active_grid_orders[order_id_to_cancel]
                    self.logger.log(
                        f"Cancelled lowest buy order: {grid_to_cancel.price:.4f} [Level {grid_to_cancel.level_index}]", 
                        "INFO"
                    )
                else:
                    self.logger.log(f"Failed to cancel buy order at {grid_to_cancel.price:.4f}", "WARNING")
            except Exception as e:
                self.logger.log(f"Error cancelling lowest buy order: {e}", "ERROR")
            
            # 3. 计算并添加新的最高价卖单
            spacing_decimal = self.config.grid_spacing / 100
            
            # 找到当前最高价卖单
            sell_orders = [(order_id, grid_level) for order_id, grid_level in self.active_grid_orders.items() 
                          if grid_level.side == 'sell']
            
            if sell_orders:
                highest_sell_price = max(sell_orders, key=lambda x: x[1].price)[1].price
                new_sell_price = highest_sell_price * (1 + spacing_decimal)
            else:
                # 如果没有卖单，从中心价格开始
                new_sell_price = self.center_price * (1 + spacing_decimal)
            
            new_sell_price = self.exchange_client.round_to_tick(new_sell_price)
            
            # 计算卖单数量
            try:
                sell_quantity = self.config.grid_per_order_amount / new_sell_price
                sell_quantity = self._round_quantity(sell_quantity)
            except:
                self.logger.log(f"Error calculating sell quantity for price {new_sell_price}", "ERROR")
                return cancelled_count, added_count
            
            # 创建新的卖单网格级别
            new_sell_level = GridLevel(
                price=new_sell_price,
                side='sell',
                quantity=sell_quantity,
                level_index=self.config.grid_upper_count + 1  # 新的最高级别
            )
            
            # 4. 下新的卖单
            success = await self._place_grid_order(new_sell_level)
            if success:
                added_count += 1
                self.logger.log(
                    f"Added new highest sell order: {new_sell_price:.4f} [Level {new_sell_level.level_index}]",
                    "INFO"
                )
            else:
                self.logger.log(f"Failed to add new sell order at {new_sell_price:.4f}", "WARNING")
            
            return cancelled_count, added_count
            
        except Exception as e:
            self.logger.log(f"Error in optimized grid move up: {e}", "ERROR")
            return cancelled_count, added_count
    
    async def _move_grid_down_optimized(self) -> Tuple[int, int]:
        """网格向下移动的优化版本：取消最高价卖单，添加最低价买单"""
        cancelled_count = 0
        added_count = 0
        
        try:
            # 1. 找到最高价格的卖单（最远离新中心价格）
            sell_orders = [(order_id, grid_level) for order_id, grid_level in self.active_grid_orders.items() 
                          if grid_level.side == 'sell']
            
            if not sell_orders:
                self.logger.log("No sell orders found for grid move down", "WARNING")
                return cancelled_count, added_count
            
            # 按价格排序，找到最高价的卖单
            highest_sell = max(sell_orders, key=lambda x: x[1].price)
            order_id_to_cancel, grid_to_cancel = highest_sell
            
            # 2. 取消最高价卖单
            try:
                cancel_result = await self.exchange_client.cancel_order(order_id_to_cancel)
                if cancel_result.success:
                    cancelled_count += 1
                    del self.active_grid_orders[order_id_to_cancel]
                    self.logger.log(
                        f"Cancelled highest sell order: {grid_to_cancel.price:.4f} [Level {grid_to_cancel.level_index}]", 
                        "INFO"
                    )
                else:
                    self.logger.log(f"Failed to cancel sell order at {grid_to_cancel.price:.4f}", "WARNING")
            except Exception as e:
                self.logger.log(f"Error cancelling highest sell order: {e}", "ERROR")
            
            # 3. 计算并添加新的最低价买单
            spacing_decimal = self.config.grid_spacing / 100
            
            # 找到当前最低价买单
            buy_orders = [(order_id, grid_level) for order_id, grid_level in self.active_grid_orders.items() 
                         if grid_level.side == 'buy']
            
            if buy_orders:
                lowest_buy_price = min(buy_orders, key=lambda x: x[1].price)[1].price
                new_buy_price = lowest_buy_price * (1 - spacing_decimal)
            else:
                # 如果没有买单，从中心价格开始
                new_buy_price = self.center_price * (1 - spacing_decimal)
            
            new_buy_price = self.exchange_client.round_to_tick(new_buy_price)
            
            # 价格安全检查
            if new_buy_price <= 0:
                self.logger.log(f"Invalid new buy price: {new_buy_price:.8f}", "ERROR")
                return cancelled_count, added_count
            
            # 计算买单数量
            try:
                buy_quantity = self.config.grid_per_order_amount / new_buy_price
                buy_quantity = self._round_quantity(buy_quantity)
            except:
                self.logger.log(f"Error calculating buy quantity for price {new_buy_price}", "ERROR")
                return cancelled_count, added_count
            
            # 创建新的买单网格级别
            new_buy_level = GridLevel(
                price=new_buy_price,
                side='buy',
                quantity=buy_quantity,
                level_index=-(self.config.grid_lower_count + 1)  # 新的最低级别
            )
            
            # 4. 下新的买单
            success = await self._place_grid_order(new_buy_level)
            if success:
                added_count += 1
                self.logger.log(
                    f"Added new lowest buy order: {new_buy_price:.4f} [Level {new_buy_level.level_index}]",
                    "INFO"
                )
            else:
                self.logger.log(f"Failed to add new buy order at {new_buy_price:.4f}", "WARNING")
            
            return cancelled_count, added_count
            
        except Exception as e:
            self.logger.log(f"Error in optimized grid move down: {e}", "ERROR")
            return cancelled_count, added_count
    
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
