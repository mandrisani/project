import oregano
from oregano_defs import *
from get_bias import get_bias

def OnStateUpdate(context, instrument, state):
	pass

def PredictBias(sym):
	return get_bias(sym)

def Activate(context, instrument):
	pass

def OnOrderExecuted(context, order, price, shares):
	instr = order.GetInstrument()
	bias = instr.GetPriceBias()
	if (bias == PRICE_BIAS_UP and instr.position < 0) or (bias == PRICE_BIAS_DOWN and instr.position > 0): # instant closing
		context.InstantClose(instr, "Posted Order Filled -> Instant Close")
	elif (bias == PRICE_BIAS_UP and instr.position > 0) or (bias == PRICE_BIAS_DOWN and instr.position < 0): # skip closing
		context.ImmediateClose(instr, price, "Posted Order Filled -> Immediate Close")

def OnL1Update(context, instrument, l1):
	closing_reason = ""
	closing_volume = 0
	upnl = instrument.GetUPnL()
	if upnl > 0 and upnl > instrument.GetCloseAtGain(): # unrealized profit
		closing_reason = "Unlealized profit"
	elif upnl < 0 and -upnl > instrument.GetCloseAtLoss(): # unrealized loss
		closing_reason = "Unrealized loss"
	elif instrument.position > instrument.GetParams().max_long_pos: # long exceeds limit
		closing_reason = "Long position exceeded limit"
		closing_volume = instrument.position - instrument.GetParams().max_long_pos
	elif -instrument.position > instrument.GetParams().max_short_pos: # short exceeds limit
		closing_reason = "Short position exceeded limit"
		closing_volume = instrument.position + instrument.GetParams().max_short_pos
	if (not instrument.GetParams().halt) and instrument.AtLoss(upnl): # stop loss
		closing_reason = "Loss exceeded"
		instrument.GetParams().halt = True
	if len(closing_reason):
		context.ClosePosition(instrument, closing_reason, closing_volume)
		return
	if instrument.GetParams().halt:
		return

	price_trend = instrument.GetPriceBias()
	if upnl >= 0:
		if instrument.position > 0:
			price_trend = PRICE_BIAS_DOWN
		elif instrument.position > 0:
			price_trend = PRICE_BIAS_UP
	trader_bid = instrument.GetTraderBestBidPrice() >= l1.bid_price
	trader_ask = instrument.GetTraderBestAskPrice() > 0 and instrument.GetTraderBestAskPrice() <= l1.ask_price
	low_l1_bid = instrument.IsLowL1(l1.bid_volume, True)
	low_l1_ask = instrument.IsLowL1(l1.ask_volume, False)
	eod = context.GetState() == STATE_EOD
	post_l2_bid = (eod or low_l1_bid or instrument.position > 0 or price_trend == PRICE_BIAS_DOWN) and not (instrument.position < 0 and instrument.GetUPnL2() > 0)
	post_l2_ask = (eod or low_l1_ask or instrument.position < 0 or price_trend == PRICE_BIAS_UP) and not (instrument.position > 0 and instrument.GetUPnL2() > 0)
	bid_price = instrument.GetNextBidPrice() if post_l2_bid else l1.bid_price
	ask_price = instrument.GetNextAskPrice() if post_l2_ask else l1.ask_price
	send_buy = (not trader_bid or instrument.position < 0) and bid_price > 0
	send_sell = (not trader_ask or instrument.position > 0) and ask_price > 0
	if instrument.GetParams().close_only:
		send_sell = instrument.position > 0
		send_buy = instrument.position < 0

	buy_mp = instrument.GetParams().destination
	sell_mp = buy_mp

	lax_order = None
	orders = context.GetOrders(instrument)
	for ord in orders:
		if ord.IsLAX():
			if lax_order is not None:
				context.CancelOrder(ord, "Wrong LAX")
			else:
				lax_order = ord
			continue

		if ord.IsBid():
			if ord.price >= ask_price:
				send_sell = False
			if ord.price != bid_price:
				context.CancelOrder(ord, "Mismatch with L1 Bid")
			elif trader_bid and instrument.position >= 0:
				context.CancelOrder(ord, "PPro8 buy level exists")
			elif ord.IsPartialFill():
				context.CancelOrder(ord, "Part Filled")
			elif instrument.GetParams().close_only and instrument.position >= 0:
				context.CancelOrder(ord, "Close Only - Cancel Bid")
			send_buy = False
		else:
			if ord.price <= bid_price:
				send_buy = False
			if ord.price != ask_price:
				context.CancelOrder(ord, "Mismatch with L1 Ask")
			elif trader_ask and instrument.position <= 0:
				context.CancelOrder(ord, "PPro8 sell level exists")
			elif ord.IsPartialFill():
				context.CancelOrder(ord, "Part Filled")
			elif instrument.GetParams().close_only and instrument.position <= 0:
				context.CancelOrder(ord, "Close Only - Cancel Ask")
			send_sell = False

	if send_buy and bid_price > 0:
		buy_mp1 = context.GetDestinationFromHeatMap(instrument, price_trend == PRICE_BIAS_UP)
		if buy_mp1 != '\0':
			buy_mp = buy_mp1
		context.SendOrder(instrument, bid_price, instrument.GetParams().order_shares, True, buy_mp)

	if send_sell and ask_price > 0:
		sell_mp1 = context.GetDestinationFromHeatMap(instrument, price_trend == PRICE_BIAS_DOWN)
		if sell_mp1 != '\0':
			sell_mp = sell_mp1
		context.SendOrder(instrument, ask_price, instrument.GetParams().order_shares, False, sell_mp)

	if instrument.position != 0 and lax_order is not None:
		send_lax = context.IsLAXEnabled() and upnl >= 0 and ((instrument.position > 0 and price_trend != PRICE_BIAS_UP) or (instrument.position < 0 and price_trend == PRICE_BIAS_DOWN))
		lax_price = l1.bid_price if instrument.position > 0 else l1.ask_price
		if lax_order is not None and not send_lax:
			context.CancelOrder(lax_order, "LAX mismatch")
			lax_order = None
		if send_lax and lax_order is None:
			context.SendOrder(instrument, lax_price, abs(instrument.position), instrument.position < 0 , LAX)

def OnTOS(context, instrument, tos, last_vcity, new_vcity):
	if new_vcity > 0 and last_vcity < 0 and instrument.position < 0:
		context.InstantClose(instrument, "Instant Close at Velocity Change")
	#if tos.GetMarketID() == LYNX: # who trades on Lynx?
		#instrument.SetPriceBias(PRICE_BIAS_NONE)

	if tos.price < instrument.GetMarketData().GetBollinger().lower:
		instrument.SetPriceBias(PRICE_BIAS_DOWN)
	elif tos.price > instrument.GetMarketData().GetBollinger().upper:
		instrument.SetPriceBias(PRICE_BIAS_UP)

def OnL2Update(context, instrument, l2):
	pass

def OnStateUpdate(context, instrument, state):
	pass

