# FreightLens — Customer Implementation Plan

**Customer:** Acme Corporation
**Scope:** Freight audit and spend intelligence across parcel, LTL and truckload
**Carriers at kickoff:** ODFL, XPO, FedEx, UPS, C.H. Robinson, R+L (6)
**Duration:** 10 weeks, kickoff to hypercare exit

This is the implementation side of the project. FreightLens is the product;
this document is how you'd stand it up for a customer. It is written to paste
straight into a Smartsheet project sheet — Task Name / Duration / Predecessors /
Owner / % Complete columns, Gantt view on, dependencies enabled.

---

## Phases

| # | Phase | Weeks | Exit criteria |
|---|---|---|---|
| 1 | Discovery & requirements | 1–2 | Signed-off data inventory and audit-rule matrix |
| 2 | Data mapping & design | 2–3 | Field mapping approved per source system |
| 3 | Integration & configuration | 3–6 | All carriers ingesting; rules configured |
| 4 | UAT | 6–8 | Customer signs off on exception accuracy |
| 5 | Go-live | 8–9 | Production cutover complete |
| 6 | Hypercare | 9–10 | 2 weeks stable; support handoff |

---

## Task list for Smartsheet

Row numbers are the predecessor references. Indent the children under each
phase parent so Smartsheet rolls up duration automatically.

| # | Task Name | Duration | Predecessors | Owner |
|---|---|---|---|---|
| **1** | **Discovery & Requirements** | | | |
| 2 | Kickoff call & project charter | 1d | — | CSA |
| 3 | Document shipment data source (TMS export, fields, cadence) | 3d | 2 | CSA + Customer IT |
| 4 | Collect carrier agreements for all 6 carriers | 5d | 2 | Customer Logistics |
| 5 | Document invoice intake per carrier (CSV / EDI 210 / portal) | 4d | 2 | CSA |
| 6 | Define audit rule matrix & tolerances with customer | 3d | 4 | CSA + Customer Logistics |
| 7 | Define GL coding and cost-allocation rules | 2d | 3 | Customer Finance |
| 8 | **Milestone: Requirements signed off** | 0d | 3,4,5,6,7 | Customer Sponsor |
| **9** | **Data Mapping & Design** | | | |
| 10 | Map TMS fields → `shipments` schema | 3d | 8 | CSA |
| 11 | Map carrier agreements → `contracted_rates` (rate types, weight breaks, discounts) | 5d | 8 | CSA |
| 12 | Map each carrier's invoice format → `carrier_invoices` + line items | 5d | 8 | CSA |
| 13 | Build carrier charge-code crosswalk (carrier label → canonical code) | 3d | 12 | CSA |
| 14 | Confirm shipment↔invoice match key (SCAC + PRO/BOL) | 2d | 10,12 | CSA |
| 15 | **Milestone: Mapping approved** | 0d | 10,11,12,13,14 | Customer Sponsor |
| **16** | **Integration & Configuration** | | | |
| 17 | Stand up environment & load contracted rates | 2d | 15 | Implementation Eng |
| 18 | Build TMS shipment feed (scheduled extract) | 4d | 15 | Customer IT |
| 19 | Build invoice intake per carrier | 8d | 15 | Implementation Eng |
| 20 | Configure 6 audit rules + tolerances | 2d | 17 | CSA |
| 21 | Historical backload — 90 days of invoices | 3d | 18,19 | Implementation Eng |
| 22 | Baseline audit run & variance review with customer | 2d | 20,21 | CSA |
| 23 | Tune tolerances from baseline false positives | 2d | 22 | CSA |
| 24 | **Milestone: Integration complete** | 0d | 18,19,20,21,22,23 | CSA |
| **25** | **User Acceptance Testing** | | | |
| 26 | Write UAT script & test cases per audit rule | 3d | 24 | CSA |
| 27 | Customer UAT execution | 8d | 26 | Customer Logistics |
| 28 | Triage & resolve UAT defects | 5d | 27 | Implementation Eng |
| 29 | Exception-accuracy validation (sample 50 invoices manually) | 3d | 27 | Customer Logistics |
| 30 | Train AP & logistics users on exception workflow | 2d | 28 | CSA |
| 31 | **Milestone: UAT sign-off** | 0d | 27,28,29,30 | Customer Sponsor |
| **32** | **Go-Live** | | | |
| 33 | Cutover plan & rollback criteria | 2d | 31 | CSA |
| 34 | Production cutover | 1d | 33 | Implementation Eng |
| 35 | First production audit run & verification | 1d | 34 | CSA |
| 36 | Enable dispute filing with carriers | 2d | 35 | Customer Logistics |
| 37 | **Milestone: Go-live** | 0d | 34,35,36 | Customer Sponsor |
| **38** | **Hypercare** | | | |
| 39 | Daily exception review with customer (2 weeks) | 10d | 37 | CSA |
| 40 | Recovery tracking & first credit confirmation | 5d | 36 | Customer Logistics |
| 41 | Handoff to support & success review | 2d | 39,40 | CSA |
| 42 | **Milestone: Hypercare exit** | 0d | 39,40,41 | Customer Sponsor |

**Critical path:** 2 → 4 → 6 → 8 → 11 → 15 → 19 → 21 → 22 → 23 → 24 → 26 → 27
→ 28 → 31 → 33 → 34 → 35 → 37 → 39 → 42. Invoice intake (task 19, 8 days) is the
longest single task and the usual source of slip, because it depends on six
different carrier formats arriving on time.

---

## Smartsheet configuration

**Columns:** Task Name, Duration, Start, Finish, Predecessors, Owner (contact
list), % Complete, Status (dropdown: Not Started / In Progress / Blocked /
Complete), Phase (dropdown), Risk Flag (checkbox).

**Views:** Gantt as the default. Grid for bulk editing. Card view grouped by
Owner for standups.

**Automations worth configuring:**
1. When Status changes to **Blocked** → alert the CSA immediately.
2. When a row is a **Milestone** and % Complete hits 100 → notify the Customer
   Sponsor.
3. **Weekly Friday** → send the project sheet summary to the distribution list.
4. When **Finish date** moves later than Baseline → flag Risk and alert the CSA.

**Dashboard widgets:** % complete by phase, milestone status, open risks,
days-to-go-live countdown, and a chart of exception volume and recovered dollars
once the baseline run exists.

---

## RAID log

| Type | Item | Impact | Mitigation / Owner |
|---|---|---|---|
| Risk | Carrier agreements are PDFs with inconsistent rate structures | Mapping (task 11) slips | Start task 4 on day 1; CSA reviews the two most complex contracts first |
| Risk | Carrier invoice formats change without notice | Silent ingestion failures post-go-live | Per-row ingestion error reporting; alert on error-rate spike |
| Risk | Customer IT capacity for the TMS feed | Task 18 blocks integration | Named IT owner at kickoff; CSV fallback if API slips |
| Risk | Tolerances too tight → false positives erode trust | UAT sign-off delayed | Baseline run (task 22) before UAT, then tune |
| Assumption | Shipment records carry the PRO/BOL the carrier bills against | Unmatchable invoices | Validated in task 14; `MISSING_SHIPMENT` rule quantifies the gap |
| Assumption | Accessorials ordered are captured in the TMS | `ACCESSORIAL_NOT_ORDERED` unusable | Confirmed in task 3; if absent, descope that rule |
| Issue | R+L contract expired 31-Dec-2025, no renewal on file | Freight rating off-tariff | Escalated to Customer Logistics; tracked as an exception in the meantime |
| Dependency | Carrier dispute portals / contacts | Recovery can't start | Gathered during task 36 |

---

## Change Request CR-001 — Add DHL eCommerce

**Raised:** Week 3 (during Data Mapping)
**Requested by:** Acme Logistics Director
**Request:** Add DHL eCommerce as a seventh carrier. Acme has shifted
roughly 18% of its parcel volume to DHL and wants it in scope at go-live rather
than in a phase 2.

### Impact analysis

| Area | Impact | Effort |
|---|---|---|
| Carrier agreement mapping | New zone-based parcel rate card — zone × weight matrix, not the per-lb structure the other parcel carriers use. Needs a new rate representation or a zone→state translation. | +3d (task 11) |
| Invoice intake | DHL sends EDI 210, not CSV. First EDI carrier in scope — new parser, new transmission setup, new test cycle with DHL. | +6d (task 19) |
| Charge-code crosswalk | ~9 DHL-specific accessorial codes to map (e.g. `DDU`, `RDI`, `OVR`). | +1d (task 13) |
| Shipment feed | TMS already records DHL shipments — no change. | 0d |
| Audit rules | Same 6 rules apply. No new rule logic. | 0d |
| UAT | DHL test cases added; parcel test set re-run. | +2d (tasks 26, 27) |
| Historical backload | 90 days of DHL invoices to source and load. | +1d (task 21) |

**Net schedule impact: +9 days on the critical path** (mapping +3, intake +6
run partly in parallel; UAT +2 absorbed by float). Go-live moves from week 9 to
**week 10**, hypercare exit to week 12.

### Options presented to the sponsor

1. **Accept the slip.** Go-live week 10, everything in scope. *Recommended* —
   18% of parcel spend unaudited at go-live is a larger cost than one week.
2. **Hold the date, phase DHL.** Go-live week 9 with 6 carriers; DHL follows in
   a 3-week phase 2. Keeps the date, leaves DHL spend unaudited for ~5 weeks.
3. **Hold the date, CSV interim.** Go-live week 9 with DHL on a manual CSV
   export instead of EDI, convert to EDI during hypercare. Keeps the date and
   the scope, but adds a manual step the customer has to own for a month.

### Decision

**Option 1 accepted** by the Customer Sponsor in week 3.

### Smartsheet actions taken

1. Set a **baseline** before editing, so the slip is visible as a variance
   rather than quietly becoming the new plan.
2. Added child tasks under Integration: *Map DHL zone rate card*, *Build DHL EDI
   210 intake*, *DHL charge-code crosswalk*, *DHL UAT cases*.
3. Updated predecessors so DHL EDI intake gates the Integration Complete
   milestone (task 24).
4. Pushed the go-live and hypercare milestone dates; Gantt now shows the
   baseline bar against the revised bar.
5. Logged CR-001 in the RAID log with the decision and the sponsor's name.
6. Notified stakeholders via the existing automation rather than a separate
   email, so the change is recorded on the sheet.

**Why this is the interesting part of the project.** The audit engine was the
easy half — the rules are arithmetic. The hard half is that a customer changed
scope 30% of the way through an implementation, and the job was to quantify the
impact honestly, give the sponsor real options with real trade-offs, and keep
the plan truthful about what slipped and why.
