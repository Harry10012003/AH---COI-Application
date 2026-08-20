# Runtime assets

`templates/` contains the Excel workbooks loaded by `backend/sources.py`.
`samples/` contains the reference COI workbook used by validation and tests.
`reference/` is reserved for retained business reference files that are not
loaded by the web application.

Do not place passwords, exports, logs, or SQLite databases in this folder.
