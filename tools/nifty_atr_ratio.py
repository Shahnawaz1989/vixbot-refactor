# nifty_atr_ratio.py - PC version with Excel output
import pandas as pd
from SmartApi import SmartConnect
import pyotp
from datetime import datetime, timedelta
import os

# ====== ANGEL API CREDENTIALS ======
API_KEY = "RjUByYN1"          # SmartAPI key
CLIENT_CODE = "M173002"       # Client code
PASSWORD = "7856"             # Login password
TOTP_SECRET = "T3ZSXQE2RXR4UIBVE5FP3COUTQ"  # TOTP secret
# ===================================

NIFTY_TOKEN = "99926000"


def smartlogin():
    """Angel SmartAPI login.[cite:1][cite:32]"""
    try:
        api = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        data = api.generateSession(CLIENT_CODE, PASSWORD, totp)

        if data['status']:
            print(f"✓ Login successful: {CLIENT_CODE}")
            return api
        else:
            print(f"✗ Login failed: {data}")
            return None
    except Exception as e:
        print(f"✗ Login error: {e}")
        return None


def get_daily_candles(api, from_date, to_date):
    """Fetch 1D NIFTY candles from Angel API.[cite:20][cite:26]"""
    try:
        params = {
            "exchange": "NSE",
            "symboltoken": NIFTY_TOKEN,
            "interval": "ONE_DAY",
            "fromdate": from_date.strftime("%Y-%m-%d 09:15"),
            "todate": to_date.strftime("%Y-%m-%d 15:30")
        }

        response = api.getCandleData(params)

        if response['status'] and response['data']:
            df = pd.DataFrame(response['data'],
                              columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df[['open', 'high', 'low', 'close']] = df[[
                'open', 'high', 'low', 'close']].astype(float)
            df['date'] = pd.to_datetime(df['timestamp']).dt.date
            return df[['date', 'open', 'high', 'low', 'close']]
        else:
            print(f"✗ Data fetch error: {response}")
            return None

    except Exception as e:
        print(f"✗ Exception: {e}")
        return None


def calculate_atr(df, period=14):
    """Calculate ATR(14) using pandas.[web:12][web:22][cite:9]"""
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift())
    low_close = abs(df['low'] - df['close'].shift())

    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()

    return atr


def process_data(df):
    """Add ATR and Ratio columns.[cite:8][cite:9]"""
    df['atr'] = calculate_atr(df)
    df['range'] = df['high'] - df['low']
    df['ratio'] = df['range'] / df['atr']

    # Clean first 14 rows (ATR calculation needs warmup)
    df = df[14:].copy()
    df = df[['date', 'high', 'low', 'atr', 'ratio']].round(2)

    return df


def save_to_excel(df, filename="nifty_atr_results.xlsx"):
    """Save results to Excel.[cite:1]"""
    output_dir = "atr_results"
    os.makedirs(output_dir, exist_ok=True)

    filepath = os.path.join(output_dir, filename)
    df.to_excel(filepath, index=False, sheet_name="ATR Analysis")

    print(f"\n✓ Results saved: {filepath}")
    print(f"✓ Total records: {len(df)}")

    # High volatility summary
    high_vol = df[df['ratio'] > 2.0]
    if len(high_vol) > 0:
        print(f"\n🚨 High Volatility Days (Ratio > 2.0): {len(high_vol)}")
        print(high_vol.to_string(index=False))


def main():
    print("=" * 50)
    print("NIFTY 1D High-Low / ATR(14) Analyzer")
    print("=" * 50)

    # Date range input
    from_date_str = input("\nFrom Date (YYYY-MM-DD): ").strip()
    to_date_str = input("To Date (YYYY-MM-DD): ").strip()

    try:
        from_date = datetime.strptime(from_date_str, "%Y-%m-%d")
        to_date = datetime.strptime(to_date_str, "%Y-%m-%d")

        # Add buffer for ATR calculation (need 14+ days before start)
        fetch_from = from_date - timedelta(days=20)

    except ValueError:
        print("✗ Invalid date format. Use YYYY-MM-DD")
        return

    # Login
    api = smartlogin()
    if not api:
        print("✗ Login failed. Check credentials.")
        return

    # Fetch data
    print(f"\n⏳ Fetching data from {fetch_from.date()} to {to_date.date()}...")
    df = get_daily_candles(api, fetch_from, to_date)

    if df is None or len(df) < 15:
        print("✗ Insufficient data. Need at least 15 days.")
        return

    print(f"✓ Fetched {len(df)} candles")

    # Calculate ATR and Ratio
    print("⏳ Calculating ATR(14) and Ratio...")
    result_df = process_data(df)

    # Filter to requested date range
    result_df = result_df[
        (result_df['date'] >= from_date.date()) &
        (result_df['date'] <= to_date.date())
    ]

    if len(result_df) == 0:
        print("✗ No data in selected date range.")
        return

    # Save to Excel
    filename = f"nifty_atr_{from_date_str}_to_{to_date_str}.xlsx"
    save_to_excel(result_df, filename)

    print("\n✓ Analysis complete!")


if __name__ == "__main__":
    main()
