/*
Read-only DBA diagnostic for the COI source failure:
dbo.V_ESCM_ORDER_COLORSIZE_SALES reports SQL Server error 468 when joined
columns use incompatible collations. This script does not alter the view.
*/
SET NOCOUNT ON;

SELECT
    s.name AS schema_name,
    v.name AS view_name,
    m.definition AS view_definition
FROM sys.views AS v
JOIN sys.schemas AS s ON s.schema_id = v.schema_id
JOIN sys.sql_modules AS m ON m.object_id = v.object_id
WHERE s.name = N'dbo'
  AND v.name = N'V_ESCM_ORDER_COLORSIZE_SALES';

SELECT
    OBJECT_SCHEMA_NAME(d.referencing_id) AS referencing_schema,
    OBJECT_NAME(d.referencing_id) AS referencing_object,
    d.referenced_schema_name,
    d.referenced_entity_name
FROM sys.sql_expression_dependencies AS d
WHERE d.referencing_id = OBJECT_ID(N'dbo.V_ESCM_ORDER_COLORSIZE_SALES');

/* Review the result against the join columns in the view definition. */
SELECT
    s.name AS schema_name,
    o.name AS object_name,
    c.name AS column_name,
    t.name AS data_type,
    c.collation_name
FROM sys.columns AS c
JOIN sys.objects AS o ON o.object_id = c.object_id
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
JOIN sys.types AS t ON t.user_type_id = c.user_type_id
WHERE c.collation_name IS NOT NULL
  AND o.object_id IN (
      SELECT d.referenced_id
      FROM sys.sql_expression_dependencies AS d
      WHERE d.referencing_id = OBJECT_ID(N'dbo.V_ESCM_ORDER_COLORSIZE_SALES')
        AND d.referenced_id IS NOT NULL
  )
ORDER BY s.name, o.name, c.name;
