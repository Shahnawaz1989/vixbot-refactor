import xlwings as xw
import json
from pathlib import Path

# ------------ CONFIG ------------
EXCEL_FILE = Path("GANN ONLY NIFTY MIDDAY BOT.xlsx").resolve()
SHEET_NAME = "15 min NIFTY"
OUTPUT_JSON = Path("gann_midday_lookup_24000_27000.json")
CMP_START = 24000
CMP_END = 27000
# ------------ CONFIG END --------

def main():
    print(f"Opening Excel: {EXCEL_FILE}")
    app = xw.App(visible=False)
    try:
        wb = app.books.open(EXCEL_FILE)
        sheet = wb.sheets[SHEET_NAME]

        gann_table = {}
        print(f"Generating MIDDAY Gann table {CMP_START}-{CMP_END}...")

        for cmp_val in range(CMP_START, CMP_END + 1):
            sheet.range("E4").value = cmp_val

            data = {
                "cmp": cmp_val,
                "buy_entry": float(sheet.range("H8").value or 0),
                "buy_t15": float(sheet.range("H8").value or 0),
                "buy_t2": float(sheet.range("J8").value or 0),
                "buy_t25": float(sheet.range("H9").value or 0),
                "buy_t3": float(sheet.range("J9").value or 0),
                "buy_t35": float(sheet.range("H10").value or 0),
                "buy_t4": float(sheet.range("J10").value or 0),

                "buy_entry_opp": float(sheet.range("J7").value or 0),

                "sell_entry": float(sheet.range("N8").value or 0),
                "sell_t15": float(sheet.range("N8").value or 0),
                "sell_t2": float(sheet.range("M8").value or 0),
                "sell_t25": float(sheet.range("N9").value or 0),
                "sell_t3": float(sheet.range("M9").value or 0),
                "sell_t35": float(sheet.range("N10").value or 0),
                "sell_t4": float(sheet.range("M10").value or 0),

                "sell_entry_opp": float(sheet.range("M7").value or 0),

                "buy_sl": float(sheet.range("N8").value or 0),
                "sell_sl": float(sheet.range("H8").value or 0),
            }

            gann_table[str(cmp_val)] = data

            if cmp_val % 100 == 0:
                print(f"✓ {cmp_val}")

        with open(OUTPUT_JSON, "w") as f:
            json.dump(gann_table, f, indent=2)

        print(f"\n✅ DONE! Saved: {OUTPUT_JSON}")
        print(f"Total entries: {len(gann_table)}")

    finally:
        wb.close()
        app.quit()

if __name__ == "__main__":
    main()
