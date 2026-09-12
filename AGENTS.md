# Keep researchOS understandable

- Keep Python, pandas, SQLite, and Jupyter. Prefer small plain functions with explicit arguments; do not add a service, ORM, scheduler, or adapter hierarchy.
- Keep state-specific extraction and metric definitions in state modules. Parsers accept retained bytes/tables and perform no network access or writes.
- Document DataFrame columns, units (usually USD), native metric names, and period meaning. Never substitute adjusted, taxable, gross revenue, or net proceeds for one another.
- Missing is not zero. Preserve reported zeros and negatives. Reject malformed dates, incomplete required components, and duplicate observation keys. Test the actual failure with a retained fixture or a small alteration in memory.
- Keep filters, intermediate tables, validation differences, charts, and interpretation visible in notebooks. Extract only repeated or difficult operations into named functions.
- Analysis is read-only by default. Downloads, writes, and historical replays require an explicit destination and intent. Preserve retained bytes and conflicting versions; never resolve revisions by newest-capture wins.
- Preserve existing user edits. Never repin approval hashes or imply renewed human approval to make a notebook pass. Show why an approval is blocked.
- Make small evidence-driven changes. Run affected tests, then the offline suite and active notebook check. Use temporary storage for collector tests. Do not fetch reports or rewrite existing databases as part of tests.

Validation from the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
```
