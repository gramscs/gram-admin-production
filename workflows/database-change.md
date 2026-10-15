Database Change Workflow — Flask + Supabase
Core Architecture Rules (Non-Negotiable)

Supabase PostgreSQL is the application's sole persistent database and source of truth.

These rules take precedence over convenience, implementation shortcuts, and default coding-agent behavior.

Never create, initialize, or switch to SQLite to solve a database problem.
Never introduce another database or persistent storage system as a fallback.
Never create local database files such as .db or .sqlite files for application data.
Never replace a failed Supabase operation with an in-memory database, local file, mock database, or alternative persistence layer to make the task appear successful.
Use the existing Supabase PostgreSQL connection, database configuration, and project conventions.
Do not silently change database providers, connection strings, or persistence architecture.
When a database operation fails, investigate and fix the actual cause.
Never claim a change has been applied to Supabase unless the result has been verified.
Never drop, reset, truncate, or recreate remote tables or databases without explicit authorization.
Do not expose database credentials, service-role keys, passwords, or connection strings containing secrets in source code, logs, or final responses.

A local SQL migration file committed to version control is allowed. A local database containing application data is not.

Stage 1: Understand the Request

Determine the exact requested change:

Rename a table or column
Add or remove a column, table, index, constraint, or relationship
Modify column types, defaults, or nullability
Update SQLAlchemy models or queries
Update application-level field names
Fix a database error
Change data or query behavior

Distinguish between:

The physical PostgreSQL table or column name
The Python/SQLAlchemy model attribute
Form field names
API request and response fields
Frontend property names

Do not rename every layer unless the requested behavior requires it.

If the request is sufficiently clear, proceed without unnecessary questions.

Stage 2: Inspect the Existing Database Architecture

Inspect only the relevant files and configuration.

Check applicable components:

Flask application configuration
Supabase client initialization or PostgreSQL connection setup
SQLAlchemy models and database session configuration
Alembic or Flask-Migrate configuration
Existing migration files and migration history
Database access helpers and repositories
Routes, services, and queries
Relevant environment-variable names
Tests and deployment configuration

Determine how the application currently connects to Supabase before changing database code.

Reuse any completed impact analysis and previously identified file list. Do not repeatedly inspect the entire repository.

Connection requirements
Use the project's existing database access mechanism wherever appropriate.
Keep credentials in environment variables or the project's established secret-management mechanism.
Never invent credentials or silently switch to a different database endpoint.
Do not replace PostgreSQL with SQLite because PostgreSQL is unavailable locally or a migration fails.

If the current connection cannot be established, diagnose the connection, credentials, permissions, configuration, or network issue. Do not introduce a fallback database.

Stage 3: Identify the Correct Change Strategy
Schema changes

For changes to tables, columns, indexes, constraints, or relationships:

Follow the existing migration strategy.
Use PostgreSQL-compatible operations.
Preserve existing data wherever possible.
Account for foreign keys, indexes, constraints, defaults, and dependencies.
Consider whether deployed application code depends on the existing schema.
Application code changes

For changes to models, queries, routes, forms, templates, or API fields:

Update all necessary references.
Preserve existing behavior outside the requested scope.
Keep database names and Python attribute names separate when appropriate.
Avoid unnecessary frontend or API contract changes.
Data changes

For updates to existing records:

Use the established Supabase/PostgreSQL connection.
Scope modifications with appropriate conditions.
Verify the affected records or results when feasible.
Never modify unrelated records as a workaround.
Stage 4: Implement the Change

Make the smallest complete set of changes needed.

Modify only necessary files.
Follow existing project conventions.
Reuse existing database helpers and connection infrastructure.
Avoid unrelated refactoring, reformatting, and architectural changes.
Do not create a new database abstraction or persistence mechanism without a genuine requirement.
Do not duplicate database clients or connection configurations unnecessarily.

For a column or table rename, update the model and dependent application references as required.

Do not modify unrelated historical migrations that have already been applied to shared or deployed environments.

Stage 5: Migration and Remote Execution Rules

Prefer version-controlled, reviewable migration files for schema changes.

When using Alembic, Flask-Migrate, or the Supabase CLI:

Inspect the existing migration conventions.
Create a migration appropriate for PostgreSQL.
Review the migration for data-loss risks and unintended operations.
Apply it to the intended Supabase environment using the existing authorized workflow.
Verify that the remote operation succeeded.
Ensure the migration history and actual remote schema are consistent.

Do not assume generating a migration means it has been applied.

Do not assume a successful command means the intended schema change occurred; verify the outcome where feasible.

Direct SQL

Direct SQL against the hosted Supabase database is acceptable when supported by the project's existing authorized tools and appropriate for the task.

For significant schema changes, preserve an auditable migration history according to the project's conventions.

Production safety
Never run destructive operations against production without explicit authorization.
Seek confirmation before dropping tables or columns, deleting significant data, resetting a database, or performing potentially irreversible transformations.
Prefer reversible changes and appropriate safeguards.
Do not bypass access controls or row-level security to force a change through.
Stage 6: Handle Failures Without Changing Architecture

If an operation fails:

Read the actual error message.
Identify the failure category: connection, authentication, permissions, SQL syntax, schema mismatch, migration history, constraint violation, or application logic.
Inspect only the relevant code, configuration, schema, or migration.
Fix the underlying problem using the existing Supabase/PostgreSQL architecture.
Retry only when safe and appropriate.
Verify the outcome.

Never respond to a complicated failure by:

Creating a SQLite database
Switching the SQLAlchemy URI to SQLite
Creating a local database as a temporary substitute
Introducing another database provider
Replacing real database operations with mock or in-memory persistence in production code
Silently swallowing database exceptions
Claiming success without performing the requested operation

If the required Supabase connection, credentials, permissions, or tools are unavailable, explain the blocker and provide the appropriate next step. Do not simulate a successful remote change.

Stage 7: Verification

Perform checks proportionate to the change:

Confirm that the application still uses the intended Supabase/PostgreSQL configuration.
Search for accidental SQLite configuration or newly introduced database files.
Verify relevant model and schema consistency.
Check for remaining references to old identifiers within the affected application scope.
Run relevant tests and checks.
Verify remote changes when authorized access is available.
Inspect the final diff for unrelated modifications.

Do not run expensive or destructive checks unnecessarily.

Tests may use mocks or isolated test fixtures where appropriate, but do not introduce a local persistent database into the application architecture. If an existing test setup uses SQLite, do not silently change production configuration or misrepresent the test environment as Supabase.

Do not claim that a remote change or test succeeded unless it was actually verified.

Stage 8: Final Response

Report concisely:

What changed
Which important files changed
Whether a migration was created
Whether the migration or data change was actually applied to Supabase
What verification was performed
Any remaining blocker or risk

Clearly distinguish between:

Code changes completed
Migration generated
Remote migration applied
Remote change verified

Do not repeat the entire investigation or provide unnecessary implementation narration.

Token and Scope Efficiency
Reuse completed impact analysis.
Do not repeat repository-wide exploration.
Search targeted identifiers before reading entire files.
Modify only the files required for a complete fix.
Do not repeat completed workflow stages without a concrete reason.
Investigate unexpected dependencies individually.
Keep plans and final responses concise.

Correctness, data integrity, security, and the existing Supabase architecture take priority over minimizing file count or token usage.