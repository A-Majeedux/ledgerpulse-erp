"""
core/stock.py

What came in, what went out, and what is left.

`products.on_hand` used to be a number a person typed on the product form and
nothing ever moved it: buying ten bags left the stock where it was, and selling
four left it there too. This module is the ledger behind that number.

Every change to a product's quantity is a row in `stock_movements`, with the
paper that caused it and what a unit was worth at the time. `products.on_hand`
is then the sum of those rows - never a figure typed in - and a product's stock
page can show how the number was reached.

Three rules worth knowing:

  * **Opening stock is a movement too.** A product whose On hand was typed in
    before this existed gets one `Opening` row for that figure the first time
    anything else moves, so the history adds up from the beginning.
  * **A draft moves nothing.** Movements are written when a document is posted
    and removed when it is reset to a draft; a bill that is still a draft has no
    effect on stock or on the ledger.
  * **Stock in is valued at the landed cost.** Goods bought on a bill that also
    carries freight, duty and clearing are worth what they finally cost, not
    what the supplier's line said.
"""

from decimal import Decimal

from core.money import money_round, to_decimal


def _rows(cursor, organization_id, product_id):
    """The movements of one product, opening stock first and then by date."""
    cursor.execute("""
        SELECT movement_date, quantity, COALESCE(unit_cost, 0), COALESCE(value, 0),
               source_type, source_id, reference
        FROM stock_movements
        WHERE organization_id = %s AND product_id = %s
        ORDER BY (source_type <> 'Opening'), movement_date, stock_movement_id
    """, (organization_id, product_id))
    return cursor.fetchall()


def ensure_opening(cursor, organization_id, product_id):
    """Give a product that was typed in with stock on it an Opening movement.

    Only when it has no movements yet - after that its history is its history.
    The figure is what the product form says today; it is taken away from the
    product's own number and handed to the movements, so nothing is counted
    twice.
    """
    cursor.execute("SELECT COUNT(*) FROM stock_movements WHERE organization_id = %s AND product_id = %s",
                   (organization_id, product_id))
    if cursor.fetchone()[0]:
        return

    cursor.execute("SELECT COALESCE(on_hand, 0), COALESCE(last_landed_cost, 0) FROM products "
                   "WHERE product_id = %s AND organization_id = %s", (product_id, organization_id))
    row = cursor.fetchone()
    if not row:
        return
    quantity, unit_cost = to_decimal(row[0]), to_decimal(row[1])
    if not quantity:
        return
    cursor.execute("""
        INSERT INTO stock_movements
            (organization_id, product_id, movement_date, quantity, unit_cost, value,
             source_type, source_id, reference)
        VALUES (%s, %s, CURRENT_DATE, %s, %s, %s, 'Opening', NULL, 'Opening stock')
    """, (organization_id, product_id, quantity, unit_cost, money_round(quantity * unit_cost)))


def record(cursor, organization_id, product_id, quantity, unit_cost=Decimal("0"), *,
           source_type, source_id, reference, movement_date):
    """Write one movement and bring the product's On hand up to date."""
    quantity = to_decimal(quantity)
    if not quantity:
        return None
    ensure_opening(cursor, organization_id, product_id)
    unit_cost = to_decimal(unit_cost)
    cursor.execute("""
        INSERT INTO stock_movements
            (organization_id, product_id, movement_date, quantity, unit_cost, value,
             source_type, source_id, reference)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING stock_movement_id
    """, (organization_id, product_id, movement_date, quantity, unit_cost,
          money_round(quantity * unit_cost), source_type, source_id, reference))
    movement_id = cursor.fetchone()[0]
    refresh_on_hand(cursor, organization_id, product_id)
    return movement_id


def clear(cursor, organization_id, source_type, source_id):
    """Remove the movements a document wrote (it is a draft again, or gone).

    Returns the products that were touched, so their On hand can be brought up
    to date again by whoever called this.
    """
    cursor.execute("""
        SELECT DISTINCT product_id FROM stock_movements
        WHERE organization_id = %s AND source_type = %s AND source_id = %s
    """, (organization_id, source_type, source_id))
    product_ids = [row[0] for row in cursor.fetchall()]
    cursor.execute("DELETE FROM stock_movements "
                   "WHERE organization_id = %s AND source_type = %s AND source_id = %s",
                   (organization_id, source_type, source_id))
    return product_ids


def refresh_on_hand(cursor, organization_id, product_id):
    """On hand is the sum of the movements, written back onto the product."""
    cursor.execute("""
        UPDATE products
        SET on_hand = COALESCE((SELECT SUM(quantity) FROM stock_movements
                                WHERE product_id = %s AND organization_id = %s), 0)
        WHERE product_id = %s AND organization_id = %s
    """, (product_id, organization_id, product_id, organization_id))


def refresh_all(cursor, organization_id, product_ids):
    for product_id in dict.fromkeys(product_ids):
        refresh_on_hand(cursor, organization_id, product_id)


def average_cost(cursor, organization_id, product_id):
    """What a unit of this product has cost us on average, for what goes out.

    Simple weighted average over everything that came IN: total value of the
    in-movements divided by the quantity they brought. A product that has never
    been bought has no cost yet - zero, not a guess.

    Opening stock is turned into a movement first if it has not been already:
    a product whose stock was typed on its form still has a cost - the last
    landed cost on the product - and goods sold out of it are not free.
    """
    ensure_opening(cursor, organization_id, product_id)
    cursor.execute("""
        SELECT COALESCE(SUM(quantity), 0), COALESCE(SUM(value), 0)
        FROM stock_movements
        WHERE organization_id = %s AND product_id = %s AND quantity > 0
    """, (organization_id, product_id))
    quantity, value = cursor.fetchone()
    quantity, value = to_decimal(quantity), to_decimal(value)
    if not quantity:
        return Decimal("0")
    return money_round(value / quantity)


def balance(cursor, organization_id, product_id):
    """(quantity, value) of what is on hand right now."""
    rows = _rows(cursor, organization_id, product_id)
    quantity = sum((to_decimal(row[1]) for row in rows), Decimal("0"))
    value = sum((to_decimal(row[3]) for row in rows), Decimal("0"))
    return quantity, value


def history(cursor, organization_id, product_id):
    """Every movement with the balance after it - what the stock page prints."""
    rows = _rows(cursor, organization_id, product_id)
    running, value_running, out = Decimal("0"), Decimal("0"), []
    for movement_date, quantity, unit_cost, value, source_type, source_id, reference in rows:
        quantity, unit_cost, value = to_decimal(quantity), to_decimal(unit_cost), to_decimal(value)
        running += quantity
        value_running += value
        out.append({
            "date": movement_date,
            "quantity": quantity,
            "unit_cost": unit_cost,
            "value": value,
            "balance": running,
            "balance_value": value_running,
            "source_type": source_type,
            "source_id": source_id,
            "reference": reference or "",
        })
    return out


# ---------------------------------------------------------------------------
# The two documents that move stock
#
# A purchase brings goods in at their landed cost; a sale takes them out at what
# they have cost us on average. Both are called after their document is posted,
# and both can be undone when it is reset to a draft.
# ---------------------------------------------------------------------------

def goods_in_from_bill(cursor, organization_id, vendor_bill_id, reference, movement_date,
                       landed_unit_costs):
    """A posted purchase bill: each product line in, at its landed unit cost.

    `landed_unit_costs` is {product_id: cost per unit} - the goods' own prices
    plus their share of the LC charges - which is what the goods are really
    worth. A line with no product is a service or an expense and moves no stock.
    """
    undo_document(cursor, organization_id, "VendorBill", vendor_bill_id)
    cursor.execute("""
        SELECT product_id, quantity, COALESCE(line_kind, 'Goods')
        FROM vendor_bill_lines
        WHERE vendor_bill_id = %s AND product_id IS NOT NULL
        ORDER BY line_order
    """, (vendor_bill_id,))
    touched = []
    for product_id, quantity, kind in cursor.fetchall():
        if kind != "Goods":
            continue        # a landed-cost charge or an asset is not stock of this product
        unit_cost = to_decimal(landed_unit_costs.get(product_id) or 0)
        record(cursor, organization_id, product_id, to_decimal(quantity or 0), unit_cost,
               source_type="VendorBill", source_id=vendor_bill_id,
               reference=reference, movement_date=movement_date)
        touched.append(product_id)
    return touched


def goods_out_from_invoice(cursor, organization_id, sales_invoice_id, reference, movement_date):
    """A posted sales invoice: each product line out, at the average cost."""
    undo_document(cursor, organization_id, "SalesInvoice", sales_invoice_id)
    cursor.execute("""
        SELECT product_id, quantity FROM sales_invoice_lines
        WHERE sales_invoice_id = %s AND product_id IS NOT NULL ORDER BY line_order
    """, (sales_invoice_id,))
    lines = cursor.fetchall()
    touched = []
    for product_id, quantity in lines:
        unit_cost = average_cost(cursor, organization_id, product_id)
        record(cursor, organization_id, product_id, -to_decimal(quantity or 0), unit_cost,
               source_type="SalesInvoice", source_id=sales_invoice_id,
               reference=reference, movement_date=movement_date)
        touched.append(product_id)
    return touched


def undo_document(cursor, organization_id, source_type, source_id):
    """Take a document's movements away (it went back to a draft) and resync."""
    touched = clear(cursor, organization_id, source_type, source_id)
    refresh_all(cursor, organization_id, touched)
    return touched
