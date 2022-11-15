from solana.publickey import PublicKey
from typing import Optional

from driftpy.clearing_house import ClearingHouse
from driftpy.constants.numeric_constants import *
from driftpy.types import *
from driftpy.accounts import *
from driftpy.math.positions import *
from driftpy.math.margin import *
from driftpy.math.spot_market import *
from driftpy.math.oracle import *

def find(l: list, f):
    valid_values = [v for v in l if f(v)]
    if len(valid_values) == 0:
        return None
    else:
        return valid_values[0]


class ClearingHouseUser:
    """This class is the main way to interact with Drift Protocol.

    It allows you to subscribe to the various accounts where the Market's state is
    stored, as well as: opening positions, liquidating, settling funding, depositing &
    withdrawing, and more.

    The default way to construct a ClearingHouse instance is using the
    [create][driftpy.clearing_house.ClearingHouse.create] method.
    """

    def __init__(
        self,
        clearing_house: ClearingHouse,
        authority: Optional[PublicKey] = None,
        subaccount_id: int = 0,
        use_cache: bool = False,
    ):
        """Initialize the ClearingHouse object.

        Note: you probably want to use
        [create][driftpy.clearing_house.ClearingHouse.create]
        instead of this method.

        Args:
            clearing_house: The Drift ClearingHouse object.
            authority: user authority to focus on (if None, the clearing
            house's .program.provider.wallet.pk is used as the auth)
        """
        self.clearing_house = clearing_house
        self.authority = authority
        if self.authority is None:
            self.authority = clearing_house.authority

        self.program = clearing_house.program
        self.oracle_program = clearing_house
        self.connection = self.program.provider.connection
        self.subaccount_id = subaccount_id
        self.use_cache = use_cache
        self.cache_is_set = False

    # cache all state, perpmarket, oracle, etc. in single cache -- user calls reload 
    # when they want to update the data? 
        # get_spot_market
        # get_perp_market 
        # get_user 
        # if state = cache => get cached_market else get new market 

    async def set_cache(self):
        self.cache_is_set = True

        self.CACHE = {}
        state = await get_state_account(self.program)
        self.CACHE['state'] = state

        spot_markets = []
        spot_market_oracle_data = []
        for i in range(state.number_of_spot_markets):
            spot_market = await get_spot_market_account(
                self.program, i
            )
            spot_markets.append(spot_market)

            if i == 0: 
                spot_market_oracle_data.append(1)
            else:
                oracle_data = await get_oracle_data(self.connection, spot_market.oracle)
                spot_market_oracle_data.append(oracle_data)
            
        self.CACHE['spot_markets'] = spot_markets
        self.CACHE['spot_market_oracles'] = spot_market_oracle_data
        
        perp_markets = []
        perp_market_oracle_data = []
        for i in range(state.number_of_markets):
            perp_market = await get_perp_market_account(
                self.program, i
            )
            perp_markets.append(perp_market)

            oracle_data = await get_oracle_data(self.connection, perp_market.amm.oracle)
            perp_market_oracle_data.append(oracle_data)

        self.CACHE['perp_markets'] = perp_markets
        self.CACHE['perp_market_oracles'] = perp_market_oracle_data

        user = await get_user_account(
            self.program, self.authority, self.subaccount_id
        )
        self.CACHE['user'] = user

    async def get_spot_oracle_data(self, spot_market: SpotMarket):
        if self.use_cache: 
            assert self.cache_is_set, 'must call clearing_house_user.set_cache() first'
            return self.CACHE['spot_market_oracles'][spot_market.market_index]
        else: 
            oracle_data = await get_oracle_data(self.connection, spot_market.oracle)        
            return oracle_data
    
    async def get_perp_oracle_data(self, perp_market: PerpMarket):
        if self.use_cache: 
            assert self.cache_is_set, 'must call clearing_house_user.set_cache() first'
            return self.CACHE['perp_market_oracles'][perp_market.market_index]
        else: 
            oracle_data = await get_oracle_data(self.connection, perp_market.amm.oracle)        
            return oracle_data
    
    async def get_state(self):
        if self.use_cache: 
            assert self.cache_is_set, 'must call clearing_house_user.set_cache() first'
            return self.CACHE['state']
        else: 
            return await get_state_account(self.program)

    async def get_spot_market(self, i):
        if self.use_cache: 
            assert self.cache_is_set, 'must call clearing_house_user.set_cache() first'
            return self.CACHE['spot_markets'][i]
        else: 
            return await get_spot_market_account(
                self.program, i
            )
    
    async def get_perp_market(self, i):
        if self.use_cache: 
            assert self.cache_is_set, 'must call clearing_house_user.set_cache() first'
            return self.CACHE['perp_markets'][i]
        else: 
            return await get_perp_market_account(
                self.program, i
            )

    async def get_user(self):
        if self.use_cache: 
            assert self.cache_is_set, 'must call clearing_house_user.set_cache() first'
            return self.CACHE['user']
        else: 
            return await get_user_account(
                self.program, self.authority, self.subaccount_id
            )

    async def get_spot_market_liability(
        self,
        market_index=None,
        margin_category=None,
        liquidation_buffer=None,
        include_open_orders=None,
    ):
        user = await self.get_user()
        total_liability = 0
        for position in user.spot_positions:
            if is_spot_position_available(position) or (
                market_index is not None and position.market_index != market_index
            ):
                continue

            spot_market = await self.get_spot_market(position.market_index)

            if position.market_index == QUOTE_ASSET_BANK_INDEX:
                if str(position.balance_type) == "SpotBalanceType.Borrow()":
                    token_amount = get_token_amount(
                        position.scaled_balance, spot_market, position.balance_type
                    )
                    weight = SPOT_WEIGHT_PRECISION
                    if margin_category == MarginCategory.INITIAL:
                        weight = max(weight, user.max_margin_ratio)

                    value = token_amount * weight / SPOT_WEIGHT_PRECISION
                    total_liability += value
                    continue
                else:
                    continue

            oracle_data = await self.get_spot_oracle_data(spot_market)
            if not include_open_orders:
                if str(position.balance_type) == "SpotBalanceType.Borrow()":
                    token_amount = get_token_amount(
                        position.scaled_balance, spot_market, position.balance_type
                    )
                    liability_value = get_spot_liability_value(
                        token_amount,
                        oracle_data,
                        spot_market,
                        margin_category,
                        liquidation_buffer,
                        user.max_margin_ratio,
                    )
                    total_liability += liability_value
                    continue
                else:
                    continue

            (
                worst_case_token_amount,
                worst_case_quote_amount,
            ) = get_worst_case_token_amounts(position, spot_market, oracle_data)

            if worst_case_token_amount < 0:
                baa_value = get_spot_liability_value(
                    abs(worst_case_token_amount),
                    oracle_data,
                    spot_market,
                    margin_category,
                    liquidation_buffer,
                    user.max_margin_ratio,
                )
                total_liability += baa_value

            if worst_case_quote_amount < 0:
                weight = SPOT_WEIGHT_PRECISION
                if margin_category == MarginCategory.INITIAL:
                    weight = max(weight, user.max_margin_ratio)
                weighted_value = (
                    abs(worst_case_quote_amount) * weight / SPOT_WEIGHT_PRECISION
                )
                total_liability += weighted_value

        return total_liability

    async def get_total_perp_positon(
        self,
        margin_category: Optional[MarginCategory] = None,
        liquidation_buffer: Optional[int] = 0,
        include_open_orders: bool = False,
    ):
        user = await self.get_user()

        unrealized_pnl = 0
        for position in user.perp_positions:
            market = await self.get_perp_market(position.market_index)

            if position.lp_shares > 0:
                pass

            price = (await self.get_perp_oracle_data(market)).price
            base_asset_amount = (
                calculate_worst_case_base_asset_amount(position)
                if include_open_orders
                else position.base_asset_amount
            )
            base_value = (
                abs(base_asset_amount)
                * price
                / (AMM_TO_QUOTE_PRECISION_RATIO * PRICE_PRECISION)
            )

            if margin_category is not None:
                margin_ratio = calculate_market_margin_ratio(
                    market, abs(base_asset_amount), margin_category
                )

                if margin_category == MarginCategory.INITIAL:
                    margin_ratio = max(margin_ratio, user.max_margin_ratio)

                if liquidation_buffer is not None:
                    margin_ratio += liquidation_buffer

                base_value = base_value * margin_ratio / MARGIN_PRECISION

            unrealized_pnl += base_value
        return unrealized_pnl

    async def can_be_liquidated(self) -> bool:
        total_collateral = await self.get_total_collateral()

        user = await self.get_user()
        liquidation_buffer = None
        if user.being_liquidated:
            liquidation_buffer = (
                await self.get_state()
            ).liquidation_margin_buffer_ratio

        maintenance_req = await self.get_margin_requirement(
            MarginCategory.MAINTENANCE, liquidation_buffer
        )

        return total_collateral < maintenance_req

    async def get_margin_requirement(
        self, margin_category: MarginCategory, liquidation_buffer: Optional[int] = 0
    ) -> int:
        perp_liability = self.get_total_perp_positon(
            margin_category, liquidation_buffer, True
        )
        spot_liability = self.get_spot_market_liability(
            None, margin_category, liquidation_buffer, True
        )
        return await perp_liability + await spot_liability

    async def get_total_collateral(
        self, margin_category: Optional[MarginCategory] = None
    ) -> int:
        spot_collateral = await self.get_spot_market_asset_value(
            margin_category,
            include_open_orders=True,
        )
        pnl = await self.get_unrealized_pnl(
            True, with_weight_margin_category=margin_category
        )
        total_collatearl = spot_collateral + pnl
        return total_collatearl

    async def get_free_collateral(self):
        total_collateral = await self.get_total_collateral()
        init_margin_req = await self.get_margin_requirement(
            MarginCategory.INITIAL,
        )
        free_collateral = total_collateral - init_margin_req
        free_collateral = max(0, free_collateral)
        return free_collateral

    async def get_user_spot_position(
        self,
        market_index: int,
    ) -> Optional[SpotPosition]:
        user = await self.get_user()

        found = False
        for position in user.spot_positions:
            if (
                position.market_index == market_index
                and not is_spot_position_available(position)
            ):
                found = True
                break

        if not found:
            return None

        return position

    async def get_user_position(
        self,
        market_index: int,
    ) -> Optional[PerpPosition]:
        user = await self.get_user()

        found = False
        for position in user.perp_positions:
            if position.market_index == market_index and not is_available(position):
                found = True
                break

        if not found:
            return None

        return position

    async def get_unrealized_pnl(
        self,
        with_funding: bool = False,
        market_index: int = None,
        with_weight_margin_category: Optional[MarginCategory] = None,
    ):
        user = await self.get_user()

        unrealized_pnl = 0
        position: PerpPosition
        for position in user.perp_positions:
            if market_index is not None and position.market_index != market_index:
                continue
            
            market = await self.get_perp_market(position.market_index)

            oracle_data = await self.get_perp_oracle_data(market)
            position_unrealized_pnl = calculate_position_pnl(
                market, position, oracle_data, with_funding
            )

            if with_weight_margin_category is not None:
                raise NotImplementedError(
                    "Only with_weight_margin_category = None supported"
                )

            unrealized_pnl += position_unrealized_pnl

        return unrealized_pnl

    async def get_spot_market_asset_value(
        self,
        margin_category: Optional[MarginCategory] = None,
        include_open_orders=True,
        market_index: Optional[int] = None,
    ):
        user = await self.get_user()
        total_value = 0
        for position in user.spot_positions:
            if is_spot_position_available(position) or (
                market_index is not None and position.market_index != market_index
            ):
                continue

            spot_market = await self.get_spot_market(position.market_index)

            if position.market_index == QUOTE_ASSET_BANK_INDEX:
                spot_token_value = get_token_amount(
                    position.scaled_balance, spot_market, position.balance_type
                )
                total_value += spot_token_value
                continue

            oracle_data = await self.get_spot_oracle_data(spot_market)

            if not include_open_orders:
                if str(position.balance_type) == "SpotBalanceType.Deposit()":
                    token_amount = get_token_amount(
                        position.scaled_balance, spot_market, position.balance_type
                    )
                    asset_value = get_spot_asset_value(
                        token_amount, oracle_data, spot_market, margin_category
                    )
                    total_value += asset_value
                    continue
                else:
                    continue

            (
                worst_case_token_amount,
                worst_case_quote_amount,
            ) = get_worst_case_token_amounts(position, spot_market, oracle_data)

            if worst_case_token_amount > 0:
                baa_value = get_spot_asset_value(
                    worst_case_token_amount, oracle_data, spot_market, margin_category
                )
                total_value += baa_value

            if worst_case_quote_amount > 0:
                total_value += worst_case_quote_amount

        return total_value

    async def get_leverage(
        self, margin_category: Optional[MarginCategory] = None
    ) -> int:
        total_liability = await self.get_margin_requirement(margin_category, None)
        total_asset_value = await self.get_total_collateral(margin_category)

        if total_asset_value == 0 or total_liability == 0:
            return 0

        leverage = total_liability * 10_000 / total_asset_value

        return leverage
