# Workstation Queues

## Framing Queue

The Framing workstation queue lists orders that require panel cutting. Each row
in `/work/stations/framing` shows:

| Column | Meaning |
| --- | --- |
| Order, Customer, Item | Order metadata with a link to the order detail when permitted. The Item column will show the SKU/name when available or fall back to the gate item number. |
| Panels Needed | Total panels to cut (production quantity × panel count). |
| Panel Material | Panel insert color or half-panel color from the gate details. |
| Panel Length | Calculated cutting length based on the gate height and the framing offset. |

### Panel Length Offset (Admin only)

Administrators can set a framing offset directly on the framing queue page. The
value is stored using the existing framing offset mechanism and is applied to
all panel length calculations on that page.

1. Navigate to `/work/stations/framing`.
2. As an admin, enter the desired offset in **Panel Length Offset** and click
   **Save Offset**.
3. The table updates to show `Panel Length = total_gate_height - offset`, rounded
   to two decimals. Non-admin users will not see the offset form.

If either the total gate height or the offset is missing/invalid, the Panel
Length column shows "—" for that row.
