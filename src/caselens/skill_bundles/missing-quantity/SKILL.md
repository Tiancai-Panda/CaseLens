---
name: missing-quantity
description: Reconcile order quantities, receipt photos and split shipments for possible shortages.
---

# missing-quantity

Read references/evidence-checklist.md and references/split-shipment.md as needed. Establish ordered quantity from order evidence, received quantity from a complete set photo, and pending quantities from shipment records. Read evidence and use image transforms to clarify labels or counts; never invent obscured items. Call check_quantity with verified evidence IDs. Self-reported counts do not prove shortage. Unknown package status needs evidence; in-transit items prevent premature conclusions. Preserve conflicts. The helper reconciles evidence only; Policy, current permissions and human approval control actions.
