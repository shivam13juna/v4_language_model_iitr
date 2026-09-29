#!/usr/bin/env python
"""Build online_retail.db — the shop's orders — from the public UCI download.

    python prepare_data.py                  # build it if it is missing, then check it
    python prepare_data.py --rebuild        # build it again from the download
    python prepare_data.py --check          # only check the file that is there

Source: UCI Machine Learning Repository, "Online Retail" (Chen, 2012), dataset 352 —
541,909 transactions from a UK online gift retailer, 2010-12-01 to 2011-12-09. CC BY 4.0.
https://archive.ics.uci.edu/dataset/352/online+retail

The spreadsheet has one row per order line. It is normalised into three tables:

    invoices    invoice_no · customer_id · invoice_ts · country · is_cancelled
    line_items  invoice_no · stock_code · quantity · unit_price
    products    stock_code · description           (the most common spelling per code)

Two facts about the columns that the service depends on, and that are easy to get wrong:

  * `country` is the country on the customer's account. It is NOT where an order was
    shipped, and the data has no shipping, dispatch or delivery information at all.
  * An invoice number starting with "C" is a cancellation (`is_cancelled = 1`).
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "online_retail.db"
ZIP = ROOT / "data" / "online_retail.zip"
URL = "https://archive.ics.uci.edu/static/public/352/online+retail.zip"

EXPECTED = {"invoices": 25_900, "line_items": 541_909, "products": 3_958}
# One order whose numbers every part of the workshops relies on.
KNOWN = ("580638", 12381.0, 7, 147.01)


def download() -> bytes:
    if ZIP.exists():
        return ZIP.read_bytes()
    print(f"downloading {URL} (about 23 MB)…")
    request = urllib.request.Request(URL, headers={"User-Agent": "porter-prepare-data"})
    raw = urllib.request.urlopen(request, timeout=300).read()
    ZIP.parent.mkdir(exist_ok=True)
    ZIP.write_bytes(raw)
    return raw


def build(path: Path) -> None:
    import pandas as pd       # only needed here; the service itself never imports pandas

    xlsx = zipfile.ZipFile(io.BytesIO(download())).read("Online Retail.xlsx")
    print("reading the spreadsheet (slow: 541,909 rows of Excel)…")
    df = pd.read_excel(io.BytesIO(xlsx), engine="openpyxl")
    df["InvoiceNo"] = df["InvoiceNo"].astype(str)

    invoices = (df.groupby("InvoiceNo")
                  .agg(customer_id=("CustomerID", "first"),
                       invoice_ts=("InvoiceDate", "first"),
                       country=("Country", "first"))
                  .reset_index()
                  .rename(columns={"InvoiceNo": "invoice_no"}))
    invoices["is_cancelled"] = invoices["invoice_no"].str.startswith("C").astype(int)
    invoices["invoice_ts"] = invoices["invoice_ts"].astype(str)

    products = (df.dropna(subset=["Description"])
                  .groupby("StockCode")["Description"]
                  .agg(lambda s: s.value_counts().index[0])
                  .reset_index())
    products.columns = ["stock_code", "description"]

    lines = df[["InvoiceNo", "StockCode", "Quantity", "UnitPrice"]].copy()
    lines.columns = ["invoice_no", "stock_code", "quantity", "unit_price"]

    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    invoices.to_sql("invoices", con, index=False)
    products.to_sql("products", con, index=False)
    lines.to_sql("line_items", con, index=False)
    con.executescript(
        "CREATE INDEX i_li_inv ON line_items(invoice_no);"
        "CREATE INDEX i_li_sc  ON line_items(stock_code);"
        "CREATE INDEX i_inv_c  ON invoices(country);"
    )
    con.commit()
    con.close()


def check(path: Path) -> bool:
    if not path.exists():
        print(f"❌ {path.name} is missing")
        return False
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ok = True
    for table, rows in EXPECTED.items():
        got = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        mark = "✅" if got == rows else "❌"
        ok &= got == rows
        print(f"{mark} {table:11s} {got:>9,} rows   (expected {rows:,})")
    invoice, customer, n_lines, total = KNOWN
    row = con.execute(
        "SELECT i.customer_id, COUNT(*), ROUND(SUM(li.quantity * li.unit_price), 2) "
        "FROM invoices i JOIN line_items li USING (invoice_no) WHERE invoice_no = ?", (invoice,)
    ).fetchone()
    con.close()
    good = row == (customer, n_lines, total)
    ok &= good
    print(f"{'✅' if good else '❌'} order {invoice}: customer {row[0]}, {row[1]} lines, total {row[2]}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="build again from the download")
    ap.add_argument("--check", action="store_true", help="only check the existing file")
    ap.add_argument("--out", type=Path, default=DB, help="where to write the database")
    args = ap.parse_args()

    if not args.check and (args.rebuild or not args.out.exists()):
        build(args.out)
    return 0 if check(args.out) else 1


if __name__ == "__main__":
    sys.exit(main())
