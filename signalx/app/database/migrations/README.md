# Database Migrations

MVP v0.1 uses SQLAlchemy `Base.metadata.create_all` on startup (see
`app.database.session.init_db`). For production, run Alembic migrations:

```bash
pip install alembic
alembic init app/database/migrations
alembic revision --autogenerate -m "init"
alembic upgrade head
```

This directory is reserved for future Alembic versions.
