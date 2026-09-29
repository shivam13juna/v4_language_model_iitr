# Data

Every file the service and the notebook read, where it came from, how to rebuild it, and
what it can and cannot support.

| file | what | origin | rebuild |
|---|---|---|---|
| `online_retail.db` | the shop's orders | **real** — UCI Online Retail | `python prepare_data.py` |
| `evals/smoke.jsonl` | 8 smoke cases, the check `release.sh` runs before a deploy | generated from SQL + written by hand | ships with the folder |
| `results/*.json` | saved runs of the checks that need Docker, the tunnel or the instance | produced by the notebook and `release.sh` | re-run the cell with `RUN_LIVE = True` |

---

## `online_retail.db` — real

UCI Machine Learning Repository, *Online Retail* (Chen, 2012), dataset 352:
541,909 order lines from a UK online gift retailer, 2010-12-01 to 2011-12-09. CC BY 4.0.
<https://archive.ics.uci.edu/dataset/352/online+retail>

`prepare_data.py` downloads the spreadsheet and normalises it into three tables:

```
invoices    invoice_no · customer_id · invoice_ts · country · is_cancelled      25,900 rows
line_items  invoice_no · stock_code · quantity · unit_price                    541,909 rows
products    stock_code · description                                             3,958 rows
```

Rebuilt from the download on 2026-09-27, the result was **byte-identical** to the file in this
folder (same SHA-1).

**What the data does not contain, and the service must not claim:**

- **No shipping, dispatch or delivery information of any kind.** There is no status, no
  carrier, no date shipped. "Where is my order?" can be answered only with when it was placed,
  what is on it, and whether it was cancelled.
- **`country` is the country on the customer's account** — where the customer is registered.
  It is not a destination. An earlier version of Porter's lookup tool said "shipped to
  {country}", which stated a fact the data does not hold. The tool now says the desk has no
  shipping information, and the evaluation cases check the reply does not invent any.
- Invoice numbers beginning with `C` are cancellations (`is_cancelled = 1`).
- Customer ids are the dataset's own anonymous numbers. Customer 12381 (six orders, account
  country Norway) is used throughout because the notebook needs one customer to follow.

## `evals/smoke.jsonl` — the deploy check

Eight cases that `release.sh` runs inside the candidate image before anything ships.

- **Five order questions come from SQL**, so the expected total or placed date is read from
  the same database the tools read and cannot drift from it. The lookup case passes only if
  the reply gives the order and its placed date, admits it has no tracking information, and
  does not claim a shipment.
- **Three behaviour cases are written by hand:** somebody else's order, a return on a
  cancelled order, and a question that is not about orders at all.
- Every expectation in a case must hold; a reply that gets half of them right fails.
