"""
vendors/landed_cost.py

What an imported purchase really cost.

A vendor bill for imported goods carries two kinds of line:

  * **goods**  - what the product costs from the supplier;
  * **landed cost** - the letter of credit's own charges: freight, customs
    duty, clearing agent, bank charges, inland haulage.

The landed-cost lines are spread over the goods lines to give each product a
true unit cost. Two ways of spreading are offered, and the owner chooses per
bill:

  * ``value`` - in proportion to what each goods line is worth (the usual
    choice when the goods are of different values);
  * ``qty``   - in proportion to how many units each line brings in.

Nothing here posts anything. It is arithmetic on figures already on the bill;
the journal for the bill is built exactly as before, from the lines and their
accounts.
"""
from decimal import Decimal

from core.money import money_round, to_decimal

BASIS_LABELS = {"value": "By value of the goods", "qty": "By quantity"}


def normalise_basis(basis):
    """Only the two we know; anything else means by value."""
    return basis if basis in BASIS_LABELS else "value"


def split_landed_cost(goods_lines, cost_total, basis="value"):
    """Work out each goods line's share of the charges and its landed unit cost.

    ``goods_lines`` is a list of dicts with at least ``quantity`` and ``total``
    (any other keys are passed straight back). Returns ``(rows, summary)``:

      * ``rows``: the same lines, each with ``allocated``, ``unit_cost`` and
        ``landed_total`` added;
      * ``summary``: ``goods_total``, ``cost_total``, ``grand_total``,
        ``units`` and ``basis``.

    The charges always add up to exactly ``cost_total``: the last line takes
    the rounding remainder, so no paisa is lost.
    """
    basis = normalise_basis(basis)
    charges = money_round(to_decimal(cost_total))
    rows = []
    for line in goods_lines:
        row = dict(line)
        row["quantity"] = to_decimal(line.get("quantity"))
        row["total"] = money_round(to_decimal(line.get("total")))
        row["allocated"] = Decimal("0")
        rows.append(row)

    goods_total = sum((row["total"] for row in rows), Decimal("0"))
    units = sum((row["quantity"] for row in rows), Decimal("0"))

    if rows and charges:
        if basis == "qty":
            weights = [row["quantity"] for row in rows]
            weight_total = units
        else:
            weights = [row["total"] for row in rows]
            weight_total = goods_total
            if weight_total == 0:
                # A goods line with no value cannot carry a share; fall back to
                # quantity, and finally to equal parts.
                weights = [row["quantity"] for row in rows]
                weight_total = units
        if weight_total == 0:
            weights = [Decimal("1") for _ in rows]
            weight_total = Decimal(len(rows))

        given = Decimal("0")
        for index, row in enumerate(rows):
            if index == len(rows) - 1:
                share = charges - given                    # the remainder
            else:
                share = money_round(charges * weights[index] / weight_total)
                given += share
            row["allocated"] = share

    for row in rows:
        row["landed_total"] = money_round(row["total"] + row["allocated"])
        # No quantity means there is no "per unit" to speak of - say nothing
        # rather than pass the whole line off as the cost of one.
        row["unit_cost"] = (money_round(row["landed_total"] / row["quantity"])
                            if row["quantity"] else Decimal("0.00"))

    summary = {
        "basis": basis,
        "basis_label": BASIS_LABELS[basis],
        "goods_total": money_round(goods_total),
        "cost_total": charges,
        "grand_total": money_round(goods_total + charges),
        "units": units,
    }
    return rows, summary


def landed_unit_cost_by_product(goods_lines, cost_total, basis="value"):
    """The landed unit cost for each product on the bill.

    A product may appear on more than one line; its figures are added together
    before the unit cost is worked out, so two boxes of the same thing give one
    sensible cost per box.
    """
    rows, _summary = split_landed_cost(goods_lines, cost_total, basis)
    totals = {}
    for row in rows:
        product_id = row.get("product_id")
        if not product_id:
            continue
        bucket = totals.setdefault(product_id, {"quantity": Decimal("0"), "landed_total": Decimal("0")})
        bucket["quantity"] += row["quantity"]
        bucket["landed_total"] += row["landed_total"]
    # A product whose lines carry no quantity has no cost per unit; leave it
    # out rather than write a figure that cannot be trusted.
    return {
        product_id: money_round(bucket["landed_total"] / bucket["quantity"])
        for product_id, bucket in totals.items() if bucket["quantity"]
    }
