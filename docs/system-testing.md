# System testing

The application test suite uses an isolated PostgreSQL database and mocked
Supabase Storage. It must not point at the configured application database or
real Supabase project.

Set `ER_TEST_DATABASE_URL` to a dedicated database whose name includes `test`
(the database-model integrity tests require the loopback database
`gram_admin_er_test`). The application fixtures create unique PostgreSQL
schemas and drop only those schemas after each test. Do not use production
credentials or share the test database with a running application.

```bash
ER_TEST_DATABASE_URL=postgresql://user:password@127.0.0.1:5432/gram_admin_er_test \
  .venv/bin/python -m pytest -q
```

The test harness disables external HTTP requests and replaces the Supabase
client with an in-memory object store. Database and storage behavior can
therefore be exercised without creating persistent local business files or
contacting external services.
