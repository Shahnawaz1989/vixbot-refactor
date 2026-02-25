def place_option_buy(api, symbol, token, quantity):
    try:
        orderparams = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": "BUY",
            "exchange": "NFO",
            "ordertype": "MARKET",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": 0,
            "squareoff": 0,
            "stoploss": 0,
            "quantity": quantity
        }

        response = api.placeOrder(orderparams)

        return {
            "status": True,
            "data": response
        }

    except Exception as e:
        print("Order Error:", e)
        return {
            "status": False,
            "error": str(e)
        }
