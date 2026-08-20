# COI sales-view collation remediation

The COI application reads `dbo.V_ESCM_ORDER_COLORSIZE_SALES` for JO, colour, and quantity. SQL Server error 468 means the view compares text values whose collations differ (observed: `Chinese_PRC_CS_AS` and `Chinese_PRC_CI_AS`).

Run `scripts/diagnose_sales_view_collation.sql` as a DBA first. It is read-only and returns the view definition, dependencies, and collations on dependency columns.

After reviewing the exact join that fails, update that comparison inside the view so both operands use the database-approved collation. Example only:

```sql
ON left_alias.text_key COLLATE Chinese_PRC_CI_AS
 = right_alias.text_key COLLATE Chinese_PRC_CI_AS
```

Prefer a consistent collation on both operands; do not change base-table collations only to repair this view. Validate the altered view with a representative GO before deployment. The application classifies this condition as `COLLATION_CONFLICT` and continues to serve last-known-good cache rows.
