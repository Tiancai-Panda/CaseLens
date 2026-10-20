# Split shipments

Ordered 3; received 2; 1 in transit: return pending_shipment, not confirmed shortage. Unknown pending quantity returns missing_evidence. Received plus pending exceeding ordered returns conflict. Resolve shipment status and recount all packages before applying policy. A tracking claim without a stored evidence source cannot establish a pending package.
