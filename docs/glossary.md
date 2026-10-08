# Freight glossary

Every term is tied to where it lives in this codebase, so the business
vocabulary and the schema stay the same conversation.

---

## The parties

**Shipper** — the company paying for freight to move. The customer. In this
project the shipper is "Acme Corporation," and every table is written from the
shipper's point of view: `shipments` is *their* record, `carrier_invoices` is
what *someone else* sent them.

**Carrier** — the company that physically moves the freight and sends the bill
(ODFL, XPO, FedEx, UPS, C.H. Robinson, R+L here). Identified by **SCAC**
(Standard Carrier Alpha Code), a 2–4 letter industry identifier —
`shipments.carrier_scac`. SCAC is the join key between all three data sources;
when it is wrong or missing, nothing reconciles.

**3PL / broker** — a middleman who arranges freight on other carriers'
equipment. C.H. Robinson (`CHRW`) is one. Matters for audit because the entity
on the invoice may not be the entity that drove the truck.

---

## The systems data comes from

**TMS (Transportation Management System)** — where the shipper plans and
records shipments: lanes, carriers chosen, weights, service levels, accessorials
ordered. **This is the source of `shipments.csv`.** It is the "what we asked
for" side of the audit.

**ERP (Enterprise Resource Planning)** — the system of record for finance
(SAP, Oracle, NetSuite). Audited invoices flow *out* to the ERP for payment
and GL posting. Not implemented here — it's the downstream integration you'd
scope in an implementation project.

**EDI 210** — the standard electronic freight-invoice format carriers transmit.
This project ingests CSV instead, which is the realistic fallback: most
implementations start with a CSV or flat-file extract because EDI onboarding per
carrier takes weeks.

---

## Modes

How freight moves. Drives which rate table applies, so it's on every shipment
and every rate row (`shipments.mode`, `contracted_rates.mode`).

| Mode | Means | Typical weight | Priced by |
|---|---|---|---|
| **Parcel** | Small packages, UPS/FedEx ground or express | under ~150 lb | per pound |
| **LTL** (Less-Than-Truckload) | Palletized freight sharing a trailer | ~150–15,000 lb | per hundredweight, by weight break |
| **TL / FTL** (Truckload) | A dedicated trailer | 15,000–45,000 lb | per mile |
| **Intermodal** | Rail + truck combination | truckload volumes | per container/mile |

In the sample data: 6 parcel, 11 LTL, 2 truckload shipments.

---

## Pricing

**Freight invoice** — the carrier's bill. One header plus one or more charge
lines: linehaul, fuel, and any accessorials. Here: `carrier_invoices` +
`invoice_line_items`. The header total and the sum of the lines can disagree,
which is itself a thing worth auditing.

**Linehaul** — the base transportation charge, before fuel and accessorials.
Charge code `FREIGHT` in this project.

**Contracted rate** — the negotiated price in the carrier agreement. The thing
you audit *against*. Here: `contracted_rates`, which stores a `rate_type`
(`per_cwt`, `per_lb`, `per_mile`, `flat`, `pct_of_linehaul`), a `base_rate`, and
the modifiers that make freight pricing non-obvious:

- **CWT (hundredweight)** — 100 lb. LTL is quoted "per CWT," so a 2,500 lb
  shipment at $32.00/cwt is 25 × $32.00 = $800.
- **Weight break** — rates step down as shipments get heavier
  (`min_weight_lb` / `max_weight_lb`). A 1,200 lb shipment and a 3,000 lb
  shipment on the same lane use different rates.
- **Discount** — a percentage off the published tariff, the main thing
  negotiated (`discount_pct`). Our XPO contract has a 35% national discount and
  a 40% discount on CA→TX specifically.
- **Minimum charge** — the floor regardless of weight (`min_charge`). A 250 lb
  LTL shipment that computes to $95 still bills at the $135 minimum.
- **Lane** — an origin/destination pair (`CA→TX`). Rates can be national
  (`*→*` here) or lane-specific. Lane-specific wins.
- **Effective dates** — contracts expire (`effective_date` /
  `expiration_date`). Freight moving on a lapsed contract is a real and
  expensive problem; our R+L contract is deliberately expired.

**Accessorial** — a charge for anything beyond moving freight point to point:
liftgate, residential delivery, inside delivery, detention, limited access,
appointment, reweigh. Usually a flat fee per occurrence. **Accessorials are
where overbilling concentrates**, because they're assessed by the driver in the
field, not by the rating engine. Here: canonical charge codes in
`models/enums.py`, what the shipper actually ordered in
`shipments.accessorials`, what the carrier billed in `invoice_line_items`.

**Fuel surcharge (FSC)** — a percentage of linehaul that floats with diesel
prices, published weekly. Typically 15–30%. Because it is a *percentage of
linehaul*, an inflated linehaul inflates the fuel too — so a rating error costs
you twice. This project deliberately prices expected fuel off the **contracted**
linehaul, not the billed one, so the second dollar gets caught.

**Billable weight vs. actual weight** — carriers rate the greater of actual
weight and dimensional weight, and may **reweigh** in the terminal. A reweigh
that disagrees with the bill of lading is a common dispute.
`shipments.billable_weight_lb` overrides `weight_lb` when present.

**Freight class** — an NMFC 50–500 classification based on density and
handling; lower class is cheaper. Captured as `shipments.freight_class`.
Reclassification disputes are a whole audit category this project doesn't cover.

---

## The audit

**Freight audit** — comparing each carrier invoice against the shipment record,
the carrier agreement, and the shipper's business rules, and flagging what
doesn't reconcile. Three inputs, one process:

```
shipments  +  contracted_rates  +  carrier_invoices  →  audit engine  →  exceptions
(what we    (what it should      (what we were
 shipped)    cost)                billed)
```

**Pre-audit vs. post-audit** — pre-audit catches errors before payment
(cheaper, needs fast turnaround); post-audit finds them after and claims money
back. This project models pre-audit: invoices land in `received`, the audit
moves them to `audited` or `disputed` before anyone pays.

**Exception** — one discrepancy on one invoice. Here: a row in
`audit_exceptions`, carrying what was billed, what was expected, the variance,
the rule that caught it, and a recommended action. **Variance is positive when
the carrier overbilled** — that's the recoverable amount.

**Exception rate** — share of invoices with at least one exception. 37% in this
sample data, which is high because the fixtures are engineered so every rule
fires; a real program runs lower.

**Recovery** — actually getting the money back: filing the dispute, tracking
the credit, confirming it landed. Modeled here as the exception status
workflow: `open → disputed → recovered` (or `dismissed`). The distinction
matters commercially — detecting $1,329 is not the same as collecting it.

**Duplicate billing** — the same shipment billed twice, often because a carrier
rebills under a new invoice number instead of issuing a credit. Caught by
`DUPLICATE_INVOICE`.

**Rate noncompliance** — the carrier applied the wrong tariff: ignored a
discount, used a national rate instead of a lane rate, or rated the wrong weight
break. The flagship rule, `RATE_MISMATCH`. The largest finding in this dataset
is exactly this: XPO billed $900 on a lane contracted at $480.

---

## Downstream of the audit

**GL coding** — tagging each freight charge with the general-ledger account it
belongs to, so finance can book it. `shipments.cost_center` is the hook; the
mapping rules themselves would be a configuration exercise in a real
implementation.

**Cost allocation** — distributing freight cost to the business unit, customer,
product line or project that caused it. `shipments.customer` and
`cost_center` are the dimensions you'd allocate across. This is often the
feature a CFO cares about more than the audit savings, because it changes how
the business prices its own products.

**Carrier scorecard** — tracking on-time performance and billing accuracy per
carrier to use in the next rate negotiation. `shipments.promised_date` vs.
`delivery_date` supports it; no rule consumes it in this build.
