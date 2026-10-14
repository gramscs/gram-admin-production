Project-Wide AI Coding Instructions
Project
Framework: Flask
ORM: Flask-SQLAlchemy
Migrations: Custom SQLAlchemy migration scripts in `migrations/`

Adapt these details to the actual project configuration.

Core Rules
Make the smallest complete change that correctly satisfies the request.
Never refactor unrelated code or reformat unrelated files.
Inspect only relevant files and directly related dependencies.
Reuse findings from the current conversation. Do not repeat completed investigations.
Preserve existing application behavior and public API contracts unless explicitly instructed otherwise.
Never skip necessary changes merely to save tokens.
Follow existing naming conventions, architecture, and error-handling patterns.
Preserve database data and provide reversible migrations where appropriate.
Run targeted tests and checks rather than the full test suite by default.
Keep plans, progress reports, and final responses concise.
Database Changes

For table or column changes, follow the workflow in workflows/database-change.md if supported by the current coding tool.

Do not regenerate an impact analysis if one has already been completed in the current task.

Before modifying files, determine whether the request concerns:

The physical database schema
Python model attributes
API or form field names
An end-to-end rename

Change only the layers required by the request.

Never edit historical migrations that have already been applied to shared or deployed databases unless explicitly requested and safe to do so.