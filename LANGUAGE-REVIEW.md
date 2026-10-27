# Language switching review

The interface now uses one translation pass with longest matching phrases. Translation covers pages, dialogs, report headings, recovery, prompts, errors, toast updates, placeholders and accessibility attributes. Arabic source text is retained for switching back; reused nodes refresh their source when application code changes their content. English uses LTR, Arabic uses RTL, and the preference persists in browser storage.

Employee/company names and other business data remain in their entered language. Free-text request descriptions, notes and comments are excluded from translation. No database schema or account changes are required.

Validation:
- 62 Python tests passed across HR, workflow, demo, production database protection and Bootstrap.
- `test_language_ui.cjs` checks English pages and dialogs, reverse switching, saved preference, live toast replacement, recovery, attributes and preservation of business data.
- `test_hr_ui.cjs` passed, covering requests, reports, XLSX, PDF rendering and mobile layout.

All browser tests use disposable local demo databases. No Railway deployment or production data changes were performed for this fix.
