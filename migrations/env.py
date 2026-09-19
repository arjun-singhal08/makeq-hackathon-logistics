from logging.config import fileConfig

from alembic import context
from flask import current_app


config = context.config
fileConfig(config.config_file_name)


def get_engine():
    return current_app.extensions["migrate"].db.engine


def get_engine_url():
    try:
        return get_engine().url.render_as_string(hide_password=False).replace("%", "%%")
    except AttributeError:
        return str(get_engine().url).replace("%", "%%")


config.set_main_option("sqlalchemy.url", get_engine_url())
target_metadata = current_app.extensions["migrate"].db.metadata


def run_migrations_offline():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    with get_engine().connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )

        from sqlalchemy import inspect
        from alembic.migration import MigrationContext
        from alembic.script import ScriptDirectory

        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if "events" in tables:
            mig_context = MigrationContext.configure(connection)
            if mig_context.get_current_revision() is None:
                script = ScriptDirectory.from_config(config)
                head_rev = script.get_current_head()
                with connection.begin():
                    mig_context.stamp(script, head_rev)
                return

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
